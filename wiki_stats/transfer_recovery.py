# SPDX-License-Identifier: MIT
"""Recover missing permanent club stints; uncertain histories never become edits."""
from datetime import date, datetime, timezone
import re
from .errors import UpdateError
from .source_validation import response_rows
from .statistics_engine import pair_valid
from .wikitext_parser import WikitextParser
from .structural_editor import validate_period, is_active_period
from .club_registry import ClubRegistry
from .api_football_client import utcnow
from .transactions import digest
from .schema_validator import validate_package
from .change_planner import plan_article
from .entity_resolver import EntityResolver, reference_identity


def history_fingerprint(history):
    return digest(sorted((t['date'],t['teams']['out']['id'],t['teams']['in']['id'],str(t.get('type') or '')) for t in history))


def permanent_transfer(kind):
    if not isinstance(kind,str): return False
    return kind.strip().casefold()=='free' or bool(re.fullmatch(r'[€£$]\s*[0-9]+(?:[.,][0-9]+)?\s*[KMB]?',kind.strip(),re.I))


def received_at(value):
    try:
        instant = datetime.fromisoformat(value.replace('Z','+00:00'))
        if instant.tzinfo is None or not 0 <= (datetime.now(timezone.utc)-instant).total_seconds() <= 86400:
            raise ValueError()
        return instant
    except (ValueError,TypeError,AttributeError):
        raise UpdateError('source_stale','Ответ источника должен иметь подтверждённое время получения за последние сутки') from None


def missing_stints(history, initial_id, start_year, current_id, as_of):
    history = sorted((t for t in history if t['date'] <= as_of), key=lambda t:t['date'])
    joins = [t for t in history if t['teams']['in']['id'] == initial_id
             and date.fromisoformat(t['date']).year == start_year]
    if len(joins) != 1:
        raise UpdateError('needs_review','Не подтверждена точная дата начала последней строки карьеры')
    if not permanent_transfer(joins[0].get('type')):
        raise UpdateError('needs_review','Исходная строка не подтверждена как постоянный переход')
    joined = joins[0]['date']
    if any(t['date']<joined and t['teams']['in']['id']==initial_id for t in history):
        raise UpdateError('needs_review','Прежнее пребывание в исходном клубе требует разделения сезонных сумм')
    events = [t for t in history if t['date'] >= joined and t is not joins[0]]
    if len({t['date'] for t in events}) != len(events):
        raise UpdateError('needs_review','Несколько переходов в один день требуют проверки порядка')
    stints, team, begin = [], initial_id, joined
    for event in events:
        if event['teams']['out']['id'] != team:
            raise UpdateError('needs_review','История переходов разорвана; промежуточный клуб не подтверждён')
        kind = event.get('type')
        if not permanent_transfer(kind):
            raise UpdateError('needs_review','Аренда, возврат или неизвестный тип перехода требуют отдельной проверки')
        target = event['teams']['in']['id']
        stints.append({'team_id':team,'start':begin,'end':event['date']})
        team, begin = target, event['date']
    if not stints or team != current_id:
        raise UpdateError('needs_review','История не приводит из старого клуба в подтверждённый текущий состав')
    stints.append({'team_id':team,'start':begin,'end':None})
    if len({s['team_id'] for s in stints}) != len(stints):
        raise UpdateError('needs_review','Возвращение в прежний клуб: сезонные суммы нельзя разделить между периодами')
    return stints


