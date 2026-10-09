# SPDX-License-Identifier: MIT
"""Headless operator and job commands. Importing this module never imports Qt."""
import argparse
import json
import os
import sys
from pathlib import Path
from uuid import uuid4
from .errors import UpdateError
from .state_store import StateStore, StoreAudit
from .api_football_client import ApiFootballClient, utcnow
from .wiki_client import WikiClient, FixtureClient
from .player_registry import PlayerRegistry
from .club_registry import ClubRegistry
from .scheduler import CycleRunner, publish_plan, text_report
from .operator_review import deserialize_plan, queue_plan, approve_stored
from .publication_policy import PublicationPolicy
from .transactions import plan_fingerprint
from .json_loader import load_package
from .change_planner import plan_package
from .api_provider import ProviderStore


def make_api(store):
    if getattr(store, 'provider', 'api_football') == 'paused':
        raise UpdateError('provider_paused', 'Сбор данных приостановлен оператором')
    if getattr(store,'provider','api_football') not in {'api_football','highlightly'}:
        raise UpdateError('provider_invalid','Неизвестный источник статистики')
    if getattr(store, 'provider', 'api_football') == 'highlightly':
        from .highlightly_client import HighlightlyClient
        return HighlightlyClient(store)
    return ApiFootballClient(store)


def login_env(wiki):
    username=os.environ.get('WIKI_BOT_USERNAME')
    password=os.environ.get('WIKI_BOT_PASSWORD')
    if not username or not password:
        raise UpdateError('authorization_required','Нужны WIKI_BOT_USERNAME и WIKI_BOT_PASSWORD через Envvars')
    return wiki.login_botpassword(username,password)


def write_json(path,value):
    Path(path).parent.mkdir(parents=True,exist_ok=True)
    Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def parser():
    p=argparse.ArgumentParser(description='BrackelBot 0.4 — Toolforge и ручной контроль')
    p.add_argument('--state',help='Локальная SQLite (на Toolforge используйте BRACKELBOT_STORAGE=toolsdb)')
    p.add_argument('--provider', choices=['api_football','highlightly','paused'], default=os.environ.get('BRACKELBOT_API_PROVIDER','api_football'))
    sub=p.add_subparsers(dest='command',required=True)
    sub.add_parser('init-db');sub.add_parser('check-api');sub.add_parser('check-auth')
    sub.add_parser('api-quotas')
    search=sub.add_parser('search-player');search.add_argument('name')
    inspect=sub.add_parser('inspect-player');inspect.add_argument('api_id',type=int);inspect.add_argument('--season',type=int,default=2026)
    scope=sub.add_parser('check-league');scope.add_argument('--tier',type=int,choices=[1,2,3],default=1);scope.add_argument('--season',type=int,default=2026)
    table=sub.add_parser('check-klstat');table.add_argument('title');table.add_argument('--fixtures')
    kt=sub.add_parser('prepare-klstat');kt.add_argument('title');kt.add_argument('evidence');kt.add_argument('--fixtures')
    kt.add_argument('--package-out',default='klstat-package.json');kt.add_argument('--report',default='klstat-report.json')
    clubs=sub.add_parser('club-list');clubs.add_argument('--tier',type=int,choices=(1,2,3));clubs.add_argument('--season',type=int,default=2026)
    run=sub.add_parser('run');run.add_argument('--season',type=int,default=2026)
    modes=run.add_mutually_exclusive_group()
    for mode in ['dry-run','manual','test','automatic']: modes.add_argument('--'+mode,action='store_true')
    run.add_argument('--report-dir',default='reports');run.add_argument('--discover-candidates',action='store_true')
    imp=sub.add_parser('import-json');imp.add_argument('file');imp.add_argument('--fixtures')
    imp.add_argument('--report',default='manual-report.json')
    rp=sub.add_parser('review-list');rp.add_argument('--plans',action='store_true')
    show=sub.add_parser('review-show');show.add_argument('hash')
    approve=sub.add_parser('review-approve');approve.add_argument('hash');approve.add_argument('operation_id');approve.add_argument('--confirm',action='store_true',required=True)
    publish=sub.add_parser('publish');publish.add_argument('hash')
    m=publish.add_mutually_exclusive_group(required=True);m.add_argument('--test',action='store_true');m.add_argument('--manual',action='store_true')
    kill=sub.add_parser('kill-switch');kill.add_argument('value',choices=['on','off'])
    kill.add_argument('--confirm',action='store_true')
    approval=sub.add_parser('approval-install');approval.add_argument('file');approval.add_argument('--confirm',action='store_true',required=True)
    pc=sub.add_parser('player-confirm');pc.add_argument('api_id',type=int);pc.add_argument('--qid',required=True);pc.add_argument('--title',required=True);pc.add_argument('--wiki-name',required=True);pc.add_argument('--season',type=int,default=2026);pc.add_argument('--confirm',action='store_true',required=True)
    cc=sub.add_parser('club-confirm');cc.add_argument('api_id',type=int);cc.add_argument('--qid',required=True);cc.add_argument('--title',required=True);cc.add_argument('--season',type=int,default=2026);cc.add_argument('--historical',action='store_true');cc.add_argument('--confirm',action='store_true',required=True)
    anchor=sub.add_parser('anchor-confirm');anchor.add_argument('api_id',type=int);anchor.add_argument('file');anchor.add_argument('--confirm',action='store_true',required=True)
    attest=sub.add_parser('attest-manual');attest.add_argument('hash');attest.add_argument('--api-id',type=int,required=True);attest.add_argument('--season',type=int,default=2026);attest.add_argument('--confirm',action='store_true',required=True)
    export=sub.add_parser('export');export.add_argument('bucket',choices=['players','clubs','anchors','review','runs','plans','backups','edits','audit']);export.add_argument('file')
    restore=sub.add_parser('restore-test');restore.add_argument('file')
    backup=sub.add_parser('backup-export');backup.add_argument('key');backup.add_argument('file')
    return p


