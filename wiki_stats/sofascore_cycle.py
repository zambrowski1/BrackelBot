# SPDX-License-Identifier: MIT
"""Collect a verified player's evidence and prepare one reviewable article diff.

Only explicitly supported competition scopes are reconciled. Missing rows do
not mean zero, and aggregate statistics for multiple clubs are never allocated.
"""
from datetime import datetime, timezone, date
from copy import deepcopy
import re

from .test_pages import is_test_page, bound_test_player
from .errors import UpdateError
from .models import ArticlePlan
from .wikitext_parser import WikitextParser
from .entity_resolver import reference_identity
from .player_registry import check_player_identity
from .structural_editor import is_active_period, validate_period
from .change_planner import plan_article
from .schema_validator import validate_package
from .transactions import TEST_TITLE, plan_fingerprint
from .sofascore_limits import DEFAULT_STATISTICS, DEFAULT_MATCH_PAGES, DEFAULT_MATCH_REQUESTS

# The regularSeason query keeps relegation playoffs out of infobox totals.
GERMAN_LEAGUES = {35, 44, 491, 42, 493, 1079, 1085}
LEAGUES = GERMAN_LEAGUES | {38,17,23,34,8,37,52,172,242}
CUPS = {217,19,328,96,329}
LEAGUE_CUPS = {21}
EUROPEAN_CUPS = {7, 679, 17015}
SUPER_CUPS = {799}
LEAGUE_LINKS = {35: '[[Чемпионат Германии по футболу|Бундеслига]]',
                44: '[[Вторая Бундеслига]]',
                491: '[[Третья лига Германии по футболу|Третья лига]]',
                42: '[[Региональная лига «Север»]]',
                493: '[[Региональная лига «Запад»]]',
                1079: '[[Региональная лига «Юго-Запад»]]',
                1085: '[[Региональная лига «Бавария»]]',
                38: '[[Чемпионат Бельгии по футболу|Чемпионат Бельгии]]',
                17:'[[Премьер-лига (Англия)|Премьер-лига]]',
                23:'[[Чемпионат Италии по футболу (Серия A)|Серия A]]',
                34:'[[Чемпионат Франции по футболу|Лига 1]]',
                8:'[[Чемпионат Испании по футболу|Ла Лига]]',
                37:'[[Чемпионат Нидерландов по футболу|Эредивизи]]',
                52:'[[Чемпионат Турции по футболу|Суперлига]]',
                172:'[[Чемпионат Чехии по футболу|Первая лига]]',
                242:'[[MLS]]'}


def season_name(value):
    if isinstance(value,str) and re.fullmatch(r'20\d{2}',value):return value
    if not isinstance(value, str) or not re.fullmatch(r'\d{2}/\d{2}|20\d{2}/\d{2}', value):
        raise UpdateError('season_invalid', 'Неоднозначный сезон Sofascore')
    start = int(value.split('/')[0])
    start = 2000+start if start < 100 else start
    if int(value.split('/')[1]) != (start+1) % 100:
        raise UpdateError('season_invalid', 'Непоследовательные годы сезона')
    return f'{start}/{(start+1)%100:02}'


def table_season_name(value):
    from .season_labels import normalize_table_season
    return normalize_table_season(value)


def european_history_covered(history, records, season, now):
    """The category includes every European competition, including qualifiers."""
    if history.get('complete') is not True:
        return False
    teams={r['team_id'] for r in records}
    scopes={(r['team_id'],r['tournament_id']) for r in records}
    season_scopes={(r['team_id'],r['tournament_id'],r['season_id']) for r in records}
    year=int(season[:4])
    for row in history.get('matches',[]):
        player=row.get('player') or {};tournament=row.get('tournament') or {}
        if player.get('team_id') not in teams:continue
        if tournament.get('id') not in EUROPEAN_CUPS and 'conference league' not in str(tournament.get('name','')).casefold():continue
        if row.get('is_finished') is not True or (row.get('status') or {}).get('type')!='finished':continue
        try:label=season_name((row.get('season') or {}).get('year'))
        except UpdateError:label=None
        if label is None and (player['team_id'],tournament.get('id'),(row.get('season') or {}).get('id')) in season_scopes:
            label=season
        if label is not None and label!=season:continue
        try:day=datetime.fromisoformat(row['start_time'].replace('Z','+00:00'))
        except (KeyError,TypeError,ValueError):return False
        if day.tzinfo is None:return False
        if day>now:continue
        if label is None:
            if date(year,6,1)<=day.date()<date(year+1,7,1):return False
            continue
        minutes=(player.get('statistics') or {}).get('minutes_played')
        if minutes==0 and type(minutes) in (int,float):continue
        if (player['team_id'],tournament.get('id')) not in scopes:return False
    return True


def absent_category_zero(history, league, category, now):
    """Prove a zero in a closed season from a complete, anchored match log."""
    if history.get('complete') is not True or category not in {'Национальный кубок','Еврокубки','Стыковые матчи','Прочие'}:
        return None
    # Domestic cup/supercup IDs and relegation rules below cover Germany.
    # A foreign league cannot prove their absence using that whitelist.
    if league['tournament_id'] not in GERMAN_LEAGUES and category != 'Еврокубки':
        return None
    ids = [row.get('id') for row in history.get('matches', [])]
    if any(type(mid) is not int or mid <= 0 for mid in ids) or len(ids) != len(set(ids)):
        return None
    year=int(league['season'][:4]);start=date(year,7,1);end=date(year+1,7,1)
    proven_scope = league.get('match_reconstructed') is True and bool(league.get('confirmed_match_ids'))
    if now.date()<end or league['numbers']['appearances']<0 or league['numbers']['appearances']==0 and not proven_scope:
        return None
    finished=[]
    for row in history.get('matches',[]):
        if row.get('is_finished') is not True or (row.get('status') or {}).get('type')!='finished':continue
        try:
            day=datetime.fromisoformat(row['start_time'].replace('Z','+00:00'))
        except (ValueError,KeyError,TypeError):return None
        if day.tzinfo is None or day>now:continue
        finished.append((row,day.date()))
    if not finished or min(d for _,d in finished)>start or max(d for _,d in finished)<end:
        return None
    anchored=[]
    for row,day in finished:
        p=row.get('player') or {};t=row.get('tournament') or {};s=row.get('season') or {}
        if p.get('team_id')!=league['team_id']:continue
        if sum((row.get(k) or {}).get('id')==league['team_id'] for k in ('home_team','away_team'))!=1:
            return None
        if (t.get('id'),s.get('id'))==(league['tournament_id'],league['season_id']):
            minutes=(p.get('statistics') or {}).get('minutes_played')
            # The history includes unused substitutes without a statistics
            # object. They cannot add appearances if the explicitly played
            # rows already equal the verified total for this club and league.
            # The final exact count check still rejects missing played rows.
            if minutes is not None:
                if type(minutes) not in (int,float) or not 0<=minutes<=130:return None
                if minutes>0:anchored.append(row)
        try:season=season_name(s.get('year'))
        except UpdateError:season=None
        if season!=league['season'] and not (season is None and start<=day<end):continue
        name=str(t.get('name','')).casefold();round_=row.get('round') or {}
        playoff='relegation' in name or any('play' in str(round_.get(k,'')).casefold() for k in ('name','slug'))
        if category=='Национальный кубок':possible=t.get('id') in CUPS
        elif category=='Еврокубки':possible=t.get('id') in EUROPEAN_CUPS or 'conference league' in name
        else:
            possible=playoff or t.get('id') in LEAGUES and bool(round_.get('cup_round_type'))
            if category=='Прочие':possible=possible or t.get('id') in SUPER_CUPS
        if possible:
            minutes=(p.get('statistics') or {}).get('minutes_played')
            if type(minutes) not in (int,float) or minutes != 0:return None
    if len(anchored)!=league['numbers']['appearances'] and not proven_scope:return None
    if proven_scope:
        candidates = {r['id'] for r, _ in finished if
            ((r.get('player') or {}).get('team_id'), (r.get('tournament') or {}).get('id'),
             (r.get('season') or {}).get('id')) == (league['team_id'],league['tournament_id'],league['season_id'])
            and not ((r.get('round') or {}).get('cup_round_type') or any('play' in str((r.get('round') or {}).get(k,'' )).lower() for k in ('name','slug')))}
        confirmed=league['confirmed_match_ids']
        if (any(type(mid) is not int for mid in confirmed) or len(confirmed)!=len(set(confirmed))
                or set(confirmed)!=candidates or league['numbers']['appearances']>len(confirmed)):return None
    return {'appearances':0,'goals':0}


