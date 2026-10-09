# SPDX-License-Identifier: MIT
"""Collect a verified player's evidence and prepare one reviewable article diff.

Only explicitly supported competition scopes are reconciled. Missing rows do
not mean zero, and aggregate statistics for multiple clubs are never allocated.
"""
from datetime import datetime, timezone, date
from copy import deepcopy
import re

from .errors import UpdateError
from .models import ArticlePlan
from .wikitext_parser import WikitextParser
from .entity_resolver import reference_identity
from .player_registry import check_player_identity
from .structural_editor import is_active_period, validate_period
from .change_planner import plan_article
from .schema_validator import validate_package
from .transactions import TEST_TITLE, plan_fingerprint

# The regularSeason query keeps relegation playoffs out of infobox totals.
LEAGUES = {35, 44, 491, 42, 493}
CUPS = {217}
LEAGUE_LINKS = {35: '[[Чемпионат Германии по футболу|Бундеслига]]',
                44: '[[Вторая Бундеслига]]',
                491: '[[Третья лига Германии по футболу|Третья лига]]',
                42: '[[Региональная лига «Север»]]',
                493: '[[Региональная лига «Запад»]]'}


def season_name(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{2}/\d{2}|20\d{2}/\d{2}', value):
        raise UpdateError('season_invalid', 'Неоднозначный сезон Sofascore')
    start = int(value.split('/')[0])
    start = 2000+start if start < 100 else start
    if int(value.split('/')[1]) != (start+1) % 100:
        raise UpdateError('season_invalid', 'Непоследовательные годы сезона')
    return f'{start}/{(start+1)%100:02}'


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
        if round_.get('cup_round_type') or any('play' in str(round_.get(k, '')).lower()
                                             for k in ('name', 'slug')):
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


def _numbers(response, pid, tid, sid, part):
    if (response.get('player_id'), response.get('tournament_id'), response.get('season_id'), response.get('type')) != (pid, tid, sid, part):
        raise UpdateError('source_scope_mismatch', 'Статистика относится к другому игроку, сезону или этапу')
    team = response.get('team', {})
    if (type(team.get('id')) is not int or team.get('is_national') is not False
            or team.get('sport', {}).get('slug') != 'football'):
        raise UpdateError('club_ambiguous', 'Статистика не относится к единственному футбольному клубу')
    stats = response.get('statistics', {})
    values = {k: stats.get(k) for k in ('appearances', 'goals')}
    if any(type(v) is not int or not 0 <= v <= 10000 for v in values.values()):
        raise UpdateError('incomplete_statistics', 'Не подтверждены игры и голы')
    return team, values


def prepare_player(snapshot, mapping, api, wiki, resolver, *, max_requests=24, max_match_pages=12):
    now = datetime.now(timezone.utc)
    day = now.date().isoformat()
    profile = api.details(mapping['api_id'])
    if profile.get('date_of_birth') != mapping['birth_date']:
        raise UpdateError('identity_mismatch', 'Дата рождения Sofascore изменилась')
    check_player_identity(wiki, mapping)
    if snapshot.title not in {mapping['title'], TEST_TITLE}:
        raise UpdateError('identity_mismatch', 'Целевая страница не соответствует связи игрока')
    parser = WikitextParser(snapshot.text)
    box = parser.infobox(mapping['wiki_name'])
    # The sandbox must actually contain the registered player's birth date too.
    if snapshot.title == TEST_TITLE:
        birthday = box.get('дата рождения')
        expected = date.fromisoformat(mapping['birth_date']).strftime('%d.%m.%Y')
        if birthday is None or birthday.text.strip() not in {expected, mapping['birth_date']}:
            raise UpdateError('identity_mismatch', 'Тестовая карточка содержит другую дату рождения')
    issues, operations, records = [], [], []
    base = {'as_of': day, 'group_id': 'sofascore-reconciliation', 'sources': [{
        'url': 'https://www.sofascore.com/football/player/'+str(mapping['api_id']),
        'provenance': 'sofascore', 'coverage_as_of': day,
        'note': 'Получено через Sofascore Scraper; дата получения не является датой матча'}]}

    def add(kind, entity, target, **fields):
        operations.append({**deepcopy(base), 'id': 'ss-'+str(len(operations)+1),
                           'type': kind, 'entity': deepcopy(entity), 'target': target, **fields})

    def resolve_team(team):
        resolved = resolver.resolve({'name': team['name'], 'kind': 'club'})
        return {'name': resolved.display, 'kind': 'club', 'wikidata_id': resolved.qid,
                'page_title': resolved.title, 'wikitext': resolved.wikitext}, resolved

    current = profile.get('team', {})
    if current.get('is_national') is not False or current.get('sport', {}).get('slug') != 'football':
        raise UpdateError('club_ambiguous', 'Не подтверждён текущий клуб')
    entity, resolved_current = resolve_team(current)
    index = api.seasons(mapping['api_id'])
    if index.get('player_id') != mapping['api_id'] or not isinstance(index.get('tournament_seasons'), list):
        raise UpdateError('incomplete_statistics', 'Нет списка сезонов игрока')
    table = parser.club_table() if any(t.name == 'клстат' for t in parser.templates) else None
    cup_category = None
    if table:
        supported = {'Национальный кубок', '[[Кубок Германии по футболу|Кубок Германии]]', 'Кубок Германии'}
        matches = [c for c in table.categories if c in supported]
        cup_category = matches[0] if len(matches) == 1 else None
    seen = set()
    queries = []
    for block in index['tournament_seasons']:
        tid = block.get('tournament', {}).get('id')
        if tid not in LEAGUES | CUPS:
            issues.append({'code': 'competition_unmapped', 'tournament_id': tid})
            continue
        for season in block.get('seasons', []):
            label = season_name(season.get('year'))
            sid = season.get('id')
            if type(sid) is not int or (tid, sid) in seen:
                raise UpdateError('ambiguous_target', 'Повторные или неверные сезоны API')
            seen.add((tid, sid))
            part = 'regularSeason' if tid in LEAGUES else 'overall'
            queries.append((label, tid, sid, part))
    # Newest evidence first; the cap is a review boundary, never zero evidence.
    queries.sort(reverse=True)
    for label, tid, sid, part in queries[:max_requests]:
        try:
            try:
                response = api.statistics(mapping['api_id'], tid, sid, part)
            except UpdateError as exc:
                if part!='regularSeason' or exc.code!='api_not_found':
                    raise
                part = 'overall'
                response = api.statistics(mapping['api_id'], tid, sid, part)
            team, numbers = _numbers(response, mapping['api_id'], tid, sid, part)
            ent, resolved = resolve_team(team)
            records.append({'season': label, 'tournament_id': tid, 'season_id': sid,
                'team_id': team['id'], 'entity': ent, 'resolved': resolved, 'numbers': numbers,
                'category': 'Чемпионат' if tid in LEAGUES else cup_category, 'part': part})
        except UpdateError as exc:
            issues.append({'code': exc.code, 'season': label, 'tournament_id': tid})
            if exc.code in {'quota_exhausted', 'api_unavailable'}:
                break
    if len(queries) > max_requests:
        issues.append({'code': 'collection_capped', 'pending': len(queries)-max_requests})
    try:
        history = api.matches(mapping['api_id'], max_pages=max_match_pages)
    except UpdateError as exc:
        issues.append({'code': exc.code})
        history = {'complete': False, 'matches': []}
    verified = []
    for record in records:
        if record['tournament_id'] not in LEAGUES or record['part']=='regularSeason':
            verified.append(record)
            continue
        # Some provider versions expose only overall. Accept this fallback only
        # if complete match history proves it contains exactly league games.
        scoped = [m for m in history['matches'] if
                  ((m.get('player',{}) or {}).get('team_id'),(m.get('tournament',{}) or {}).get('id'),(m.get('season',{}) or {}).get('id'))
                  == (record['team_id'],record['tournament_id'],record['season_id'])
                  and m.get('is_finished') is True and (m.get('status',{}) or {}).get('type')=='finished'
                  and isinstance(((m.get('player',{}) or {}).get('statistics',{}) or {}).get('minutes_played'), (int,float))
                  and m['player']['statistics']['minutes_played']>0]
        playoffs = any((m.get('round') or {}).get('cup_round_type')
                       or any('play' in str((m.get('round') or {}).get(k,'')).lower() for k in ('name','slug'))
                       for m in scoped)
        if history['complete'] and not playoffs and len(scoped)==record['numbers']['appearances'] and scoped:
            verified.append(record)
        else:
            issues.append({'code':'overall_league_unproven','season':record['season'],
                           'tournament_id':record['tournament_id']})
    records = verified

    identities = {}
    def row_entity(text):
        if text in identities:
            return identities[text]
        try:
            identities[text] = reference_identity(wiki, text)['qid']
            return identities[text]
        except UpdateError:
            return None

    if table:
        for row in table.seasons:
            qid = row_entity(row.club)
            for category in table.categories:
                covered = [r for r in records if r['season'] == row.season
                           and r['resolved'].qid == qid and r['category'] == category]
                if len(covered) != 1:
                    issues.append({'code': 'category_not_covered', 'club': row.club,
                                   'season': row.season, 'category': category})
                    continue
                i = table.categories.index(category)
                old = {k: row.values[str(2+2*i+j)].numeric()[0].value
                       for j, k in enumerate(('appearances', 'goals'))}
                new = covered[0]['numbers']
                if old != new:
                    if not any(op['type'] == 'update_totals' for op in operations):
                        add('update_totals', {'name': 'КлСтат', 'wikitext': '{{КлСтат}}'},
                            {'structure': 'club_table'}, payload={'complete': True})
                    add('update_stats', {'name': covered[0]['entity']['name'], 'wikitext': row.club},
                        {'structure': 'club_table'}, season=row.season,
                        competition={'name': category, 'category': category, 'scope': 'category'},
                        expected=old, new=new)
        # New rows need explicit evidence for every column; no inferred zeros.
        covered_rows = {(r['resolved'].qid, r['season']) for r in records}
        existing = {(row_entity(r.club), r.season) for r in table.seasons}
        for qid, label in sorted(covered_rows-existing):
            selected = [r for r in records if r['resolved'].qid == qid and r['season']==label]
            categories = {c: [r for r in selected if r['category']==c] for c in table.categories}
            league = [r for r in selected if r['tournament_id'] in LEAGUES]
            if len(league)!=1 or any(len(rows)!=1 for rows in categories.values()):
                issues.append({'code': 'new_row_needs_all_categories', 'qid': qid, 'season': label})
                continue
            blocks = [r for r in table.seasons if row_entity(r.club)==qid]
            ent = deepcopy(league[0]['entity'])
            payload = {'complete': True, 'categories': {c: rows[0]['numbers'] for c, rows in categories.items()},
                       'league_wikitext': LEAGUE_LINKS[league[0]['tournament_id']]}
            if blocks:
                ent['wikitext'] = blocks[0].club
                earlier = [r for r in blocks if r.season < label]
                payload.update({'insert_after_season': earlier[-1].season} if earlier
                               else {'insert_before_season': blocks[0].season})
                add('add_season', ent, {'structure': 'club_table'}, season=label, payload=payload)
            else:
                payload['insert_after_club'] = table.seasons[-1].club
                add('add_table_club', ent, {'structure': 'club_table'}, season=label, payload=payload)

    # Infobox totals require consecutive league seasons for one unbroken stint.
    transfers = api.transfers(mapping['api_id'])
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
        if sorted(years) != list(range(first, last+1)):
            return None
        return {k: sum(r['numbers'][k] for r in rows) for k in ('appearances', 'goals')}

    current_rows = [r for r in career if row_entity(r[1]) == resolved_current.qid and is_active_period(r[0])]
    club_consistent = False
    if len(current_rows) == 1:
        period, text, value = current_rows[0]
        first = validate_period(period)
        league_records = [r for r in records if r['team_id'] == current['id'] and r['tournament_id'] in LEAGUES]
        last = max((int(r['season'][:4]) for r in league_records), default=first-1)
        new = total(resolved_current.qid, first, last)
        if new:
            old = dict(zip(('appearances', 'goals'), [n.value for n in value.numeric(pair=True)]))
            if old != new:
                add('update_stats', {'name': entity['name'], 'wikitext': text},
                    {'structure': 'career', 'field': 'клубы', 'period': period}, season=None,
                    competition={'name': 'Чемпионат', 'category': 'all', 'scope': 'league'}, expected=old, new=new)
        else:
            issues.append({'code': 'career_incomplete', 'club': current['id']})
        club_consistent = row_entity(box.get('нынешний клуб').text) == resolved_current.qid if box.get('нынешний клуб') else False
    elif not current_rows and arrival:
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
            new_total = total(resolved_current.qid, year, year)
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
        old = box['номер'].numeric()[0].value
        # If the already-current club uses a redirect/alternative display, the
        # editor still requires its canonical, independently checked identity.
        if box['нынешний клуб'].text.strip() == entity['wikitext'] or any(o['type']=='update_current_club' for o in operations):
            if old != number:
                add('update_shirt_number', entity, {'structure': 'career', 'field': 'клубы'},
                    payload={'expected_number': old, 'number': number})
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
            last = max((int(r['season'][:4]) for r in records
                        if r['resolved'].qid==row_entity(team) and r['tournament_id'] in LEAGUES), default=-1)
        else:
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
        if old_day and confirmed < old_day.isoformat():
            issues.append({'code': 'date_evidence_older', 'confirmed_match_date': confirmed, 'article_date': old_day.isoformat()})
        elif old_day and confirmed > old_day.isoformat() and coverage_complete:
            add('update_date', entity, {'structure': 'career', 'field': 'клубы'},
                payload={'field': 'обновление данных о клубе', 'expected_value': old.text.strip(),
                         'statistics_as_of': confirmed})
        elif not coverage_complete:
            issues.append({'code': 'global_date_incomplete_coverage', 'confirmed_match_date': confirmed})
    else:
        issues.append({'code': 'match_date_unconfirmed'})
    if not table:
        league_records = [r for r in records if r['tournament_id'] in LEAGUES]
        if confirmed and league_records:
            clubs = {}
            for record in sorted(league_records,key=lambda r:r['season']):
                qid = record['resolved'].qid
                block = clubs.setdefault(qid, {'entity': record['entity'], 'rows': []})
                block['rows'].append({'season': record['season'],
                    'league_wikitext': LEAGUE_LINKS[record['tournament_id']],
                    'categories': {'Чемпионат': record['numbers']}})
            add('create_statistics_section', {'name': 'КлСтат', 'wikitext': '{{КлСтат}}'},
                {'structure': 'club_table'}, payload={'complete': True, 'full_career': False,
                'statistics_as_of': confirmed, 'categories': ['Чемпионат'], 'clubs': list(clubs.values())})
            issues.append({'code': 'partial_section', 'message': 'Только подтверждённые чемпионаты; полный итог карьеры не заявляется'})
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
    article = {'title': snapshot.title, 'base_revid': snapshot.revid, 'player': mapping['wiki_name'], 'operations': operations}
    package = {'schema_version': '1.1', 'package_id': 'sofascore-'+str(snapshot.revid),
               'generated_at': now.isoformat(), 'articles': [article]}
    if operations:
        validate_package(package)
        plan = plan_article(snapshot, article, '1.1', resolver)
        for change in plan.changes:
            change.source_verification = 'sofascore_collected_scope_checked'
            if change.status=='ready':
                change.message = 'Структура и показатели сверены с Sofascore; требуется просмотр diff'
    else:
        plan = ArticlePlan(snapshot, [], snapshot.text, '', [], article=article, schema_version='1.1')
        plan.content_fingerprint = plan_fingerprint(plan)
    # The adapter prepares review diffs; automatic mainspace source attestation
    # is deliberately not fabricated by a manually prepared sandbox plan.
    return package, plan, {'provider': 'sofascore', 'player_id': mapping['api_id'],
        'confirmed_match_date': confirmed, 'covered_statistics': len(records), 'review': issues,
        'quota': api.quota_status()}
