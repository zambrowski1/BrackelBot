# SPDX-License-Identifier: MIT
import re
import mwparserfromhell as mw
from .entity_resolver import reference_identity
from .errors import UpdateError
from .national_team_identity import valid_qid
from .sfv_youth_profile import parse_keeper_youth
from .structural_editor import validate_period
from .wikitext_parser import WikitextParser


def plain_swiss_age(text, age):
    code = mw.parse(text)
    if code.filter_wikilinks() or any(str(t.name).strip().casefold() != 'флаг швейцарии' for t in code.filter_templates()):
        return False
    return re.fullmatch(r'Швейцария\s*\(до\s+' + str(age) + r'(?:\s+(?:лет|года))?\)', code.strip_code().strip()) is not None


def prepare_swiss_youth(snapshot, mapping, bundle, wiki, now):
    operations, issues = [], []
    try:
        scopes = parse_keeper_youth(mapping, bundle, now)
        career = WikitextParser(snapshot.text).career('национальная сборная', mapping['wiki_name'])
        for scope in scopes:
            age = scope['age']; selected = []; ru = None
            for row in career:
                if plain_swiss_age(row[1], age):
                    selected.append(row)
                elif '(до ' + str(age) + ')' in row[1]:
                    title = f'Сборная Швейцарии по футболу (до {age} ' + ('года)' if age == 21 else 'лет)')
                    try:
                        ru = wiki.get_page_identity(title)
                        if not valid_qid(ru.get('qid')):
                            raise UpdateError('national_identity_mismatch','Не подтверждена возрастная сборная')
                        if reference_identity(wiki, row[1]).get('qid') == ru['qid']: selected.append(row)
                    except UpdateError as exc:
                        issues.append({'code':exc.code,'age':age,'message':str(exc)})
            if len(selected) != 1:
                issues.append({'code':'national_career_row_unproven','age':age}); continue
            period, text, value = selected[0]
            if validate_period(period) > int(scope['first_date'][:4]) or not re.fullmatch(r'20[0-9]{2}(?:—20[0-9]{2})?', period):
                issues.append({'code':'national_career_period_unproven','age':age}); continue
            if int(period[-4:]) < int(scope['statistics_as_of'][:4]):
                issues.append({'code':'national_career_period_unproven','age':age}); continue
            old = dict(zip(('appearances','goals'), [n.value for n in value.numeric(pair=True)]))
            title = f'Сборная Швейцарии по футболу (до {age} ' + ('года)' if age == 21 else 'лет)')
            try:
                ru = ru or wiki.get_page_identity(title)
                en = wiki.get_foreign_page_identity(f'Switzerland national under-{age} football team','en')
                if ru.get('title') != title or not valid_qid(ru.get('qid')) or ru['qid'] != en.get('qid'):
                    raise UpdateError('national_identity_mismatch','Не подтверждена швейцарская возрастная сборная')
            except UpdateError as exc:
                issues.append({'code':exc.code,'age':age,'message':str(exc)})
                continue
            if validate_period(period) == int(scope['first_date'][:4]) and int(period[-4:]) == int(scope['statistics_as_of'][:4]):
                scope['career_binding'] = {'period': period, 'team': text, 'qid': ru['qid'],
                    'numbers': old, 'verified_period': period}
            if old == scope['numbers']: continue
            operations.append({'id':'sfv-youth-card-'+str(age),'type':'update_stats','group_id':'sfv-youth-'+str(age),
                'entity':{'name':title,'kind':'national_team','age':age,'wikidata_id':ru['qid'],'page_title':title,'wikitext':text},
                'target':{'structure':'career','field':'национальная сборная','period':period},
                'season':None,'competition':{'name':'Матчи сборной','category':'all','scope':'national_all'},
                'as_of':scope['statistics_as_of'],
                'sources':[{'url':url,'provenance':'uefa' if url.startswith('https://www.uefa.com/') else 'sfv',
                            'coverage_as_of':scope['statistics_as_of'],
                            'note':'Все выступления и полные минуты в воротах сверены с суммой SFV; голы соперников подсчитаны только в сыгранных матчах'}
                           for url in scope['sources']],
                'expected':old,'new':scope['numbers']})
        return operations, issues, scopes
    except UpdateError as exc:
        return [], [{'code':exc.code,'message':str(exc)}], []