def confirmed_match_date(history, scopes, now=None):
    """Return a UTC date only for a complete, unambiguous match history.

    Scopes are (team, tournament, season); cup matches and friendlies cannot
    advance league-only infobox dates. Explicit playoff rounds are excluded.
    """
    if not history.get('complete'):
        return None
    now = now or datetime.now(timezone.utc)
    dates = []
    for row in history['matches']:
        player = (row.get('player', {}) or {})
        scope = (player.get('team_id'), (row.get('tournament', {}) or {}).get('id'), (row.get('season', {}) or {}).get('id'))
        if scope not in scopes:
            continue
        round_ = row.get('round', {}) or {}
        from .sofascore_match_stats import excluded_league_round
        if excluded_league_round(scope[1], round_):
            continue
        if row.get('is_finished') is not True or (row.get('status', {}) or {}).get('type') != 'finished':
            continue
        minutes = (player.get('statistics', {}) or {}).get('minutes_played')
        if type(minutes) not in (int, float) or not 0 < minutes <= 130:
            continue
        teams = [(row.get('home_team', {}) or {}), (row.get('away_team', {}) or {})]
        if (sum(t.get('id') == scope[0] for t in teams) != 1
                or any(t.get('sport', {}).get('slug') != 'football' for t in teams)):
            continue
        try:
            timestamp = datetime.fromisoformat(row['start_time'].replace('Z', '+00:00'))
            if timestamp.tzinfo is None or timestamp > now:
                continue
        except (KeyError, ValueError, TypeError):
            continue
        dates.append(timestamp.astimezone(timezone.utc).date())
    return max(dates).isoformat() if dates else None


def _numbers(response, pid, tid, sid, part, *, goalkeeper=False):
    if (response.get('player_id'), response.get('tournament_id'), response.get('season_id'), response.get('type')) != (pid, tid, sid, part):
        raise UpdateError('source_scope_mismatch', 'Статистика относится к другому игроку, сезону или этапу')
    team = response.get('team', {})
    if (type(team.get('id')) is not int or team.get('is_national') is not False
            or team.get('sport', {}).get('slug') != 'football'):
        raise UpdateError('club_ambiguous', 'Статистика не относится к единственному футбольному клубу')
    stats = response.get('statistics', {})
    values = {k: stats.get(k) for k in ('appearances', 'goals')}
    if goalkeeper:
        conceded = stats.get('goals_conceded')
        if type(conceded) is not int or not 0 <= conceded <= 10000:
            raise UpdateError('incomplete_statistics', 'Sofascore не подтвердил пропущенные голы вратаря')
        values['goals'] = -conceded
    if type(values['appearances']) is not int or not 0 <= values['appearances'] <= 10000 or type(values['goals']) is not int or not (-10000 if goalkeeper else 0) <= values['goals'] <= 10000:
        raise UpdateError('incomplete_statistics', 'Не подтверждены игры и голы')
    return team, values


def prepare_player(snapshot, mapping, api, wiki, resolver, *, max_requests=DEFAULT_STATISTICS, max_match_pages=DEFAULT_MATCH_PAGES, max_match_requests=DEFAULT_MATCH_REQUESTS, statistics_since=None, now=None):
    if statistics_since is not None and (type(statistics_since) is not int or not 1900<=statistics_since<=2100):
        raise UpdateError('season_boundary_invalid','Неверный первый сезон обновления')
    if type(max_match_requests) is not int or not 0<=max_match_requests<=2000:
        raise UpdateError('request_limit_invalid','Лимит обращений к матчевой статистике должен быть от 0 до 2000')
    from dataclasses import replace
    from .career_migration import migration_operation
    now = now or datetime.now(timezone.utc)
    from .infobox_binding import operation as binding_operation, bind
    binding = binding_operation(snapshot.text, mapping, now.date().isoformat())
    if binding:
        profile = api.details(mapping['api_id'])
        if profile.get('id') != mapping['api_id'] or profile.get('date_of_birth') != mapping['birth_date']:
            raise UpdateError('identity_mismatch', 'Не подтверждён профиль игрока без имени в карточке')
        check_player_identity(wiki, mapping)
        if snapshot.title != mapping['title'] and not is_test_page(snapshot.title):
            raise UpdateError('identity_mismatch', 'Статья не соответствует проверенной связи игрока')
        bound_text, _ = bind(snapshot.text, mapping['wiki_name'], binding)
        package, _, evidence = prepare_player(replace(snapshot, text=bound_text), mapping, api, wiki, resolver,
            max_requests=max_requests, max_match_pages=max_match_pages, max_match_requests=max_match_requests,
            statistics_since=statistics_since, now=now)
        article = package['articles'][0]
        article['operations'].insert(0, binding)
        article.pop('collection_result', None)
        for op in article['operations']:
            op['group_id'] = 'sofascore-reconciliation'
        validate_package(package)
        plan = plan_article(snapshot, article, '1.1', resolver)
        plan.content_fingerprint = plan_fingerprint(plan)
        return package, plan, evidence
    operation = migration_operation(snapshot.text, mapping['wiki_name'], now.date().isoformat())
    repairs = [operation] if operation else []
    from .statistics_layout import operation as layout_operation
    layout = layout_operation(snapshot.text,mapping['wiki_name'],now.date().isoformat())
    if layout:repairs.insert(0,layout)
    from .legacy_club_table import migration_operation as club_migration
    old_table=club_migration(snapshot.text,mapping['wiki_name'],now.date().isoformat())
    if old_table:repairs.append(old_table)
    from .klstat_normalization import operation as normalize_operation
    normalized=normalize_operation(snapshot.text,mapping['wiki_name'],now.date().isoformat())
    if normalized:repairs.append(normalized)
    unknown_period = _active_period_repair(snapshot, mapping, api, wiki, resolver, now)
    if unknown_period:
        repairs.append(unknown_period)
    if not repairs:
        return _prepare_statistics(snapshot,mapping,api,wiki,resolver,
            max_requests=max_requests,max_match_pages=max_match_pages,max_match_requests=max_match_requests,statistics_since=statistics_since,now=now)
    # A template alias change is independent of statistic collection. Still
    # verify the operator's player binding before producing this live preview.
    profile = api.details(mapping['api_id'])
    if type(profile.get('id')) is not int or profile.get('id') != mapping['api_id'] or profile.get('date_of_birth') != mapping['birth_date']:
        raise UpdateError('identity_mismatch', 'Профиль Sofascore не совпадает с подтверждённым футболистом')
    check_player_identity(wiki,mapping)
    if snapshot.title != mapping['title'] and not is_test_page(snapshot.title):
        raise UpdateError('identity_mismatch', 'Целевая страница не соответствует связи игрока')
    article = {'title':snapshot.title,'base_revid':snapshot.revid,'subject_scope':'football_statistics',
               'player':mapping['wiki_name'],'operations':repairs}
    package = {'schema_version':'1.1','package_id':'sofascore-'+str(snapshot.revid),
               'generated_at':now.isoformat(),'articles':[article]}
    validate_package(package)
    migration = plan_article(snapshot,article,'1.1',resolver)
    if any(c.status not in {'ready','already_applied'} for c in migration.changes):
        return package,migration,{'provider':'sofascore','review':[{'code':'template_migration_failed'}]}
    # Raw legacy tables may already contain incorrect sums. Correct only
    # derived totals from their preserved, fully numeric season cells.
    if old_table:
        from .structural_editor import recompute_totals
        try:fixed_totals=recompute_totals(migration.preview)
        except UpdateError:fixed_totals=migration.preview
        if fixed_totals!=migration.preview:
            totals={'id':'repair-existing-club-totals','type':'update_totals',
                'entity':{'name':'КлСтат','wikitext':'{{КлСтат}}'},
                'target':{'structure':'club_table'},'as_of':now.date().isoformat(),
                'sources':[{'url':'https://ru.wikipedia.org/wiki/'+snapshot.title,
                    'provenance':'user_provided','coverage_as_of':now.date().isoformat(),
                    'note':'Арифметический пересчёт сохранённых сезонных чисел; новые матчи не добавляются'}],
                'payload':{'complete':True}}
            for op in article['operations']+[totals]:op['group_id']='sofascore-reconciliation'
            article['operations'].append(totals)
            migration=plan_article(snapshot,article,'1.1',resolver)
    statistics_warnings = []
    try:
        statistics,statistics_plan,evidence = _prepare_statistics(replace(snapshot,text=migration.preview),
            mapping,api,wiki,resolver,max_requests=max_requests,max_match_pages=max_match_pages,max_match_requests=max_match_requests,statistics_since=statistics_since,now=now)
        article['operations'].extend(statistics['articles'][0]['operations'])
        # Statistics are addressed against the repaired text. Apply their
        # prerequisites in the same ordered transaction, including on replay.
        for op in article['operations']:
            op['group_id']='sofascore-reconciliation'
        statistics_warnings = statistics['articles'][0].get('review_warnings',[])
    except UpdateError as exc:
        evidence = {'provider':'sofascore','player_id':mapping['api_id'],
            'review':[{'code':exc.code,'message':str(exc)}], 'quota':api.quota_status()}
        statistics_warnings = ['Статистика не изменена: '+str(exc)]
    if statistics_warnings:
        article['review_warnings'] = list(dict.fromkeys(statistics_warnings))
    validate_package(package)
    plan = plan_article(snapshot,article,'1.1',resolver)
    for change in plan.changes:
        change.source_verification = ('template_redirect_migration' if change.operation['type']=='migrate_career_template'
                                      else 'existing_table_arithmetic' if change.operation['type']=='update_totals'
                                      else 'sofascore_collected_scope_checked')
    plan.content_fingerprint = plan_fingerprint(plan)
    return package,plan,evidence