def main(argv=None):
    if hasattr(sys.stdout,'reconfigure'): sys.stdout.reconfigure(encoding='utf-8')
    args=parser().parse_args(argv)
    if args.command=='club-list':
        from .entity_resolver import EntityResolver, ResolvedEntity
        rows=[]
        for club in EntityResolver(None).catalogue:
            if args.tier and (club.get('league_tier')!=args.tier or club.get('season')!=args.season): continue
            rows.append({'qid':club['qid'],'name':club['name'],'title':club['title'],
                         'display_name':club.get('display_name',club['name']),'league_tier':club.get('league_tier'),
                         'season':club.get('season'),'team_variant':club.get('team_variant','primary'),
                         'wikitext':ResolvedEntity(club['title'],club['qid'],club['flag'],club.get('display_name',club['name']),'club',
                                                   foreign_language=club.get('foreign_language'),foreign_title=club.get('foreign_title')).wikitext})
        print(json.dumps(rows,ensure_ascii=False,indent=2));return 0
    store=None
    try:
        if args.provider == 'paused' and args.command in {'run','check-api','api-quotas','search-player','inspect-player','check-league','player-confirm','club-confirm'}:
            raise UpdateError('provider_paused', 'Сбор данных приостановлен оператором')
        store=StateStore(args.state) if args.state else StateStore.from_env()
        store=ProviderStore(store,args.provider)
        if args.command=='init-db':
            if store.get('policy','kill_switch') is None: store.put('policy','kill_switch',True)
            print('Хранилище готово. По умолчанию публикация выключена.');return 0
        if args.command=='check-api':
            with store.run_lock(): result=make_api(store).status()
            print(json.dumps(result,ensure_ascii=False,indent=2));return 0
        if args.command in {'api-quotas','search-player','inspect-player','check-league'}:
            if args.provider != 'highlightly':
                raise UpdateError('provider_invalid','Эта команда требует --provider highlightly')
            with store.run_lock():
                api=make_api(store)
                if args.command=='api-quotas': result=api.quota_status()
                elif args.command=='search-player': result=api.search_players(args.name)
                elif args.command=='check-league': result=api.scope(args.season,{1:67162,2:68013,3:68864}[args.tier])
                else:
                    result=api.player(args.api_id,args.season,fresh=False)
                    _,profile,_=api.profile(args.api_id)
                    result['transfers']=profile['transfers']
            print(json.dumps(result,ensure_ascii=False,indent=2));return 0
        if args.command=='check-auth':
            wiki=WikiClient();print('Авторизация: '+login_env(wiki));wiki.verify_authenticated();wiki.logout_local();return 0
        if args.command=='check-klstat':
            from .klstat_editor import check_table
            wiki=FixtureClient(args.fixtures) if args.fixtures else WikiClient()
            result=check_table(wiki.fetch_page(args.title).text)
            print(json.dumps(result,ensure_ascii=False,indent=2));return 1 if result['warnings'] else 0
        if args.command=='prepare-klstat':
            from .klstat_planner import prepare_table, validate_evidence
            from .entity_resolver import EntityResolver
            from .audit_logger import make_report
            from .json_loader import load_json
            evidence=load_json(args.evidence)
            validate_evidence(evidence)
            wiki=FixtureClient(args.fixtures) if args.fixtures else WikiClient()
            pkg,plan=prepare_table(wiki.fetch_page(args.title),evidence,EntityResolver(wiki))
            write_json(args.package_out,pkg)
            report=make_report(pkg,[plan],[])
            with store.run_lock():
                report['plan_hashes']=[queue_plan(store,plan,'klstat-'+uuid4().hex)]
                if args.fixtures:
                    item=store.get('plans',report['plan_hashes'][0]);item['offline']=True;store.put('plans',report['plan_hashes'][0],item)
            write_json(args.report,report);print(plan.diff)
            print(json.dumps(report['plan_hashes']))
            return 0 if all(c.status in {'ready','already_applied'} for c in plan.changes) else 1
        if args.command=='kill-switch':
            if args.value=='off' and not args.confirm: raise UpdateError('confirmation_required','Для выключения аварийной блокировки нужен --confirm')
            store.put('policy','kill_switch',args.value=='on');print('Аварийная блокировка: '+args.value);return 0
        if args.command=='approval-install':
            from jsonschema import Draft202012Validator, FormatChecker
            from importlib.resources import files
            value=json.loads(Path(args.file).read_text(encoding='utf-8'))
            schema=json.loads(files('wiki_stats').joinpath('approval.schema.json').read_text(encoding='utf-8'))
            if list(Draft202012Validator(schema,format_checker=FormatChecker()).iter_errors(value)):
                raise UpdateError('approval_invalid','Файл одобрения не соответствует approval.schema.json')
            if not value['community_approved'] or not value['operator_enabled']:
                raise UpdateError('approval_invalid','Образец выключен. Включайте только после реального одобрения задачи.')
            store.put('policy','community_approval',value);print('Установлено явное одобрение; аварийный выключатель остаётся отдельным.');return 0
        if args.command=='export':
            write_json(args.file,dict(store.items(args.bucket)));print(args.file);return 0
        if args.command=='backup-export':
            value=store.get('backups',args.key)
            if not value: raise UpdateError('invalid_backup','Нет такой резервной копии')
            write_json(args.file,value);print(args.file);return 0
        if args.command=='restore-test':
            from .backup import plan_restore
            with store.run_lock():
                plan=plan_restore(WikiClient(),args.file)
                print(queue_plan(store,plan,'restore-'+uuid4().hex))
            print('Diff подготовлен. Нужны review-show, review-approve и publish --test.');return 0
        if args.command=='review-list':
            bucket='plans' if args.plans else 'review'
            print(json.dumps(dict(store.items(bucket)),ensure_ascii=False,indent=2));return 0
        if args.command=='review-show':
            item=store.get('plans',args.hash)
            if not item: raise UpdateError('plan_not_found','Нет такого плана')
            plan=deserialize_plan(item['plan'])
            print(f"Страница: {plan.snapshot.title}; ревизия {plan.snapshot.revid}; статус {item['status']}\n{plan.diff}")
            for c in plan.changes: print(c.id,c.status,c.decision,'\n'+c.diff)
            store.put('review_seen',args.hash,{'hash':plan_fingerprint(plan),'viewed_at':utcnow()});return 0
        if args.command=='attest-manual':
            from .player_registry import check_player_identity
            from .transactions import digest
            with store.run_lock():
                item=store.get('plans',args.hash);mapping=PlayerRegistry(store).get(args.api_id)
                if not item or item.get('offline') or not mapping or item['status']!='pending':
                    raise UpdateError('source_unverified','Нужны живой ожидающий план и подтверждённая связь игрока')
                plan=deserialize_plan(item['plan'])
                if plan.snapshot.title!=mapping['title'] or plan.article.get('player')!=mapping['wiki_name']:
                    raise UpdateError('identity_mismatch','Ручной пакет относится к другой статье или карточке')
                if plan.warnings or any(c.status not in {'ready','already_applied'} for c in plan.changes):
                    raise UpdateError('needs_review','Сначала устраните ошибки редактора')
                check_player_identity(WikiClient(),mapping)
                plan.publishable=bool(plan.diff)
                store.put('plans',args.hash,{**item,'plan':__import__('dataclasses').asdict(plan)})
                store.put('plan_evidence',args.hash,{'manual':True,'plan_hash':args.hash,'league':78,'season':args.season,
                          'provider':args.provider,
                          'fetched_at':utcnow(),'player_id':args.api_id,'mapping_hash':digest(mapping)})
                StoreAudit(store,'operator').log('sources_attested',plan_hash=args.hash,api_id=args.api_id)
            print('Ручные источники подтверждены оператором; по-прежнему требуются diff и отдельное утверждение каждой операции.');return 0
        if args.command=='review-approve':
            with store.run_lock():
                if store.get('review_seen',args.hash,{}).get('hash')!=args.hash:
                    raise UpdateError('diff_required','Сначала выполните review-show для этого плана')
                approve_stored(store,args.hash,args.operation_id)
                StoreAudit(store,'operator').log('operation_approved',plan_hash=args.hash,operation_id=args.operation_id)
            print('Операция утверждена. Публикация выполняется отдельной командой.');return 0
        if args.command=='publish':
            with store.run_lock():
                policy=PublicationPolicy(store,'test' if args.test else 'manual')
                item=store.get('plans',args.hash)
                if not item: raise UpdateError('plan_not_found','Нет такого плана')
                policy.gate_plan(deserialize_plan(item['plan']))
                wiki=WikiClient(publication_policy=policy);login_env(wiki)
                try: result=publish_plan(store,args.hash,wiki,policy)
                finally: wiki.logout_local()
                print(json.dumps(result,ensure_ascii=False,indent=2));return 0
        if args.command=='import-json':
            package=load_package(args.file)
            wiki=FixtureClient(args.fixtures) if args.fixtures else WikiClient()
            plans,failures=plan_package(package,wiki)
            from .audit_logger import make_report
            report=make_report(package,plans,failures)
            with store.run_lock():
                report['plan_hashes']=[queue_plan(store,p,'manual-'+uuid4().hex) for p in plans]
                if args.fixtures:
                    for h in report['plan_hashes']:
                        item=store.get('plans',h);item['offline']=True;store.put('plans',h,item)
            write_json(args.report,report);print(json.dumps(report['plan_hashes']));return 1 if failures else 0
        if args.command in {'player-confirm','club-confirm','anchor-confirm'}:
            with store.run_lock():
                wiki=WikiClient()
                if args.command=='anchor-confirm':
                    anchor=json.loads(Path(args.file).read_text(encoding='utf-8'))
                    result=PlayerRegistry(store).confirm_anchor(args.api_id,anchor,wiki)
                elif args.command=='player-confirm':
                    if args.provider == 'highlightly':
                        player,_,_=make_api(store).profile(args.api_id, fresh=True)
                        players={args.api_id:player}
                    else:
                        dataset=store.get('datasets',str(args.season))
                        if not dataset: raise UpdateError('dataset_missing','Сначала выполните run --dry-run')
                        from .statistics_engine import normalize_dataset
                        players,_=normalize_dataset(dataset)
                    if args.api_id not in players: raise UpdateError('identity_unmapped','ID отсутствует в полном сборе выбранного сезона')
                    result=PlayerRegistry(store).confirm(players[args.api_id],args.qid,args.title,args.wiki_name,wiki)
                else:
                    if args.historical or args.provider == 'highlightly':
                        from .source_validation import response_rows
                        rows=response_rows(make_api(store).team(args.api_id))
                        teams=[r.get('team',{}) for r in rows]
                        if (len(teams)!=1 or teams[0].get('id')!=args.api_id or teams[0].get('national') is not False):
                            raise UpdateError('club_unmapped','API не подтвердил единственный исторический клуб с указанным ID')
                    else:
                        scope=store.get('scope',str(args.season),{})
                        teams=[t for t in scope.get('teams',[]) if t['id']==args.api_id]
                    if len(teams)!=1: raise UpdateError('club_unmapped','ID отсутствует в составе клубов выбранного сезона')
                    result=ClubRegistry(store).confirm(teams[0],args.qid,args.title,wiki)
                StoreAudit(store,'operator').log('registry_confirmed',kind=args.command,api_id=args.api_id)
                print(json.dumps(result,ensure_ascii=False,indent=2));return 0
        if args.command=='run':
            mode='automatic' if args.automatic else ('manual' if args.manual else ('test' if args.test else 'dry_run'))
            wiki=WikiClient()
            # Automatic preflight cannot send writes while approval is absent;
            # collection can still run without credentials when disabled.
            if mode=='automatic' and os.environ.get('BRACKELBOT_PUBLICATION')=='1' and not store.get('policy','kill_switch',True):
                login_env(wiki)
            try:
                runner=CycleRunner
                if args.provider == 'highlightly':
                    from .highlightly_scheduler import HighlightlyCycleRunner
                    runner=HighlightlyCycleRunner
                report=runner(store,make_api(store),wiki,season=args.season,mode=mode,discover=args.discover_candidates).run()
            finally: wiki.logout_local()
            folder=Path(args.report_dir)
            write_json(folder/(report['run_id']+'.json'),report)
            (folder/(report['run_id']+'.txt')).write_text(text_report(report),encoding='utf-8')
            print(text_report(report));return 0 if report['status']=='completed' else 1
    except (UpdateError,OSError,ValueError) as exc:
        # Storage error text may contain credentials. Only domain errors are rendered.
        print(f'Ошибка: {exc}' if isinstance(exc,UpdateError) else 'Ошибка файла или конфигурации.',file=sys.stderr)
        return 2
    except Exception:
        print('Ошибка хранилища или зависимости. Выполнение остановлено; диагностические секреты не выводятся.',file=sys.stderr)
        return 2
    finally:
        if store: store.close()


if __name__=='__main__': raise SystemExit(main())