def collect_statistics(api, player_id, birth_date, stints, as_of, *, fresh=False):
    """Sum league-only season records overlapping each verified stint.

    League calendars distinguish a summer move from the preceding season.
    Missing records are unknown, including a player with no recorded appearances.
    """
    collected, timestamps, player_cache, urls = [], [], {}, set()
    for stint in stints:
        total = {'appearances':0,'goals':0}
        season_stats = {}
        covered = False
        first, last = date.fromisoformat(stint['start']), date.fromisoformat(stint['end'] or as_of)
        if last < first or last.year-first.year > 30:
            raise UpdateError('needs_review','Период истории некорректен или слишком велик для одного сбора')
        for season in range(first.year-1,last.year+1):
            metadata = api.team_leagues(stint['team_id'],season,fresh=fresh)
            urls.add(f"https://v3.football.api-sports.io/leagues?team={stint['team_id']}&season={season}&type=league")
            timestamps.append(metadata['fetched_at'])
            leagues = []
            for item in response_rows(metadata):
                try:
                    league = item['league']
                    if league['type'] != 'League':
                        continue
                    matching = [s for s in item['seasons'] if s['year'] == season]
                    if len(matching) != 1 or type(league['id']) is not int:
                        raise ValueError()
                    calendar = matching[0]
                    start, end = date.fromisoformat(calendar['start']), date.fromisoformat(calendar['end'])
                    if end < start:
                        raise ValueError()
                    # The departure day belongs to the new stint.
                    if end < first or start >= last and stint['end'] is not None or start > last:
                        continue
                    if calendar.get('coverage',{}).get('players') is not True:
                        raise UpdateError('incomplete_statistics','Нет подтверждённого покрытия игроков исторического чемпионата')
                    leagues.append(league['id'])
                except (KeyError,TypeError,ValueError):
                    raise UpdateError('api_invalid_response','Исторический чемпионат не подтверждает календарь и сезон') from None
            if not response_rows(metadata):
                raise UpdateError('incomplete_statistics','Нет календаря чемпионата клуба; отсутствие ответа не является нулём')
            if not leagues:
                continue
            if len(leagues) != 1:
                raise UpdateError('needs_review','Несколько чемпионатов клуба в одном сезоне нельзя автоматически объединить')
            if season not in player_cache:
                player_cache[season] = api.player_statistics(player_id,season,fresh=fresh)
                urls.add(f'https://v3.football.api-sports.io/players?id={player_id}&season={season}')
            record = player_cache[season]
            timestamps.append(record['fetched_at'])
            rows = response_rows(record)
            try:
                if len(rows)!=1 or rows[0]['player']['id'] != player_id or rows[0]['player']['birth']['date'] != birth_date:
                    raise ValueError()
                selected = [s for s in rows[0]['statistics'] if s['team']['id'] == stint['team_id']
                            and s['league']['id'] == leagues[0] and s['league']['season'] == season]
                if len(selected)!=1:
                    raise UpdateError('incomplete_statistics','Нет единственной сезонной записи игрока за промежуточный клуб')
                pair = {'appearances':selected[0]['games'].get('appearences'),'goals':selected[0]['goals'].get('total')}
                if not pair_valid(pair):
                    raise UpdateError('incomplete_statistics','Неизвестные матчи или голы периода нельзя заменить нулями')
            except (KeyError,TypeError,ValueError,AttributeError):
                raise UpdateError('api_invalid_response','Исторические числа не подтверждают игрока, клуб или сезон') from None
            for key in total:
                total[key] += pair[key]
            season_stats[str(season)] = pair
            covered = True
        if not covered or not pair_valid(total):
            raise UpdateError('incomplete_statistics','Полные числа периода не получены; строки с вымышленными нулями не создаются')
        collected.append({**stint,'stats':total,'season_stats':season_stats})
    times = [received_at(t) for t in timestamps]
    return collected, min(times).isoformat(), sorted(urls)