def _active_period_repair(snapshot, mapping, api, wiki, resolver, now):
    """Restore a known arrival year and unknown end only for the current club."""
    parser=WikitextParser(snapshot.text)
    career=parser.career('клубы',mapping['wiki_name'])
    candidates=[row for row in career if re.fullmatch(r'(?:19|20)[0-9]{2}—\?',row[0])]
    if not candidates:
        return None
    profile=api.details(mapping['api_id'])
    current=profile.get('team',{})
    if current.get('is_national') is not False or current.get('sport',{}).get('slug')!='football':
        return None
    transfers=api.transfers(mapping['api_id'])
    if transfers.get('player_id')!=mapping['api_id']:
        raise UpdateError('identity_mismatch','История переходов другого игрока')
    arrivals=sorted((t for t in transfers.get('transfers',[]) if t.get('transfer_to',{}).get('id')==current.get('id')),
                    key=lambda t:t.get('transfer_date',''),reverse=True)
    if not arrivals:
        return None
    try:
        arrival=date.fromisoformat(arrivals[0]['transfer_date'][:10])
    except (ValueError,KeyError,TypeError):
        return None
    if arrival>now.date() or any(t.get('transfer_from',{}).get('id')==current['id']
            and t.get('transfer_date','')[:10]>=arrival.isoformat() for t in transfers.get('transfers',[])):
        return None
    resolved=resolver.resolve({'name':current['name'],'kind':'club'})
    matches=[r for r in candidates if int(r[0][:4])==arrival.year
             and reference_identity(wiki,r[1])['qid']==resolved.qid]
    if len(matches)!=1:
        return None
    period,team,_=matches[0]
    return {'id':'restore-active-career-period','type':'update_career_period','group_id':'career-period-repair',
        'entity':{'name':resolved.display,'kind':'club','wikidata_id':resolved.qid,'page_title':resolved.title,'wikitext':team},
        'target':{'structure':'career','field':'клубы','period':period},'as_of':now.date().isoformat(),
        'sources':[{'url':'https://www.sofascore.com/football/player/'+str(mapping['api_id']),
            'provenance':'sofascore','coverage_as_of':now.date().isoformat(),
            'note':'Дата прихода '+arrival.isoformat()+' и нынешний клуб подтверждены профилем и историей переходов'}],
        'payload':{'period':str(arrival.year)+'—{{н.в.}}','restore_unknown_end':True,'arrival_date':arrival.isoformat()}}


