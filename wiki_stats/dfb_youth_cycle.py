# SPDX-License-Identifier: MIT
"""Prepare youth card changes only from complete, identity-bound DFB lists."""
from .dfb_youth_profile import parse_closed_youth
from .entity_resolver import reference_identity
from .errors import UpdateError
from .national_team_identity import valid_qid
from .structural_editor import validate_period
from .wikitext_parser import WikitextParser

TITLES = {
    19: ('Сборная Германии по футболу (до 19 лет)', 'Deutsche Fußballnationalmannschaft (U-19-Junioren)'),
    20: ('Сборная Германии по футболу (до 20 лет)', 'Germany national under-20 football team'),
    21: ('Сборная Германии по футболу (до 21 года)', 'Germany national under-21 football team'),
    23: ('Олимпийская сборная Германии по футболу', 'Germany Olympic football team'),
}


def prepare_youth_nationals(snapshot, mapping, api, wiki, now, *, goalkeeper=False):
    operations, issues, scopes = [], [], []
    if not hasattr(api, 'national_secondary_source'):
        return operations, issues, scopes
    bundle = api.national_secondary_source(mapping['api_id'])
    if bundle is None:
        return operations, issues, scopes
    if isinstance(bundle,dict) and bundle.get('kind') == 'sfv_keeper':
        if not goalkeeper:
            return [], [{'code':'goal_metric_mismatch','message':'SFV вратаря нельзя применять к полевому игроку'}], []
        from .sfv_youth_cycle import prepare_swiss_youth
        return prepare_swiss_youth(snapshot,mapping,bundle,wiki,now)
    try:
        if goalkeeper:
            raise UpdateError('dfb_youth_keeper_unproven', 'Голы DFB не подтверждают пропущенные голы вратаря')
        scopes = parse_closed_youth(mapping, bundle, now)
        career = WikitextParser(snapshot.text).career('национальная сборная', mapping['wiki_name'])
        for scope in scopes:
            age = scope['age']; ru_title, en_title = TITLES[age]
            ru = wiki.get_page_identity(ru_title)
            en = wiki.get_foreign_page_identity(en_title, 'de' if age == 19 else 'en')
            if (ru.get('title') != ru_title or not valid_qid(ru.get('qid'))
                    or ru['qid'] != en.get('qid')):
                raise UpdateError('national_identity_mismatch', 'Не подтверждена возрастная сборная DFB')
            rows = []
            for row in career:
                try:
                    if reference_identity(wiki, row[1]).get('qid') == ru['qid']:
                        rows.append(row)
                except UpdateError:
                    continue
            if len(rows) != 1:
                issues.append({'code': 'national_career_row_unproven', 'age': age})
                continue
            period, text, value = rows[0]
            first, last = scope['first_date'][:4], scope['statistics_as_of'][:4]
            if validate_period(period) != int(first):
                issues.append({'code': 'national_career_period_unproven', 'age': age})
                continue
            new_period = first if first == last else first + '—' + last
            # Olympic participation is not age-limited: preserve its career period.
            if age == 23:
                new_period = period
            common = {'group_id': 'dfb-youth-' + str(age),
                      'entity': {'name': ru_title, 'page_title': ru_title, 'wikidata_id': ru['qid'],
                                 'kind': 'national_team', 'age': age if age != 23 else None, 'wikitext': text},
                      'as_of': scope['statistics_as_of'],
                      'sources': [{'url': url, 'provenance': 'dfb',
                                   'coverage_as_of': scope['statistics_as_of'],
                                   'note': 'Итог DFB сверён со всеми матчами возрастной сборной'}
                                  for url in scope['sources']]}
            if period != new_period:
                operations.append({**common, 'id': 'dfb-youth-period-' + str(age),
                    'type': 'update_career_period',
                    'target': {'structure': 'career', 'field': 'национальная сборная', 'period': period},
                    'payload': {'period': new_period}})
            old = dict(zip(('appearances', 'goals'), [n.value for n in value.numeric(pair=True)]))
            scope['career_binding'] = {'period': period, 'team': text, 'qid': ru['qid'],
                'numbers': old, 'verified_period': new_period}
            if old != scope['numbers']:
                operations.append({**common, 'id': 'dfb-youth-card-' + str(age), 'type': 'update_stats',
                    'target': {'structure': 'career', 'field': 'национальная сборная', 'period': new_period},
                    'season': None, 'competition': {'name': 'Матчи сборной', 'category': 'all', 'scope': 'national_all'},
                    'expected': old, 'new': scope['numbers']})
            from .dfb_youth_matches import full_youth_repair
            try:
                repair = full_youth_repair(snapshot, mapping, scope, common['entity'], bundle, wiki)
                if repair:
                    operations.append(repair)
            except UpdateError as exc:
                issues.append({'code': exc.code, 'age': age, 'message': str(exc)})
    except UpdateError as exc:
        issues.append({'code': exc.code, 'message': str(exc)})
        return [], issues, []
    return operations, issues, scopes
