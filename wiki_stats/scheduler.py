# SPDX-License-Identifier: MIT
"""One daily job entry point; Toolforge Jobs Framework owns the schedule."""
from dataclasses import asdict
from datetime import datetime, timezone, date
from uuid import uuid4
import mwparserfromhell as mw
from .errors import UpdateError
from .api_football_client import utcnow
from .player_registry import PlayerRegistry, check_player_identity
from .club_registry import ClubRegistry
from .statistics_engine import normalize_dataset, update_operation
from .wikitext_parser import WikitextParser
from .change_planner import plan_article
from .schema_validator import validate_package
from .transactions import digest, approve_change
from .state_store import StoreAudit
from .operator_review import queue_plan, deserialize_plan, serialize_plan
from .publication_policy import PublicationPolicy, ServerPublisher
from .models import Mode


def review(store, kind, identifier, code, details=None):
    key=digest([kind,identifier,code])
    store.put('review',key,{'kind':kind,'identifier':identifier,'status':'needs_review',
                          'reason':code,'details':details or {},'updated_at':utcnow()})


class CycleRunner:
    def __init__(self, store, api, wiki, *, season=2026, mode='dry_run', discover=False):
        self.store,self.api,self.wiki=store,api,wiki
        self.season,self.mode,self.discover=season,mode,discover

    def _scope(self):
        league=self.api.leagues(self.season)['data']['response']
        matching=[s for l in league if l.get('league',{}).get('id')==78
                  for s in l.get('seasons',[]) if s.get('year')==self.season]
        if len(matching)!=1 or matching[0].get('coverage',{}).get('players') is not True:
            raise UpdateError('api_coverage_missing','API не подтверждает покрытие игроков выбранного сезона')
        teams=self.api.teams(self.season)['data']['response']
        clubs={}
        for row in teams:
            team=row.get('team',{})
            tid=team.get('id')
            if type(tid) is not int or tid<=0 or tid in clubs or team.get('national') is not False or not team.get('name'):
                raise UpdateError('api_invalid_response','Неверный состав клубов чемпионата')
            clubs[tid]=team
        if not clubs: raise UpdateError('api_incomplete','Пустой список команд не является составом лиги')
        return clubs

    def _rosters(self, teams):
        membership={}
        for tid in teams:
            data=self.api.squads(tid)['data']
            rows=data['response']
            if len(rows)!=1 or rows[0].get('team',{}).get('id')!=tid or not isinstance(rows[0].get('players'),list) or not rows[0]['players']:
                raise UpdateError('api_incomplete','Не получен полный текущий состав клуба')
            for player in rows[0]['players']:
                pid=player.get('id')
                if type(pid) is not int or pid<=0: raise UpdateError('api_invalid_response','Неверный ID в составе клуба')
                membership.setdefault(pid,set()).add(tid)
        return membership

    def _departures(self, teams, membership, registry, report):
        for key,mapping in self.store.items('players'):
            pid=int(key)
            if pid in membership or mapping['monitoring']!='active': continue
            try:
                rows=self.api.transfers(pid)['data']['response']
                history=[t for row in rows if row.get('player',{}).get('id')==pid for t in row.get('transfers',[])]
                history=[t for t in history if isinstance(t.get('date'),str) and t['date']<=date.today().isoformat()]
                if not history:
                    review(self.store,'monitoring',pid,'absence_unconfirmed');report['review_count']+=1;continue
                latest=max(t['date'] for t in history)
                recent=[t for t in history if t['date']==latest]
                if len(recent)!=1: raise UpdateError('needs_review','Неоднозначные трансферы в один день')
                transfer=recent[0]
                target=transfer.get('teams',{}).get('in',{}).get('id')
                origin=transfer.get('teams',{}).get('out',{}).get('id')
                if type(target) is not int or target<=0 or target in teams or origin not in mapping['club_ids']:
                    review(self.store,'monitoring',pid,'absence_unconfirmed');report['review_count']+=1;continue
                # Future/old transfers do not explain the current absence. Operator
                # handles the structural career change; monitoring can pause.
                if latest < mapping['verified_at'][:10]:
                    review(self.store,'monitoring',pid,'old_transfer');report['review_count']+=1;continue
                mapping={**mapping,'monitoring':'departed','departure':transfer,'departure_confirmed_at':utcnow()}
                self.store.put('players',pid,mapping)
                review(self.store,'transfer',pid,'confirmed_departure_manual',transfer)
                report['departures']+=1;report['review_count']+=1
            except (UpdateError,TypeError,KeyError) as exc:
                review(self.store,'monitoring',pid,getattr(exc,'code','invalid_transfer'))
                report['review_count']+=1
                if getattr(exc,'code','') in {'api_quota_exhausted','api_rate_limited','api_authentication'}: raise

    def run(self):
        with self.store.run_lock():
            run_id=uuid4().hex
            log=StoreAudit(self.store,run_id)
            report={'run_id':run_id,'started_at':utcnow(),'mode':self.mode,'league':78,'season':self.season,
                    'api_available':False,'status':'running','player_count':0,'article_count':0,
                    'discrepancies':0,'prepared_count':0,'published_count':0,'error_count':0,
                    'review_count':0,'departures':0,'plans':[],'errors':[]}
            self.store.put('runs',run_id,report)
            try:
                status=self.api.status();report['api_available']=True;report['account']=status
                teams=self._scope()
                dataset=self.api.players(self.season)
                players,observations=normalize_dataset(dataset)
                membership=self._rosters(teams)
                # No registry/absence conclusions before all pages AND rosters.
                self.store.put('scope',str(self.season),{'league':78,'teams':list(teams.values()),'fetched_at':utcnow()})
                report['player_count']=len(players)
                registry=PlayerRegistry(self.store)
                for pid,player in players.items():
                    if not registry.get(pid):
                        review(self.store,'identity',pid,'identity_unmapped',{'player':player})
                        report['review_count']+=1
                        if self.discover: registry.candidates(player,self.wiki)
                for tid,team in teams.items():
                    if not ClubRegistry(self.store).get(tid):
                        review(self.store,'club',tid,'club_unmapped',team)
                        report['review_count']+=1
                self._departures(teams,membership,registry,report)
                for observation in observations:
                    self._process(observation,players,teams,membership,registry,report,log)
                if self.mode=='automatic' and not report.get('publication_blocked'):
                    self._publish_automatic(report,log)
                report['status']='blocked' if report.get('publication_blocked') else 'completed'
            except UpdateError as exc:
                report['status']='stopped';report['error_count']+=1
                report['errors'].append({'status':exc.code,'message':str(exc)})
                log.log('cycle_stopped',status=exc.code)
            except Exception:
                report['status']='failed';report['error_count']+=1
                report['errors'].append({'status':'internal_error','message':'Непредвиденная ошибка. Публикация остановлена; подробности не выводятся, чтобы не раскрыть секреты.'})
                log.log('cycle_stopped',status='internal_error')
            finally:
                report['finished_at']=utcnow();report['requests']=self.api.used
                self.store.put('runs',run_id,report)
                log.log('cycle_finished',status=report['status'],published=report['published_count'])
            return report

    def _process(self,obs,players,teams,membership,registry,report,log):
        pid,tid=obs['api_id'],obs['team_id']
        mapping=registry.get(pid)
        if not mapping: return
        try:
            if tid not in teams or mapping['monitoring']!='active' or membership.get(pid)!= {tid}:
                raise UpdateError('needs_review','Текущий состав не подтверждает единственный клуб; переход требует проверки')
            if mapping['birth_date']!=players[pid].get('birth',{}).get('date'):
                raise UpdateError('identity_mismatch','Дата рождения API изменилась')
            check_player_identity(self.wiki,mapping)
            club,resolved=ClubRegistry(self.store).verify(tid,self.wiki)
            anchor_key=f'{pid}:{tid}:{self.season}'
            anchor=self.store.get('anchors',anchor_key)
            if not anchor: raise UpdateError('needs_review','Нет подтверждённой карьерной базы для периода и сезона')
            if not anchor['period'].endswith('{{н.в.}}'):
                raise UpdateError('needs_review','Закрытый исторический период нельзя обновлять дельтой текущего сезона')
            anchor_code=mw.parse(anchor['career_wikitext'])
            if (any(not str(t.name).strip().casefold().startswith('флаг ') for t in anchor_code.filter_templates())
                or len(anchor_code.filter_wikilinks())!=1):
                raise UpdateError('needs_review','Аренда, резерв или сложное оформление карьеры требуют ручного подтверждения')
            # A return in the same season must not reuse an old stint anchor.
            transfer_rows=self.api.transfers(pid)['data']['response']
            transfer_history=[t for row in transfer_rows if row.get('player',{}).get('id')==pid for t in row.get('transfers',[])]
            if any(t.get('date','')>anchor['as_of'] and t.get('date','')<=obs['fetched_at'][:10] for t in transfer_history):
                raise UpdateError('needs_review','После базы зарегистрирован трансфер; нужна новая база периода')
            snapshot=self.wiki.fetch_page(mapping['title'])
            if snapshot.title!=mapping['title'] or snapshot.namespace!=0:
                raise UpdateError('identity_mismatch','Получена другая статья или пространство, чем в проверенной связи игрока')
            found=report.setdefault('found_articles',[])
            if snapshot.title not in found: found.append(snapshot.title)
            report['article_count']=len(found)
            parser=WikitextParser(snapshot.text)
            box=parser.infobox(mapping['wiki_name'])
            current=box.get('нынешний клуб')
            current_links={str(x.title).strip() for x in mw.parse(current.text if current else '').filter_wikilinks()}
            if resolved.title not in current_links:
                raise UpdateError('needs_review','Текущий клуб в карточке не соответствует текущему составу API')
            matches=[v for p,c,v in parser.career('клубы',mapping['wiki_name']) if p==anchor['period'] and c==anchor['career_wikitext']]
            links={str(x.title).strip() for x in mw.parse(anchor['career_wikitext']).filter_wikilinks()}
            if len(matches)!=1 or resolved.title not in links:
                raise UpdateError('club_mismatch','Клуб и период базы не совпадают с единственной строкой карточки')
            nums=matches[0].numeric(pair=True);old={'appearances':nums[0].value,'goals':nums[1].value}
            operation=update_operation(mapping,anchor,obs,old)
            operations=[]
            if old!=operation['new']: operations.append(operation)
            # The existing editor recomputes totals only if all categories are
            # known. API Bundesliga stats update just the championship column.
            if any(t.name=='клстат' for t in parser.templates):
                table=parser.club_table()
                seasons=[r for r in table.seasons if r.season==f'{self.season}/{str(self.season+1)[-2:]}'
                         and resolved.title in {str(x.title).strip() for x in mw.parse(r.club).filter_wikilinks()}]
                if len(seasons)!=1:
                    review(self.store,'season',f'{pid}:{tid}','new_season_needs_complete_categories',{'observation':obs})
                    report['review_count']+=1
                else:
                    categories=[c for c in table.categories if c=='Чемпионат']
                    if len(categories)!=1: raise UpdateError('unsupported_structure','Категория чемпионата не определена однозначно')
                    index=table.categories.index(categories[0]);row=seasons[0]
                    previous={'appearances':row.values[str(2+2*index)].numeric()[0].value,
                              'goals':row.values[str(3+2*index)].numeric()[0].value}
                    if previous!=obs['stats']:
                        if any(obs['stats'][k]<previous[k] for k in previous):
                            raise UpdateError('needs_review','Сезонная таблица содержит больше API; проверьте историческое исправление')
                        table_op={**operation,'id':operation['id']+'-season','season':row.season,
                                  'entity':{'name':obs['team_name'],'wikitext':row.club},
                                  'target':{'structure':'club_table'},'competition':{'name':'Чемпионат','category':'Чемпионат','scope':'category'},
                                  'expected':previous,'new':obs['stats']}
                        operations.append(table_op)
            if not operations: return
            for op in operations: op['group_id']=operation['id']+'-atomic'
            report['discrepancies']+=1
            article={'title':snapshot.title,'player':mapping['wiki_name'],'subject_scope':'bundesliga',
                     'base_revid':snapshot.revid,'operations':operations}
            package={'schema_version':'1.1','package_id':'api-'+report['run_id'],'generated_at':utcnow(),'articles':[article]}
            validate_package(package)
            plan=plan_article(snapshot,article,'1.1')
            for c in plan.changes: c.source_verification='api_football_with_operator_verified_anchor'
            if plan.warnings or any(c.status not in {'ready','already_applied'} for c in plan.changes):
                raise UpdateError('unsupported_structure','План не прошёл проверки редактора или арифметики')
            plan.publishable=bool(plan.diff)
            plan_hash=queue_plan(self.store,plan,report['run_id'])
            self.store.put('plan_evidence',plan_hash,{'plan_hash':plan_hash,'league':78,'season':self.season,
                'fetched_at':obs['fetched_at'],'player_id':pid,'team_id':tid,'anchor_key':anchor_key,
                'mapping_hash':digest(mapping),'anchor_hash':digest(anchor),'observation':obs})
            self.store.put('packages',plan_hash,package)
            report['prepared_count']+=1
            report['plans'].append({'hash':plan_hash,'title':snapshot.title,'diff':plan.diff})
            log.log('plan_prepared',title=snapshot.title,plan_hash=plan_hash)
        except UpdateError as exc:
            if exc.code in {'unsupported_structure','totals_mismatch','integrity_error','patch_conflict'}:
                report['publication_blocked']=True
            if exc.code not in {'needs_review','club_unmapped','identity_unmapped'}:
                report['error_count']+=1
                report['errors'].append({'status':exc.code,'message':str(exc),'api_id':pid,'team_id':tid})
            review(self.store,'statistics',f'{pid}:{tid}',exc.code,{'observation':obs})
            report['review_count']+=1
            log.log('operation_requires_review',api_id=pid,team_id=tid,status=exc.code)
            if exc.code in {'api_quota_exhausted','api_rate_limited','api_authentication','authorization_error','publication_unknown'}: raise

    def _publish_automatic(self,report,log):
        policy=PublicationPolicy(self.store,'automatic')
        # All structural validation finishes before the first publication.
        for entry in report['plans']:
            item=self.store.get('plans',entry['hash'])
            if item['status']!='pending': continue
            plan=deserialize_plan(item['plan'])
            try:
                policy.gate_plan(plan)
                for c in plan.changes:
                    if c.status=='ready': approve_change(plan,c.id)
                publish_plan(self.store,entry['hash'],self.wiki,policy,log,plan=plan,source_api=self.api)
                report['published_count']+=1
            except UpdateError as exc:
                review(self.store,'publication',entry['hash'],exc.code)
                report['review_count']+=1
                if exc.code in {'authorization_error','authorization_required','publication_unknown','publishing_disabled','source_stale','recheck_failed','unsupported_structure','integrity_error','totals_mismatch'}: raise