def _prepare_statistics(snapshot, mapping, api, wiki, resolver, *, max_requests=DEFAULT_STATISTICS, max_match_pages=DEFAULT_MATCH_PAGES, max_match_requests=DEFAULT_MATCH_REQUESTS, statistics_since=None, now=None):
    now = now or datetime.now(timezone.utc)
    day = now.date().isoformat()
    current_season = now.year if now.month>=7 else now.year-1
    profile = api.details(mapping['api_id'])
    if type(profile.get('id')) is not int or profile.get('id') != mapping['api_id'] or profile.get('date_of_birth') != mapping['birth_date']:
        raise UpdateError('identity_mismatch', 'Дата рождения Sofascore изменилась')
    check_player_identity(wiki, mapping)
    if snapshot.title != mapping['title'] and not is_test_page(snapshot.title):
        raise UpdateError('identity_mismatch', 'Целевая страница не соответствует связи игрока')
    parser = WikitextParser(snapshot.text)
    box = parser.infobox(mapping['wiki_name'])
    from .goalkeeper import is_goalkeeper
    if is_goalkeeper(parser, mapping['wiki_name']) and profile.get('position') not in (None,'G','GK','Goalkeeper','goalkeeper'):
        raise UpdateError('goal_metric_mismatch','Роль вратаря в статье расходится с позицией Sofascore')
    if not is_goalkeeper(parser, mapping['wiki_name']):
        for field in ('клубы', 'национальная сборная'):
            if field not in box:
                continue
            for _, _, value in parser.career(field, mapping['wiki_name']):
                if value.numeric(pair=True)[1].value < 0:
                    raise UpdateError('goal_metric_mismatch',
                        'В карточке есть пропущенные голы с минусом, но не указана позиция вратаря; обновление остановлено')
    issues, operations, records = [], [], []
    # A missing birthday is allowed only for an exact operator-assigned test
    # copy. Existing conflicting dates always stop collection. Nothing is filled.
    if is_test_page(snapshot.title):
        birthday = box.get('дата рождения')
        raw_birthday = birthday.text.strip() if birthday else ''
        if not raw_birthday and bound_test_player(snapshot.title,mapping):
            issues.append({'code':'sandbox_operator_binding','message':'Копия назначена оператором этому игроку; дата рождения не заполняется'})
        else:
            try:
                if re.fullmatch(r'\d{1,2}\.\d{1,2}\.\d{4}', raw_birthday):
                    actual_birthday = datetime.strptime(raw_birthday, '%d.%m.%Y').date()
                else:
                    actual_birthday = date.fromisoformat(raw_birthday)
            except ValueError:
                actual_birthday = None
            if actual_birthday != date.fromisoformat(mapping['birth_date']):
                raise UpdateError('identity_mismatch', 'Тестовая карточка: дата рождения отсутствует или расходится с источником')
    base = {'as_of': day, 'group_id': 'sofascore-reconciliation', 'sources': [{
        'url': 'https://www.sofascore.com/football/player/'+str(mapping['api_id']),
        'provenance': 'sofascore', 'coverage_as_of': day,
        'note': 'Получено через Sofascore Scraper; дата получения не является датой матча'}]}

    def add(kind, entity, target, **fields):
        operations.append({**deepcopy(base), 'id': 'ss-'+str(len(operations)+1),
                           'type': kind, 'entity': deepcopy(entity), 'target': target, **fields})

    def resolve_team(team):
        if not isinstance(team,dict) or not isinstance(team.get('name'),str) or not team['name'].strip():
            raise UpdateError('club_ambiguous','Источник не указал проверяемую команду')
        from .source_team_identity import resolve_source_team
        resolved = resolve_source_team(team, wiki, resolver)
        return {'name': resolved.display, 'kind': 'club', 'wikidata_id': resolved.qid,
                'page_title': resolved.title, 'wikitext': resolved.wikitext}, resolved

    current = profile.get('team', {})
    if current.get('is_national') is not False or current.get('sport', {}).get('slug') != 'football':
        raise UpdateError('club_ambiguous', 'Не подтверждён текущий клуб')
    without_club = current.get('name') == 'No team' and current.get('id') == 241802
    if without_club:
        entity, resolved_current = None, None
        issues.append({'code':'current_club_unknown','message':'В профиле нет текущего клуба; нынешний клуб в карточке сохранён'})
    else:
        entity, resolved_current = resolve_team(current)
    index = api.seasons(mapping['api_id'])
    if index.get('player_id') != mapping['api_id'] or not isinstance(index.get('tournament_seasons'), list):
        raise UpdateError('incomplete_statistics', 'Нет списка сезонов игрока')
    # Tolerate repeated season keys while collecting evidence only. The editor
    # remains strict and needs an explicit source-bound repair operation first.
    table = parser.club_table(allow_duplicates=True) if any(t.name == 'клстат' for t in parser.templates) else None
    from .competition_labels import category_label
    category_names={category_label(c):c for c in table.categories} if table else {}
    league_category=category_names.get('Чемпионат',category_names.get('Лига','Чемпионат'))
    cup_category = None
    if table:
        supported = {'Национальный кубок', 'Кубок Германии','Кубок','Кубки'}
        matches = [c for c in table.categories if category_label(c) in supported]
        cup_category = matches[0] if len(matches) == 1 else None
    european_category = category_names.get('Еврокубки')
    other_category = category_names.get('Прочие')
    league_cup_category = category_names.get('Кубок лиги')
    if not table:
        cup_category = 'Кубки'
        league_cup_category = cup_category
        european_category = 'Еврокубки'
        other_category = 'Прочие'
    if not league_cup_category and cup_category and category_label(cup_category) in {'Кубок','Кубки'}:
        league_cup_category=cup_category
    supported_tournaments = LEAGUES | CUPS | (LEAGUE_CUPS if league_cup_category else set()) | (EUROPEAN_CUPS if european_category else set()) | (SUPER_CUPS if other_category else set())
    failed_categories = set()
    failed_tournaments = set()
    def category_for(tid):
        return league_category if tid in LEAGUES else league_cup_category if tid in LEAGUE_CUPS else european_category if tid in EUROPEAN_CUPS else other_category if tid in SUPER_CUPS else cup_category
    def fail_scope(label,tid):
        failed_categories.add((label,category_for(tid)))
        failed_tournaments.add((label,tid))
    seen = set()
    queries = []
    for block in index['tournament_seasons']:
        tid = block.get('tournament', {}).get('id')
        if tid not in supported_tournaments:
            issues.append({'code': 'competition_unmapped', 'tournament_id': tid})
            # An unknown European tournament cannot disappear from an
            # aggregate European column while other competitions update it.
            name=str(block.get('tournament',{}).get('name','')).casefold()
            if european_category and any(term in name for term in ('conference league','champions league','europa league')):
                for season in block.get('seasons',[]):
                    try:label=season_name(season.get('year'))
                    except UpdateError:continue
                    failed_categories.add((label,european_category))
            continue
        for season in block.get('seasons', []):
            try:label = season_name(season.get('year'))
            except UpdateError as exc:
                issues.append({'code':exc.code,'tournament_id':tid,'season_id':season.get('id')})
                continue
            if statistics_since is not None and int(label[:4])<statistics_since:
                continue
            sid = season.get('id')
            if type(sid) is not int or (tid, sid) in seen:
                raise UpdateError('ambiguous_target', 'Повторные или неверные сезоны API')
            seen.add((tid, sid))
            types = (index.get('statistics_types', {}).get(str(tid), {}) or {}).get(str(sid))
            # The provider advertises the actual statistics scope per season.
            # Football often has overall/home/away, not regularSeason. For our
            # explicitly supported league IDs, overall is the named league;
            # home/away are venue splits, not extra competitions.
            advertised_overall = (tid in LEAGUES and tid != 242 and isinstance(types, list)
                and bool(types) and all(isinstance(t, str) for t in types)
                and 'overall' in types and set(types) <= {'overall','home','away'})
            part = ('overall' if advertised_overall else 'regularSeason') if tid in LEAGUES else 'overall'
            queries.append((label, tid, sid, part, advertised_overall))
    # Newest evidence first; the cap is a review boundary, never zero evidence.
    queries.sort(reverse=True)
    for label, tid, *_ in queries[max_requests:]:
        fail_scope(label,tid)
    for query_index, (label, tid, sid, part, advertised_overall) in enumerate(queries[:max_requests]):
        try:
            try:
                response = api.statistics(mapping['api_id'], tid, sid, part)
            except UpdateError as exc:
                if part!='regularSeason' or exc.code!='api_not_found' or tid==242:
                    raise
                part = 'overall'
                response = api.statistics(mapping['api_id'], tid, sid, part)
            from .goalkeeper import is_goalkeeper
            team, numbers = _numbers(response, mapping['api_id'], tid, sid, part, goalkeeper=is_goalkeeper(parser, mapping['wiki_name']))
            ent, resolved = resolve_team(team)
            records.append({'season': label, 'tournament_id': tid, 'season_id': sid,
                'team_id': team['id'], 'entity': ent, 'resolved': resolved, 'numbers': numbers,
                'category': category_for(tid), 'part': part,
                'advertised_overall': advertised_overall})
        except UpdateError as exc:
            fail_scope(label,tid)
            issues.append({'code': exc.code, 'season': label, 'tournament_id': tid})
            # A failed season must not hide independent seasons. Do not retry
            # the failed request; stop only for account-wide access limits.
            if exc.code in {'quota_exhausted', 'api_rate_limited', 'api_authentication'}:
                for pending_label, pending_tid, *_ in queries[query_index+1:]:
                    fail_scope(pending_label,pending_tid)
                break
    if len(queries) > max_requests:
        issues.append({'code': 'collection_capped', 'pending': len(queries)-max_requests})
    try:
        history = api.matches(mapping['api_id'], max_pages=max_match_pages)
    except UpdateError as exc:
        issues.append({'code': exc.code})
        history = {'complete': False, 'matches': []}
    verified = []
    from .sofascore_match_stats import played_matches, reconstruct
    match_budget = {'remaining': max_match_requests if statistics_since is None else 0}
    goalkeeper = is_goalkeeper(parser, mapping['wiki_name'])
    transfers = api.transfers(mapping['api_id'])
    if transfers.get('player_id')!=mapping['api_id']:
        raise UpdateError('identity_mismatch','История переходов другого игрока')
    for record in records:
        year=int(record['season'][:4])
        split=any(date(year,7,1).isoformat()<str(t.get('transfer_date',''))[:10]<date(year+1,7,1).isoformat()
            and record['team_id'] in {(t.get('transfer_from') or {}).get('id'),(t.get('transfer_to') or {}).get('id')}
            for t in transfers.get('transfers',[]))
        if split:
            club_matches = played_matches(history, record, now, european=record['tournament_id'] in EUROPEAN_CUPS,
                league=record['tournament_id'] in LEAGUES)
            if club_matches is None or len(club_matches)!=record['numbers']['appearances']:
                club_matches = played_matches(history, record, now, european=record['tournament_id'] in EUROPEAN_CUPS,
                    league=record['tournament_id'] in LEAGUES, allow_unknown_minutes=True)
                recovered = reconstruct(api, mapping['api_id'], record, club_matches, match_budget, goalkeeper=goalkeeper)
                if recovered is not None:
                    record['numbers'] = recovered
                    record['match_reconstructed'] = True
                    issues.append({'code':'match_statistics_reconstructed','season':record['season'],
                        'team_id':record['team_id'],'tournament_id':record['tournament_id'],
                        'appearances':recovered['appearances'],'match_ids':[m['id'] for m in club_matches]})
                    verified.append(record)
                    continue
                fail_scope(record['season'],record['tournament_id'])
                issues.append({'code':'multi_club_season_unproven','season':record['season'],'team_id':record['team_id'],
                    'statistics_appearances':record['numbers']['appearances'],
                    'history_appearances':len(club_matches) if club_matches is not None else None})
                continue
        if statistics_since is not None and record['tournament_id'] != 242:
            # Primary mode copies the explicit tournament aggregate after
            # player/club/season checks. No protocol-by-protocol recount.
            verified.append(record)
            continue
        if record['tournament_id'] in EUROPEAN_CUPS:
            if statistics_since is not None:
                # Copy this tournament's explicit aggregate. This mode does
                # not rebuild older/qualification scopes from match protocols.
                verified.append(record)
                continue
            scoped=[];unknown=False
            for m in history['matches']:
                p=m.get('player') or {};s=m.get('season') or {}
                if p.get('team_id')!=record['team_id'] or (m.get('tournament') or {}).get('id')!=record['tournament_id']:continue
                try:label=season_name(s.get('year'))
                except UpdateError:label=None
                if m.get('is_finished') is not True or (m.get('status') or {}).get('type')!='finished':continue
                # A defective date in a different, explicitly identified
                # season cannot invalidate the target season's evidence.
                if s.get('id')!=record['season_id'] and label is not None and label!=record['season']:
                    continue
                try:match_day=datetime.fromisoformat(m['start_time'].replace('Z','+00:00'))
                except (KeyError,TypeError,ValueError):unknown=True;continue
                if match_day.tzinfo is None:unknown=True;continue
                if match_day>now:continue
                if s.get('id')!=record['season_id'] and label!=record['season']:
                    year=int(record['season'][:4])
                    if label is None and date(year,6,1)<=match_day.date()<date(year+1,7,1):unknown=True
                    continue
                minutes=(p.get('statistics') or {}).get('minutes_played')
                if type(minutes) not in (int,float):unknown=True
                elif minutes>0:scoped.append(m)
            # Some season endpoints omit qualification while the article's
            # European total includes it. Never replace the complete column
            # with the main-round subtotal.
            if not history['complete'] or unknown or len(scoped)!=record['numbers']['appearances']:
                matches = played_matches(history, record, now, european=True, allow_unknown_minutes=True)
                recovered = reconstruct(api, mapping['api_id'], record, matches, match_budget, goalkeeper=goalkeeper)
                if recovered is not None:
                    record['numbers'] = recovered
                    record['match_reconstructed'] = True
                    issues.append({'code':'match_statistics_reconstructed','season':record['season'],
                        'team_id':record['team_id'],'tournament_id':record['tournament_id'],
                        'appearances':recovered['appearances'],'match_ids':[m['id'] for m in matches]})
                    verified.append(record)
                    continue
                fail_scope(record['season'],record['tournament_id'])
                issues.append({'code':'european_scope_unproven','season':record['season'],
                    'tournament_id':record['tournament_id'],'statistics_appearances':record['numbers']['appearances'],
                    'history_appearances':len(scoped)})
                continue
        if record['tournament_id'] not in LEAGUES or record['part']=='regularSeason':
            verified.append(record)
            continue
        if record['tournament_id'] == 38:
            belgian_matches = played_matches(history, record, now)
            if belgian_matches is not None and len(belgian_matches)==record['numbers']['appearances']:
                verified.append(record)
            else:
                fail_scope(record['season'], record['tournament_id'])
                issues.append({'code':'overall_league_unproven','season':record['season'],'tournament_id':38})
            continue
        # Some provider versions expose only overall. Accept this fallback only
        # if complete match history proves it contains exactly league games.
        scoped = [m for m in history['matches'] if
                  ((m.get('player',{}) or {}).get('team_id'),(m.get('tournament',{}) or {}).get('id'),(m.get('season',{}) or {}).get('id'))
                  == (record['team_id'],record['tournament_id'],record['season_id'])
                  and m.get('is_finished') is True and (m.get('status',{}) or {}).get('type')=='finished'
                  and isinstance(((m.get('player',{}) or {}).get('statistics',{}) or {}).get('minutes_played'), (int,float))
                  and m['player']['statistics']['minutes_played']>0]
        from .sofascore_match_stats import excluded_league_round
        playoffs = any(excluded_league_round(record['tournament_id'], m.get('round') or {}) for m in scoped)
        if not playoffs and (record['advertised_overall'] or
                history['complete'] and len(scoped)==record['numbers']['appearances'] and scoped):
            verified.append(record)
        else:
            issues.append({'code':'overall_league_unproven','season':record['season'],
                           'tournament_id':record['tournament_id']})
    records = verified
    # A player can have an indexed cup season and no league index entry when
    # every league appearance was on the bench. Verify the actual roster and
    # events for each missing scope; index omission alone never supplies zero.
    from .sofascore_unindexed import recover_unindexed
    groups = {(r['resolved'].qid,r['season']) for r in records} if statistics_since is None else set()
    for qid,label in sorted(groups):
        anchors = [r for r in records if (r['resolved'].qid,r['season'])==(qid,label)
            and r['numbers']['appearances']>0]
        if not anchors:continue
        team_ids={r['team_id'] for r in anchors}
        if len(team_ids)!=1:continue
        team_id=next(iter(team_ids))
        present={r['tournament_id'] for r in records if (r['team_id'],r['season'])==(team_id,label)}
        scoped=[m for m in history['matches'] if (m.get('player') or {}).get('team_id')==team_id
            and (m.get('season') or {}).get('year') in {label,label[2:]}]
        tids={(m.get('tournament') or {}).get('id') for m in scoped} & (LEAGUES | EUROPEAN_CUPS) & supported_tournaments
        for tid in sorted(tids-present):
            if tid in LEAGUES and not all(r['resolved'].flag=='Германии' for r in anchors):continue
            scopes={(tid,(m.get('season') or {}).get('id')) for m in scoped if (m.get('tournament') or {}).get('id')==tid}
            if scopes & seen or tid in LEAGUES and present & LEAGUES:continue
            result=None
            for anchor in anchors:
                result=recover_unindexed(history,anchor,mapping['api_id'],api,match_budget,now,tid,
                    european=tid in EUROPEAN_CUPS,league=tid in LEAGUES,goalkeeper=goalkeeper)
                if result is not None:break
                if match_budget.get('failures') and match_budget['failures'][-1].get('tournament_id')==tid:break
            if result is None:continue
            records.append({**anchor,'tournament_id':tid,'season_id':result['season_id'],
                'numbers':result['numbers'],'category':category_for(tid),'part':'overall',
                'match_reconstructed':True,'confirmed_match_ids':result['match_ids']})
            issues.append({'code':'unindexed_scope_reconstructed','season':label,'team_id':team_id,
                'tournament_id':tid,'numbers':result['numbers'],'match_ids':result['match_ids']})
            base['sources'][0]['note']+='; отсутствующий в индексе турнир восстановлен по составам и событиям: '+', '.join(map(str,result['match_ids']))
    recovered_ids = sorted({mid for issue in issues if issue['code']=='match_statistics_reconstructed'
        for mid in issue['match_ids']})
    if recovered_ids:
        base['sources'][0]['note'] += '; клубные/еврокубковые итоги восстановлены по явной статистике матчей: '+', '.join(map(str,recovered_ids))

    missing_cup_cache={}
    missing_europe_cache={}
    current_other_cache={}
    from .dfb_club_cycle import club_secondary_scopes
    secondary_values, secondary_entities, secondary_issues, secondary_scopes = club_secondary_scopes(
        snapshot, {**mapping, 'source_name':profile.get('name')}, api, wiki, table, now,
        goalkeeper=goalkeeper) if statistics_since is None else ({},{},[],[])
    issues.extend(secondary_issues)
    secondary_used = {}
    from .club_career_closure import gent_closed_spell
    closed_spell=gent_closed_spell(mapping,records,history,api,now) if statistics_since is None else None
    if closed_spell:
        issues.append({'code':'remaining_categories_from_closed_club_spell',**closed_spell})
    def primary_category_numbers(qid, label, category):
        label=table_season_name(label)
        # A combined column cannot be considered complete merely because one
        # of its tournaments returned data while another failed.
        if category != league_category and (label,category) in failed_categories:
            return None
        if table:
            aliases={r.season for r in table.seasons if table_season_name(r.season)==label and row_entity(r.club)==qid}
            if len(aliases)>1:
                return None
        rows = [r for r in records if r['season']==label and r['resolved'].qid==qid and r['category']==category]
        if not rows:
            if (label,category) in failed_categories:return None
            if statistics_since is not None:
                # No season response is unknown, never an inferred zero.
                return None
            if (closed_spell and (qid,label)==(closed_spell['qid'],closed_spell['season'])
                    and category in {cup_category,'Стыковые матчи','Прочие'}):
                return closed_spell['numbers']
            league=[r for r in records if r['season']==label and r['resolved'].qid==qid and r['tournament_id'] in LEAGUES]
            zero=absent_category_zero(history,league[0],'Национальный кубок' if category==cup_category else category,now) if len(league)==1 else None
            if (zero is None and category=='Прочие' and len(league)==1
                    and league[0]['resolved'].flag=='Германии'):
                key=(qid,label)
                if key not in current_other_cache:
                    from .sofascore_current_other import recover_current_other
                    proof=recover_current_other(history,league[0],api,
                        match_budget if statistics_since is None else {'remaining':4},now)
                    current_other_cache[key]=proof
                    if proof:
                        issues.append({'code':'current_other_from_competition_calendars','qid':qid,
                                       'season':label,**proof})
                        for url,provider in zip(proof['primary_sources'],('dfl','club_official')):
                            base['sources'].append({'url':url,'provenance':provider,'coverage_as_of':day,
                                'note':'Прочие 2026/27: полный Суперкубок без этого клуба; стыковые матчи не раньше 27.05.2027; Telekom Cup — товарищеский турнир'})
                if current_other_cache[key]:zero=current_other_cache[key]['numbers']
            if statistics_since is None and zero is None and category==cup_category and len(league)==1 and league[0]['resolved'].flag=='Германии':
                key=(qid,label)
                if key not in missing_cup_cache:
                    from .sofascore_missing_cup import recover_cup
                    missing_cup_cache[key]=recover_cup(history,league[0],mapping['api_id'],api,match_budget,now,goalkeeper=goalkeeper)
                    if missing_cup_cache[key] is None:
                        from .sofascore_calendar import recover_calendar
                        calendar=recover_calendar(league[0],mapping['api_id'],api,match_budget,now,217,goalkeeper=goalkeeper)
                        if calendar is not None:
                            missing_cup_cache[key]=(calendar['numbers'],calendar['match_ids'])
                            issues.append({'code':'cup_from_complete_tournament_calendar','qid':qid,
                                'season':label,'season_id':calendar['season_id'],
                                'numbers':calendar['numbers'],'match_ids':calendar['match_ids'],
                                'calendar_match_ids':calendar['calendar_match_ids'],
                                'roster_proven_absences':calendar.get('roster_proven_absences',[]),
                                'secondary_proofs':calendar.get('secondary_proofs',[])})
                            for proof in calendar.get('secondary_proofs',[]):
                                base['sources'].append({'url':proof['source_url'],'provenance':'dfb',
                                    'coverage_as_of':now.date().isoformat(),
                                    'note':'Протокол DFB: оба состава сопоставлены с Sofascore; три разрешённые замены использованы другими игроками; матч '+str(proof['match_id'])})
                    if missing_cup_cache[key] is not None:
                        value,ids=missing_cup_cache[key]
                        issues.append({'code':'missing_cup_reconstructed','qid':qid,'season':label,'numbers':value,'match_ids':ids})
                        base['sources'][0]['note']+='; кубок восстановлен по подтверждённым составам и событиям матчей: '+', '.join(map(str,ids))
                if missing_cup_cache[key] is not None:return missing_cup_cache[key][0]
            if zero is not None and not current_other_cache.get((qid,label)) and not any(i['code']=='zero_from_complete_match_history' and i.get('season')==label and i.get('category')==category and i.get('qid')==qid for i in issues):
                issues.append({'code':'zero_from_complete_match_history','season':label,'category':category,'qid':qid})
            if statistics_since is None and zero is None and category=='Еврокубки' and len(league)==1 and 2009<=int(label[:4])<2021:
                # From 2009/10 through 2020/21 the European club column has
                # Champions League and Europa League; Conference starts in
                # 2021/22. Verify both full calendars, including qualifying.
                key=(qid,label)
                if key not in missing_europe_cache:
                    from .sofascore_calendar import recover_european_calendar
                    value=recover_european_calendar(league[0],mapping['api_id'],api,match_budget,now,goalkeeper=goalkeeper)
                    missing_europe_cache[key]=value['numbers'] if value is not None else None
                    if value is not None:
                        issues.append({'code':'european_from_complete_tournament_calendars',
                            'qid':qid,'season':label,**value})
                        base['sources'][0]['note']+='; еврокубки подтверждены полными календарями Лиги чемпионов и Лиги Европы, включая квалификацию'
                return missing_europe_cache[key]
            return zero
        if any((label,r['tournament_id']) in failed_tournaments for r in rows):return None
        if statistics_since is None and category=='Еврокубки' and not european_history_covered(history,rows,label,now):
            if not any(i['code']=='european_category_incomplete' and i.get('qid')==qid and i.get('season')==label for i in issues):
                issues.append({'code':'european_category_incomplete','qid':qid,'season':label})
            return None
        if len({r['tournament_id'] for r in rows}) != len(rows):
            return None
        if len(rows)>1 and category!=european_category and not (category==cup_category and category_label(category) in {'Кубок','Кубки'}):
            return None
        return {k:sum(r['numbers'][k] for r in rows) for k in ('appearances','goals')}

    def category_numbers(qid, label, category):
        primary = primary_category_numbers(qid, label, category)
        key = (qid, table_season_name(label), category)
        secondary = secondary_values.get(key)
        if primary is not None:
            if secondary and primary != secondary['numbers']:
                from .dfb_club_reconciliation import omitted_verified_appearances
                selected_records = [r for r in records if r['season']==key[1]
                    and r['resolved'].qid==qid and r['category']==category]
                omitted = omitted_verified_appearances(selected_records,history,secondary)
                if omitted:
                    if not any(i['code']=='secondary_club_list_incomplete' and i.get('scope')==list(key) for i in issues):
                        issues.append({'code':'secondary_club_list_incomplete','scope':list(key),
                            'missing_verified_match_ids':omitted,'primary':primary,'secondary':secondary['numbers']})
                    for s in secondary_scopes:
                        if (s['qid'],s['season'],s['category']) == key:
                            s['complete']=False
                            s['incomplete_reason']='missing_verified_appearances'
                    return primary
                if not any(i['code']=='club_sources_disagree' and i.get('scope')==list(key) for i in issues):
                    issues.append({'code':'club_sources_disagree','scope':list(key),
                                   'primary':primary,'secondary':secondary['numbers']})
                return None
            return primary
        if secondary:
            secondary_used[key] = secondary
            return secondary['numbers']
        return None

    identities = {}
    def row_entity(text):
        source_identities = [key for key,entity in secondary_entities.items()
            if entity.get('source_entity_id') and entity['wikitext']==text]
        if len(source_identities)==1:return source_identities[0]
        if source_identities:return None
        if text in identities:
            return identities[text]
        try:
            identities[text] = reference_identity(wiki, text)['qid']
            return identities[text]
        except UpdateError as exc:
            identities[text]=None
            issues.append({'code':'article_club_identity_unproven','wikitext':text,'message':str(exc)})
            return None

    if table:
        from collections import defaultdict
        duplicate_rows=defaultdict(list)
        def table_row_numbers(row):
            return {c:{'appearances':row.values[str(2+2*i)].numeric()[0].value,
                       'goals':row.values[str(3+2*i)].numeric(signed=True)[0].value} for i,c in enumerate(table.categories)}
        for row in table.seasons:
            if statistics_since is not None and int(table_season_name(row.season)[:4])<statistics_since:
                continue
            duplicate_rows[(row.club,row.season)].append(row)
        for (club,label),rows in duplicate_rows.items():
            if len(rows)<2:continue
            qid=row_entity(club)
            known={c:n for c in table.categories if (n:=category_numbers(qid,label,c)) is not None}
            old=[table_row_numbers(row) for row in rows]
            unknown=set(table.categories)-set(known)
            if not known or any(any(r[c]!=old[0][c] for r in old) for c in unknown):
                issues.append({'code':'duplicate_season_unresolved','season':label,'club':club})
                continue
            add('repair_duplicate_season',{'name':'КлСтат','wikitext':club},{'structure':'club_table'},season=label,
                payload={'expected_rows':old,'categories':known})
            if unknown:
                issues.append({'code':'duplicate_season_unconfirmed_categories','season':label,'categories':sorted(unknown)})
        for row in table.seasons:
            if statistics_since is not None and int(table_season_name(row.season)[:4])<statistics_since:
                continue
            qid = row_entity(row.club)
            for category in table.categories:
                new = category_numbers(qid, row.season, category)
                if new is None:
                    issues.append({'code': 'category_not_covered', 'club': row.club,
                                   'season': row.season, 'category': category})
                    continue
                i = table.categories.index(category)
                old = {k: row.values[str(2+2*i+j)].numeric(signed=bool(j))[0].value
                       for j, k in enumerate(('appearances', 'goals'))}
                if old != new:
                    if not any(op['type'] == 'update_totals' for op in operations):
                        add('update_totals', {'name': 'КлСтат', 'wikitext': '{{КлСтат}}'},
                            {'structure': 'club_table'}, payload={'complete': True})
                    secondary = secondary_used.get((qid,table_season_name(row.season),category))
                    source_override = {'as_of':secondary['statistics_as_of'],
                        'group_id':'dfb-club-'+qid+'-'+table_season_name(row.season)+'-'+str(i), 'sources':[{'url':url,'provenance':'dfb',
                        'coverage_as_of':secondary['statistics_as_of'],
                        'note':'Итог закрытого клубного сезона DFB сверён с полным списком выступлений'}
                        for url in secondary['sources']]} if secondary else {}
                    if secondary and secondary.get('complete') is True and secondary.get('confirmed_calendar_match_ids'):
                        source_override={'as_of':base['as_of'],'group_id':base['group_id'],
                            'sources':[{'url':url,'provenance':'dfb','coverage_as_of':base['as_of'],
                                'note':'Заново проверен законченный сезон по '+secondary['statistics_as_of']+' по полному официальному календарю и всем протоколам'} for url in secondary['sources']]}
                    if (closed_spell and (qid,table_season_name(row.season))==(closed_spell['qid'],closed_spell['season'])
                            and category in {cup_category,'Стыковые матчи','Прочие'}):
                        # This ended spell is immutable; the complete current
                        # history reconfirms its closed total. Keep the season's
                        # historical boundary in the evidence, while related
                        # arithmetic operations share today's verification date.
                        source_override={'as_of':base['as_of'],
                            'group_id':base['group_id'],
                            'sources':[{'url':url,'provenance':'club_official','coverage_as_of':base['as_of'],
                                'note':'Заново проверен законченный период по '+closed_spell['statistics_as_of']+': официальный полный итог совпадает с суммой подтверждённых чемпионата и еврокубков'} for url in closed_spell['sources']]}
                    add('update_stats', {'name': next((r['entity']['name'] for r in records if r['resolved'].qid==qid), secondary_entities.get(qid,{}).get('name',row.club)), 'wikitext': row.club},
                        {'structure': 'club_table'}, season=row.season,
                        competition={'name': category, 'category': category, 'scope': 'category'},
                        expected=old, new=new, **source_override)
        # New rows need explicit evidence for every column; no inferred zeros.
        covered_rows = {(r['resolved'].qid, r['season']) for r in records}
        existing = {(row_entity(r.club), table_season_name(r.season)) for r in table.seasons}
        planned_rows={}
        for qid, label in sorted(covered_rows-existing):
            selected = [r for r in records if r['resolved'].qid == qid and r['season']==label]
            categories = {c: category_numbers(qid, label, c) for c in table.categories}
            league = [r for r in selected if r['tournament_id'] in LEAGUES]
            if len(league)!=1 or statistics_since is None and any(numbers is None for numbers in categories.values()):
                issues.append({'code': 'new_row_needs_all_categories', 'qid': qid, 'season': label,
                    'missing_categories': [c for c, value in categories.items() if value is None],
                    'league_status': 'missing' if not league else 'ambiguous' if len(league)>1 else 'confirmed'})
                continue
            missing=[c for c,n in categories.items() if n is None]
            if missing:issues.append({'code':'unknown_table_cells','qid':qid,'season':label,'missing_categories':missing})
            blocks = [r for r in table.seasons if row_entity(r.club)==qid]
            blocks += planned_rows.get(qid,[])
            ent = deepcopy(league[0]['entity'])
            payload = {'complete': True, 'categories': categories,
                       'league_wikitext': LEAGUE_LINKS[league[0]['tournament_id']]}
            if missing:payload['allow_unknown']=True
            if blocks:
                ent['wikitext'] = blocks[0].club
                earlier = sorted((r for r in blocks if table_season_name(r.season) < label),key=lambda r:table_season_name(r.season))
                payload.update({'insert_after_season': earlier[-1].season} if earlier
                               else {'insert_before_season': blocks[0].season})
                add('add_season', ent, {'structure': 'club_table'}, season=label, payload=payload)
            else:
                starts={}
                for r in table.seasons+[r for rows in planned_rows.values() for r in rows]:
                    starts[r.club]=min(starts.get(r.club,table_season_name(r.season)),table_season_name(r.season))
                later=sorted((club for club,first in starts.items() if first>label),key=lambda c:starts[c])
                if later:payload['insert_before_club']=later[0]
                else:payload['insert_after_club']=max(starts,key=lambda c:starts[c])
                add('add_table_club', ent, {'structure': 'club_table'}, season=label, payload=payload)
            from types import SimpleNamespace
            planned_rows.setdefault(qid,[]).append(SimpleNamespace(club=ent['wikitext'],season=label))

    # Infobox totals require consecutive league seasons for one unbroken stint.
    if transfers.get('player_id') != mapping['api_id'] or not isinstance(transfers.get('transfers'), list):
        raise UpdateError('incomplete_transfers', 'Нет проверенной истории переходов')
    arrivals = [t for t in transfers['transfers'] if t.get('transfer_to', {}).get('id') == current['id']]
    arrivals.sort(key=lambda t: t.get('transfer_date', ''), reverse=True)
    arrival = arrivals[0] if arrivals else None
    career = parser.career('клубы', mapping['wiki_name'])

    def total(qid, first, last):
        if last < first:
            return None
        rows = [r for r in records if r['resolved'].qid == qid and r['tournament_id'] in LEAGUES
                and first <= int(r['season'][:4]) <= last]
        years = [int(r['season'][:4]) for r in rows]
        preserved=[]
        if statistics_since is not None and first<statistics_since:
            # The operator explicitly retains old seasons. Use those existing
            # league cells once, never a stale whole-career total as a base.
            if not table:return None
            for year in range(first,min(last+1,statistics_since)):
                old_rows=[r for r in table.seasons if row_entity(r.club)==qid
                    and int(table_season_name(r.season)[:4])==year]
                if len(old_rows)!=1:return None
                a=old_rows[0].values['2'].numeric()[0].value
                g=old_rows[0].values['3'].numeric(signed=True)[0].value
                if a is None or g is None or a<0 or (g>0 if goalkeeper else g<0):return None
                preserved.append({'appearances':a,'goals':g})
            first=statistics_since
        if sorted(years) != list(range(first, last+1)):
            return None
        return {k: sum(r['numbers'][k] for r in rows)+sum(r[k] for r in preserved) for k in ('appearances', 'goals')}

    current_rows = [r for r in career if resolved_current is not None and row_entity(r[1]) == resolved_current.qid and is_active_period(r[0])]
    modern_closed_coverage=set()
    if statistics_since is not None:
        for period,text,value in career:
            if is_active_period(period):continue
            years=re.fullmatch(r'((?:19|20)[0-9]{2})—((?:19|20)[0-9]{2})',period)
            if not years:continue
            first,last=int(years[1]),int(years[2])-1
            if last<statistics_since:
                modern_closed_coverage.add((period,text))
                continue
            qid=row_entity(text)
            ids={r['team_id'] for r in records if r['resolved'].qid==qid}
            exits=[t for t in transfers['transfers'] if (t.get('transfer_from') or {}).get('id') in ids
                and str(t.get('transfer_date',''))[:4]==years[2]]
            # Calendar-year career periods map to full league seasons only
            # when a summer departure actually closes this stint.
            if len(ids)!=1 or len(exits)!=1:continue
            try:exit_day=date.fromisoformat(exits[0]['transfer_date'][:10])
            except (KeyError,ValueError,TypeError):continue
            if exit_day>now.date() or exit_day.month not in {6,7,8}:continue
            new=total(qid,first,last)
            if new is None:continue
            old=dict(zip(('appearances','goals'),[n.value for n in value.numeric(pair=True)]))
            if old!=new:
                add('update_stats',{'name':text,'wikitext':text},
                    {'structure':'career','field':'клубы','period':period},season=None,
                    competition={'name':'Чемпионат','category':'all','scope':'league'},expected=old,new=new)
            modern_closed_coverage.add((period,text))
    club_consistent = False
    if current_rows and len({r[0] for r in current_rows})==1 and len({r[1] for r in current_rows})==1:
        period, text, value = current_rows[0]
        first = validate_period(period)
        departed = any(t.get('transfer_from',{}).get('id')==current['id']
                       and str(t.get('transfer_date',''))[:4]>=str(first) for t in transfers['transfers'])
        new = None if departed else total(resolved_current.qid, first, current_season)
        if new:
            old = dict(zip(('appearances', 'goals'), [n.value for n in value.numeric(pair=True)]))
            if len(current_rows)>1:
                expected=[dict(zip(('appearances','goals'),[n.value for n in r[2].numeric(pair=True)])) for r in current_rows]
                add('repair_career_duplicates',{'name':entity['name'],'wikitext':text},
                    {'structure':'career','field':'клубы','period':period},payload={'expected_stats':expected,'stats':new})
            elif old != new:
                add('update_stats', {'name': entity['name'], 'wikitext': text},
                    {'structure': 'career', 'field': 'клубы', 'period': period}, season=None,
                    competition={'name': 'Чемпионат', 'category': 'all', 'scope': 'league'}, expected=old, new=new)
        else:
            issues.append({'code': 'career_incomplete', 'club': current['id']})
        club_consistent = row_entity(box.get('нынешний клуб').text) == resolved_current.qid if box.get('нынешний клуб') else False
        if new and not club_consistent and box.get('нынешний клуб'):
            add('update_current_club',entity,{'structure':'career','field':'клубы'},
                expected=box['нынешний клуб'].text.strip(),payload={})
            club_consistent=True
    elif not current_rows and arrival and resolved_current is not None:
        previous_team = arrival.get('transfer_from', {})
        previous_ent, previous = resolve_team(previous_team)
        active = [r for r in career if is_active_period(r[0])]
        try:
            transfer_day = date.fromisoformat(arrival['transfer_date'][:10])
        except (KeyError, ValueError, TypeError):
            raise UpdateError('incomplete_transfers', 'Нет даты перехода') from None
        if (len(active) == 1 and row_entity(active[0][1]) == previous.qid
                and transfer_day <= now.date() and transfer_day.month in (6, 7, 8)):
            period, text, value = active[0]
            first = validate_period(period)
            year = transfer_day.year
            old_total = total(previous.qid, first, year-1)
            new_total = total(resolved_current.qid, year, current_season)
            departed_before = any(t.get('transfer_from',{}).get('id')==previous_team['id']
                and f'{first}-01-01'<=str(t.get('transfer_date',''))[:10]<transfer_day.isoformat()
                for t in transfers['transfers'])
            if departed_before:
                old_total = None
            if old_total is not None and new_total is not None:
                old = dict(zip(('appearances', 'goals'), [n.value for n in value.numeric(pair=True)]))
                if old != old_total:
                    add('update_stats', {'name': previous_ent['name'], 'wikitext': text},
                        {'structure': 'career', 'field': 'клубы', 'period': period}, season=None,
                        competition={'name': 'Чемпионат', 'category': 'all', 'scope': 'league'}, expected=old, new=old_total)
                closed = f'{first}—{year}'
                add('update_career_period', {'name': previous_ent['name'], 'wikitext': text},
                    {'structure': 'career', 'field': 'клубы', 'period': period}, payload={'period': closed})
                add('add_club', entity, {'structure': 'career', 'field': 'клубы'},
                    payload={'period': f'{year}—{{{{н.в.}}}}', 'role': 'primary', 'stats': new_total,
                             'previous': {'wikitext': text, 'period': closed}})
                add('update_current_club', entity, {'structure': 'career', 'field': 'клубы'},
                    expected=box['нынешний клуб'].text.strip(), payload={})
                club_consistent = True
            else:
                issues.append({'code': 'career_incomplete', 'club': previous_team['id']})
        else:
            issues.append({'code': 'transfer_chain_needs_review'})
    else:
        issues.append({'code': 'current_club_needs_review'})
    number = profile.get('jersey_number')
    if club_consistent and type(number) is int and 1 <= number <= 99 and box.get('номер'):
        raw_number=box['номер'].text.strip()
        try:old = box['номер'].numeric()[0].value
        except UpdateError:
            old=None
        number_payload=({'expected_number':old,'number':number} if type(old) is int and 1<=old<=99
            else {'expected_raw':raw_number,'number':number} if re.fullmatch(r'[?—–-]?|0',raw_number) else None)
        # If the already-current club uses a redirect/alternative display, the
        # editor still requires its canonical, independently checked identity.
        if box['нынешний клуб'].text.strip() == entity['wikitext'] or any(o['type']=='update_current_club' for o in operations):
            if old != number and number_payload is not None:
                add('update_shirt_number', entity, {'structure': 'career', 'field': 'клубы'},
                    payload=number_payload)
            elif number_payload is None:
                issues.append({'code':'shirt_number_format_needs_review'})
        elif old != number:
            issues.append({'code': 'number_club_format_needs_review'})
    elif type(number) is not int or not 1 <= number <= 99:
        issues.append({'code': 'shirt_number_unknown'})

    scopes = {(r['team_id'], r['tournament_id'], r['season_id']) for r in records if r['tournament_id'] in LEAGUES}
    confirmed = confirmed_match_date(history, scopes, now)
    # A card-wide date asserts coverage of every career row. Partial evidence
    # can change individual cells but cannot advance that global assertion.
    def career_covered(period, team):
        first = validate_period(period)
        if is_active_period(period):
            last = current_season
        else:
            if statistics_since is not None:
                return (period,team) in modern_closed_coverage
            # A closed calendar-year stint does not identify its final season
            # without transfer evidence. Do not overclaim global coverage.
            return False
        return total(row_entity(team), first, last) is not None
    coverage_complete = all(career_covered(period, team) for period, team, _ in career)
    if confirmed:
        old = box.get('обновление данных о клубе')
        try:
            old_day = datetime.strptime(old.text.strip(), '%d.%m.%Y').date() if old else None
        except ValueError:
            old_day = None
        if statistics_since is not None and old_day and old_day.year >= statistics_since:
            # A previously dated closed stint cannot gain new appearances.
            # Advancing an existing date needs complete active stints, not a
            # refetch of unchanged careers which ended before that date.
            def covered_since_previous_update(period, team):
                if is_active_period(period):
                    return career_covered(period, team)
                years = re.findall(r'(?:19|20)[0-9]{2}', period)
                return bool(years) and int(years[-1]) < old_day.year or career_covered(period, team)
            coverage_complete = all(covered_since_previous_update(period, team) for period,team,_ in career)
        if old_day and confirmed < old_day.isoformat():
            issues.append({'code': 'date_evidence_older', 'confirmed_match_date': confirmed, 'article_date': old_day.isoformat()})
        elif old_day and confirmed > old_day.isoformat() and coverage_complete and entity is not None:
            add('update_date', entity, {'structure': 'career', 'field': 'клубы'},
                payload={'field': 'обновление данных о клубе', 'expected_value': old.text.strip(),
                         'statistics_as_of': confirmed})
        elif not coverage_complete:
            issues.append({'code': 'global_date_incomplete_coverage', 'confirmed_match_date': confirmed})
    else:
        issues.append({'code': 'match_date_unconfirmed'})
    if not table:
        league_records = [r for r in records if r['tournament_id'] in LEAGUES]
        if league_records and (confirmed or statistics_since is not None):
            clubs = {}
            categories = [league_category] + [c for c in (cup_category,european_category,other_category)
                if any(category_for(tid)==c for _,tid,*_ in queries)]
            for record in sorted(league_records,key=lambda r:r['season']):
                qid = record['resolved'].qid
                values = {c:category_numbers(qid,record['season'],c) for c in categories}
                if statistics_since is None and any(v is None for v in values.values()):
                    issues.append({'code':'new_row_needs_all_categories','qid':qid,
                        'season':record['season'],'missing_categories':[c for c,v in values.items() if v is None]})
                    continue
                missing=[c for c,v in values.items() if v is None]
                if missing:issues.append({'code':'unknown_table_cells','qid':qid,'season':record['season'],'missing_categories':missing})
                block = clubs.setdefault(qid, {'entity': record['entity'], 'rows': []})
                block['rows'].append({'season': record['season'],
                    'league_wikitext': LEAGUE_LINKS[record['tournament_id']],
                    'categories': values})
            if clubs:
                from .competition_notes import column_notes
                header_notes=column_notes(records,list(clubs.values()),categories)
                add('create_statistics_section', {'name': 'КлСтат', 'wikitext': '{{КлСтат}}'},
                    {'structure': 'club_table'}, payload={'complete': True, 'full_career': False,'allow_unknown':statistics_since is not None,
                    'statistics_as_of': confirmed, 'categories': categories, 'clubs': list(clubs.values()),'header_notes':header_notes})
                issues.append({'code': 'partial_section', 'message': 'Известные сезонные показатели скопированы; неизвестные ячейки обозначены длинным тире, полного итога карьеры нет'})
            else:
                issues.append({'code':'section_needs_confirmed_evidence'})
        else:
            issues.append({'code': 'section_needs_confirmed_evidence'})
    if table and confirmed and not any(r['code'] in {'category_not_covered', 'new_row_needs_all_categories'} for r in issues):
        table_date = confirmed_match_date(history,
            {(r['team_id'], r['tournament_id'], r['season_id']) for r in records if r['category'] in table.categories}, now)
        table_ref = next(t for t in parser.templates if t.name=='клстат')
        markers = [t for t in parser.templates if t.name=='обновлено'
                   and t.start+len(str(t.node))<=table_ref.start
                   and not snapshot.text[t.start+len(str(t.node)):table_ref.start].strip()]
        if table_date and len(markers)==1:
            old = markers[0].values()['1'].text.strip()
            if old < table_date:
                add('update_table_date', {'name': 'КлСтат', 'wikitext': '{{КлСтат}}'},
                    {'structure': 'club_table'}, payload={'expected_value': old, 'statistics_as_of': table_date})
    missing_rows = [r for r in issues if r['code']=='new_row_needs_all_categories']
    source_warnings = []
    unknown_cells=[i for i in issues if i['code']=='unknown_table_cells']
    if unknown_cells:source_warnings.append('Неизвестные ячейки новых сезонов и неподтверждённые новые итоги обозначены длинным тире; прежние итоги сохраняются. Подробности в отчёте.')
    uncovered = [i for i in issues if i['code']=='category_not_covered']
    if uncovered:
        source_warnings.append('Не подтверждены '+str(len(uncovered))+' графы существующих клубных строк; '
            'исходные значения сохранены. Полнота таблицы не подтверждена; сезоны: '+
            ', '.join(sorted({i['season'] for i in uncovered}))+'. Подробности указаны в отчёте.')
    if any(i['code']=='global_date_incomplete_coverage' for i in issues):
        source_warnings.append('Общая дата клубной статистики сохранена: не подтверждён полный охват всех периодов карьеры.')
    if any(i['code']=='match_date_unconfirmed' for i in issues):
        source_warnings.append('Общая дата клубной статистики сохранена: дата последнего подтверждённого матча не установлена.')
    if any(i['code']=='club_sources_disagree' for i in issues):
        source_warnings.append('Клубные источники расходятся по одной или нескольким графам; спорные значения сохранены до сверки протоколов.')
    if secondary_issues:
        source_warnings.append('Часть дополнительных клубных данных DFB не прошла проверку личности или полноты; причины указаны в отчёте.')
    if any(i['code'] in {'european_scope_unproven','european_category_incomplete'} for i in issues):
        source_warnings.append('Охват части еврокубковых сезонов не подтверждён полной историей матчей; квалификация не исключается из общей графы. Неподтверждённые ячейки сохранены.')
    if any(i['code']=='multi_club_season_unproven' for i in issues):
        source_warnings.append('Статистика сезона с переходом между клубами не подтверждена отдельно для выбранного клуба. Общий итог нескольких клубов не переносится в строку одной команды.')
    for issue in issues:
        if issue['code']=='duplicate_season_unconfirmed_categories':
            source_warnings.append('Дубликат сезона '+issue['season']+' объединён по подтверждённым графам; '+
                ', '.join(issue['categories'])+' сохранены из совпадающих исходных ячеек и не подтверждены источником.')
        elif issue['code']=='duplicate_season_unresolved':
            source_warnings.append('Дубликат сезона '+issue['season']+' не исправлен: недостаточно данных для различающихся ячеек.')
    if missing_rows:
        labels = {r['resolved'].qid:r['resolved'].display for r in records}
        source_warnings.append('Не восстановлены строки сезонов: '+', '.join(
            labels.get(r['qid'],r['qid'])+' '+r['season']+' (не подтверждены: '+
            ', '.join(r['missing_categories'] or ['однозначная лига'])+')' for r in missing_rows)+
            '; полнота таблицы не подтверждена.')
    article = {'title': snapshot.title, 'base_revid': snapshot.revid, 'subject_scope':'football_statistics', 'player': mapping['wiki_name'], 'operations': operations}
    from .sofascore_national_cycle import prepare_nationals
    if statistics_since is not None:
        # National match lists have their own evidence budget; old club
        # reconstruction cannot consume it in the bounded season mode.
        match_budget={'remaining':max_match_requests}
    national_operations,national_issues,national_scopes=prepare_nationals(
        snapshot,mapping,history,api,wiki,match_budget,now,goalkeeper=goalkeeper,create_tables=statistics_since is not None)
    from .dfb_youth_cycle import prepare_youth_nationals
    youth_operations,youth_issues,youth_scopes=prepare_youth_nationals(
        snapshot,{**mapping,'source_name':profile.get('name')},api,wiki,now,goalkeeper=goalkeeper)
    from .national_card_coverage import close_national_card
    complete_operations,national_card_complete=close_national_card(
        snapshot,mapping,national_operations+youth_operations,national_scopes+youth_scopes,now)
    operations.extend(complete_operations)
    if national_card_complete:
        national_issues=[i for i in national_issues if i['code']!='national_global_date_incomplete_coverage']
    elif national_scopes:
        source_warnings.append('Общая дата статистики сборных сохранена: полный охват всех сборных ещё не подтверждён.')
    issues.extend(national_issues)
    issues.extend(youth_issues)
    national_issues.extend(youth_issues)
    if any(i['code']!='national_global_date_incomplete_coverage' for i in national_issues):
        source_warnings.append('Часть статистики сборных не подтверждена полной сверкой матчей. Неподтверждённые показатели сохранены; причины указаны в отчёте.')
    if source_warnings:
        article['review_warnings'] = source_warnings
    if any(f['code']=='match_request_cap' for f in match_budget.get('failures',[])):
        source_warnings.append('Часть пересчётов остановлена внутренним лимитом обращений к матчевой статистике, включая сохранённые ответы. Это ограничение адаптера, а не доказательство отсутствия данных в API.')
        article['review_warnings']=source_warnings
    if not operations:
        article['collection_result'] = 'no_confirmed_changes'
    package = {'schema_version': '1.1', 'package_id': 'sofascore-'+str(snapshot.revid),
               'generated_at': now.isoformat(), 'articles': [article]}
    validate_package(package)
    if operations:
        plan = plan_article(snapshot, article, '1.1', resolver)
        for change in plan.changes:
            provenance = sorted({s['provenance'] for s in change.operation.get('sources', [])})
            change.source_verification = ('existing_table_arithmetic' if change.operation['type']=='update_totals'
                                          else '+'.join(provenance)+'_collected_scope_checked')
            if change.status=='ready':
                change.message = ('Итоги пересчитаны по имеющимся строкам таблицы; полнота карьеры не подтверждается'
                                  if change.operation['type']=='update_totals' else
                                  'Структура и показатели сверены с источниками; требуется просмотр diff')
    else:
        plan = ArticlePlan(snapshot, [], snapshot.text, '', source_warnings, article=article, schema_version='1.1')
        plan.content_fingerprint = plan_fingerprint(plan)
    # The adapter prepares review diffs; automatic mainspace source attestation
    # is deliberately not fabricated by a manually prepared sandbox plan.
    return package, plan, {'provider': 'sofascore', 'player_id': mapping['api_id'],
        'collection_mode':'season_aggregates' if statistics_since is not None else 'match_verified',
        'statistics_since':statistics_since,'old_seasons_policy':'preserve_existing' if statistics_since is not None else 'verify_full_history',
        'confirmed_match_date': confirmed, 'covered_statistics': len(records), 'review': issues,
        'match_recovery_failures':match_budget.get('failures',[]),
        'national_scopes':national_scopes,
        'youth_national_scopes':youth_scopes,
        'secondary_club_scopes':secondary_scopes,
        'quota': api.quota_status()}
