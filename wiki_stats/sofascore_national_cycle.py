# SPDX-License-Identifier: MIT
"""Collect senior national totals and prepare source-bound review operations."""
import re
import mwparserfromhell as mw
from .errors import UpdateError
from .entity_resolver import reference_identity
from .national_team_identity import NationalTeamIdentity
from .sofascore_national import collect_national_scope
from .sofascore_sbmatches import full_repair,create_missing
from .wikitext_parser import WikitextParser
from .structural_editor import validate_period, is_active_period


def unlinked_youth_label(text):
    code=mw.parse(text)
    if code.filter_wikilinks() or any(not str(t.name).strip().casefold().startswith('флаг ')
                                    for t in code.filter_templates()):
        return False
    age=re.search(r'\(\s*до\s+([0-9]{1,2})(?:\s+(?:лет|года))?\s*\)\s*$',code.strip_code().strip())
    return age is not None and 1<=int(age[1])<=23


def prepare_nationals(snapshot, mapping, history, api, wiki, budget, now, *, goalkeeper=False,create_tables=False):
    operations, issues, scopes = [], [], []
    if not hasattr(api, 'national_statistics'):
        return operations, issues, scopes
    try:
        if budget['remaining'] <= 0:
            raise UpdateError('match_request_cap', 'Нет лимита для сверки сборных')
        budget['remaining'] -= 1
        aggregate = api.national_statistics(mapping['api_id'])
        if (not isinstance(aggregate, dict) or type(aggregate.get('player_id')) is not int
                or aggregate['player_id'] != mapping['api_id'] or not isinstance(aggregate.get('statistics'), list)):
            raise UpdateError('national_identity_mismatch', 'Не подтверждён игрок в статистике сборных')
        teams = [item.get('team') for item in aggregate['statistics'] if isinstance(item, dict)]
        if len(teams) != len(aggregate['statistics']) or any(not isinstance(t, dict) for t in teams):
            raise UpdateError('national_aggregate_unproven', 'Не подтверждены сборные игрока')
        ids = [t.get('id') for t in teams]
        if any(type(tid) is not int or tid <= 0 for tid in ids) or len(ids) != len(set(ids)):
            raise UpdateError('national_aggregate_unproven', 'Повторная сборная в статистике')
        identity = NationalTeamIdentity(wiki)
        career = WikitextParser(snapshot.text).career('национальная сборная', mapping['wiki_name'])
        for team in teams:
            # Youth scopes have different coverage and are never mapped by country code.
            try:
                entity = identity.resolve(team)
            except UpdateError as exc:
                issues.append({'code': exc.code, 'team_id': team.get('id'), 'message': str(exc)})
                continue
            scope = collect_national_scope(mapping['api_id'], team['id'], history, api, budget,
                                           now, goalkeeper=goalkeeper)
            if scope is None:
                issues.append({'code': 'national_scope_unproven', 'team_id': team['id']})
                continue
            scopes.append(scope)
            if not scope['statistics_as_of']:
                issues.append({'code': 'national_date_unproven', 'team_id': team['id']})
                continue
            sources = []
            for verified in scope['matches']:
                url = verified['match'].get('link')
                if not isinstance(url, str) or not url.startswith('https://www.sofascore.com/'):
                    raise UpdateError('national_match_unproven', 'Нет ссылки на подтверждённый матч')
                sources.append({'url': url, 'provenance': 'sofascore',
                    'coverage_as_of': scope['statistics_as_of'],
                    'note': 'Полная история участия сверена с итогом сборной; голы проверены по матчам'})
            matches=[];unresolved_adult=False
            for row in career:
                try:
                    if reference_identity(wiki,row[1]).get('qid')==entity['wikidata_id']:
                        matches.append(row)
                except UpdateError as exc:
                    if not unlinked_youth_label(row[1]):
                        unresolved_adult=True
                        issues.append({'code':'national_career_identity_unproven','team_id':team['id'],
                                       'period':row[0],'message':str(exc)})
            if len(matches) == 1 and not unresolved_adult:
                period, text, value = matches[0]
                first = validate_period(period)
                years = [int(v['date'][:4]) for v in scope['matches']]
                if not is_active_period(period) or min(years) < first:
                    issues.append({'code': 'national_career_period_unproven', 'team_id': team['id']})
                else:
                    old = dict(zip(('appearances', 'goals'), [n.value for n in value.numeric(pair=True)]))
                    if first == min(years):
                        scope['career_binding'] = {'period': period, 'team': text,
                            'qid': entity['wikidata_id'], 'numbers': old, 'verified_period': period}
                    if old != scope['numbers']:
                        operations.append({'id': 'national-card-'+str(team['id']), 'type': 'update_stats',
                            'group_id': 'full-national-'+str(team['id']),
                            'entity': {**{k: v for k, v in entity.items() if k != 'gen'}, 'wikitext': text},
                            'target': {'structure': 'career', 'field': 'национальная сборная', 'period': period},
                            'season': None, 'competition': {'name': 'Матчи сборной', 'category': 'all', 'scope': 'national_all'},
                            'as_of': scope['statistics_as_of'], 'sources': sources,
                            'expected': old, 'new': scope['numbers']})
            else:
                issues.append({'code': 'national_career_row_unproven', 'team_id': team['id']})
            try:
                repair = full_repair(snapshot, mapping, scope, wiki, goalkeeper=goalkeeper)
                if repair is None and create_tables:
                    repair=create_missing(snapshot,mapping,scope,wiki,goalkeeper=goalkeeper)
                if repair:
                    repair['id'] += '-'+str(team['id'])
                    repair['group_id'] = 'full-national-'+str(team['id'])
                    operations.append(repair)
            except UpdateError as exc:
                issues.append({'code': exc.code, 'team_id': team['id'], 'message': str(exc)})
        # The card date covers youth rows too. Senior totals cannot certify those rows.
        if scopes:
            issues.append({'code': 'national_global_date_incomplete_coverage'})
    except UpdateError as exc:
        if exc.code in {'quota_exhausted', 'api_rate_limited', 'api_authentication'}:
            budget['remaining'] = 0
        issues.append({'code': exc.code, 'message': str(exc)})
    return operations, issues, scopes