def publish_plan(store,plan_hash,wiki,policy,logger=None,plan=None,source_api=None):
    item=store.get('plans',plan_hash)
    if not item or item['status']!='pending': raise UpdateError('approval_invalid','Нет ожидающего плана')
    if item.get('offline'): raise UpdateError('offline_publication','План из локальных копий нельзя публиковать; выполните живую проверку')
    plan=plan or deserialize_plan(item['plan'])
    policy.gate_plan(plan)
    if plan.snapshot.title!='Участник:Zambrowski/testbot':
        evidence=store.get('plan_evidence',plan_hash)
        check_player_identity(wiki,store.get('players',evidence['player_id']))
        if not evidence.get('manual'):
            ClubRegistry(store).verify(evidence['team_id'],wiki)
            if source_api is None:
                from .api_football_client import ApiFootballClient
                source_api=ApiFootballClient(store)
            live_players,observations=normalize_dataset(source_api.player(evidence['player_id'],evidence['season']))
            current=[o for o in observations if o['api_id']==evidence['player_id'] and o['team_id']==evidence['team_id']]
            roster=source_api.squads(evidence['team_id'],fresh=True)['data']['response']
            members=[p.get('id') for r in roster if r.get('team',{}).get('id')==evidence['team_id'] for p in r.get('players',[])]
            history=source_api.transfers(evidence['player_id'],fresh=True)['data']['response']
            transfers=[t for r in history if r.get('player',{}).get('id')==evidence['player_id'] for t in r.get('transfers',[])]
            anchor=store.get('anchors',evidence['anchor_key'])
            mapping=store.get('players',evidence['player_id'])
            live_player=live_players.get(evidence['player_id'],{})
            if (len(current)!=1 or current[0]['stats']!=evidence['observation']['stats'] or evidence['player_id'] not in members
                or live_player.get('birth',{}).get('date')!=mapping['birth_date']
                or any(anchor['as_of']<t.get('date','')<=current[0]['fetched_at'][:10] for t in transfers)):
                from .transactions import clear_approvals
                clear_approvals(plan);plan.publishable=False
                store.put('plans',plan_hash,{**item,'status':'stale','plan':serialize_plan(plan),'last_error':'source_changed'})
                raise UpdateError('source_changed','API изменился после diff; требуется новый план')
    wiki.publication_policy=policy
    try:
        result=ServerPublisher(policy,logger or StoreAudit(store,item['run_id'])).publish(plan,wiki,mode=Mode.MANUAL)
    except UpdateError as exc:
        store.put('plans',plan_hash,{**item,'status':'stale' if exc.code in {'revision_conflict','publication_unknown','recheck_failed','plan_modified'} else 'pending',
                                    'plan':serialize_plan(plan),'last_error':exc.code})
        raise
    store.put('plans',plan_hash,{**item,'status':'published','plan':serialize_plan(plan),'result':result})
    store.put('edits',plan_hash,result)
    evidence=store.get('plan_evidence',plan_hash)
    if evidence and not evidence.get('manual'):
        mapping=store.get('players',evidence['player_id'])
        mapping['last_confirmed_statistics']=evidence['observation']
        # Changing runtime counters must not change verified identity evidence.
        store.put('players',evidence['player_id'],mapping)
    return result


def text_report(report):
    return ('BrackelBot: '+report['run_id']+'\nРежим: '+report['mode']+'; статус: '+report['status']+'\n'
            + '\n'.join(f'{k}: {report[k]}' for k in ['player_count','article_count','discrepancies','prepared_count','published_count','error_count','review_count'])
            + '\n'+'\n'.join(f"{x['status']}: {x['message']}" for x in report['errors'])+'\n')