def recovery_plan(store, api, wiki, mapping, snapshot, current_id, history, as_of):
    parser = WikitextParser(snapshot.text)
    if any(t.name=='клстат' for t in parser.templates):
        raise UpdateError('needs_review','Карточка с сезонной таблицей требует согласованного восстановления таблицы и карьеры')
    box = parser.infobox(mapping['wiki_name'])
    rows = parser.career('клубы',mapping['wiki_name'])
    if not rows or 'нынешний клуб' not in box:
        raise UpdateError('needs_review','Нет исходной строки и текущего клуба для восстановления истории')
    period, old_team, old_numbers = rows[-1]
    if not is_active_period(period):
        raise UpdateError('needs_review','Последняя строка старой карточки уже закрыта')
    if any(is_active_period(p) for p,_,_ in rows[:-1]):
        raise UpdateError('needs_review','Несколько действующих периодов карьеры требуют проверки')
    initial_page = reference_identity(wiki,old_team)
    if reference_identity(wiki,box['нынешний клуб'].text) != initial_page:
        raise UpdateError('needs_review','Текущий клуб и последняя строка старой карточки противоречат друг другу')
    initial = [c for _,c in store.items('clubs') if c['qid']==initial_page['qid'] and c['title']==initial_page['title']]
    if len(initial)!=1:
        raise UpdateError('club_unmapped','API ID старого клуба ещё не сопоставлен с русской статьёй')
    stints = missing_stints(history,initial[0]['api_id'],validate_period(period),current_id,as_of)
    resolved = {}
    for stint in stints:
        _, resolved[stint['team_id']] = ClubRegistry(store).verify(stint['team_id'],wiki)
    statistics, fetched_at, urls = collect_statistics(api,mapping['api_id'],mapping['birth_date'],stints,as_of)
    old_pair = dict(zip(('appearances','goals'),[n.value for n in old_numbers.numeric(pair=True)]))
    if any(statistics[0]['stats'][k] < old_pair[k] for k in old_pair):
        raise UpdateError('needs_review','Исторический итог старого клуба меньше чисел в статье')
    group = 'history-' + digest([mapping['api_id'],stints])[:16]
    sources = [{'url':f"https://v3.football.api-sports.io/transfers?player={mapping['api_id']}",
                'provenance':'api_football','coverage_as_of':as_of,
                'note':'Цепочка постоянных переходов; числа каждого периода получены отдельно из чемпионатов.'}]
    for url in urls:
        sources.append({'url':url,
                        'provenance':'api_football','coverage_as_of':as_of,'note':'Проверка сезонных чисел по клубу и чемпионату.'})
    operations = []
    def operation(kind, entity, target, **fields):
        operations.append({'id':f'{group}-{len(operations)}','group_id':group,'type':kind,
                           'entity':entity,'target':target,'as_of':as_of,'sources':sources,**fields})
    original = {'name':resolved[stints[0]['team_id']].display,'wikitext':old_team}
    target = {'structure':'career','field':'клубы','period':period}
    if old_pair != statistics[0]['stats']:
        operation('update_stats',original,target,season=None,
                  competition={'name':'Чемпионат','category':'all','scope':'league'},expected=old_pair,new=statistics[0]['stats'])
    closed = f"{validate_period(period)}—{date.fromisoformat(stints[0]['end']).year}"
    operation('update_career_period',original,target,payload={'period':closed})
    previous = {'wikitext':old_team,'period':closed}
    expected_current = box['нынешний клуб'].text.strip()
    for stint in statistics[1:]:
        club = resolved[stint['team_id']]
        end = str(date.fromisoformat(stint['end']).year) if stint['end'] else '{{н.в.}}'
        new_period = f"{date.fromisoformat(stint['start']).year}—{end}"
        entity = {'name':club.display,'kind':'club','wikidata_id':club.qid,'page_title':club.title,'display_name':club.display}
        operation('add_club',entity,{'structure':'career','field':'клубы'},
                  payload={'period':new_period,'role':'primary','stats':stint['stats'],'previous':previous})
        if stint['end'] is None:
            operation('update_current_club',entity,{'structure':'career','field':'клубы'},expected=expected_current,payload={})
        previous = {'wikitext':club.wikitext,'period':new_period}
    article = {'title':snapshot.title,'player':mapping['wiki_name'],'subject_scope':'bundesliga',
               'base_revid':snapshot.revid,'operations':operations}
    package = {'schema_version':'1.1','package_id':group,'generated_at':utcnow(),'articles':[article]}
    validate_package(package)
    plan = plan_article(snapshot,article,'1.1',EntityResolver(wiki))
    if plan.warnings or any(c.status not in {'ready','already_applied'} for c in plan.changes):
        reasons = [c.message for c in plan.changes if c.status not in {'ready','already_applied'}] + plan.warnings
        raise UpdateError('needs_review','Восстановление цепочки не прошло проверки редактора: ' + '; '.join(reasons))
    plan.publishable = bool(plan.diff)
    evidence = {'kind':'transfer','league':78,'fetched_at':fetched_at,'player_id':mapping['api_id'],
                'mapping_hash':digest(mapping),'club_hashes':{str(tid):digest(ClubRegistry(store).get(tid)) for tid in resolved},
                'history_hash':history_fingerprint(history),'stints':statistics,'as_of':as_of}
    return plan, package, evidence
