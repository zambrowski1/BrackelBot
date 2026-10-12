# SPDX-License-Identifier: MIT
from copy import deepcopy
from datetime import datetime, timezone

import pytest

from tests.stage23_helpers import snapshot, MINI, U18
from tests.test_sofascore_national import NationalAPI
from wiki_stats.sofascore_national_cycle import prepare_nationals
from wiki_stats.structural_editor import execute_operation

NOW = datetime(2026, 10, 11, tzinfo=timezone.utc)
MAPPING = {'api_id': 1, 'wiki_name': 'Кальвин Браккельман'}
TEXT = MINI.replace('|2016—2017|', '|2020—{{н.в.}}|').replace(
    U18,
    '[[Сборная Германии по футболу|Германия]]')


class Wiki:
    def get_page_identity(self, title):
        return {'title': title, 'qid': 'Q1' if title == 'Сборная Германии по футболу' else 'Q2'}
    def get_foreign_page_identity(self, title, language):
        return {'title': title, 'qid': 'Q1'}


def api():
    source = NationalAPI()
    for row in source.rows:
        row['home_team'].update(name='Germany', code='GER', gender='M')
        row['link'] = 'https://www.sofascore.com/football/match/test#id:'+str(row['id'])
    return source


def collect(text=TEXT, source=None, **kwargs):
    source = source or api()
    return prepare_nationals(snapshot(text), MAPPING,
        {'complete': True, 'matches': source.rows}, source, Wiki(), {'remaining': 20}, NOW, **kwargs)


def test_complete_scope_updates_card_with_individual_sources_and_replays_without_changes():
    operations, issues, scopes = collect()
    assert len(operations) == 1 and scopes[0]['numbers'] == {'appearances': 2, 'goals': 2}
    op = operations[0]
    assert op['expected'] == {'appearances': 6, 'goals': 0}
    assert len(op['sources']) == 2
    result, status = execute_operation(TEXT, MAPPING['wiki_name'], op)
    assert status == 'ready'
    assert collect(result)[0] == []
    assert 'обновление данных о сборной = 04.10.2026' in result
    assert not any(i['code'] == 'national_global_date_incomplete_coverage' for i in issues)


@pytest.mark.parametrize('problem', ['aggregate', 'missing_game', 'duplicate_team', 'later_period', 'youth'])
def test_incomplete_or_mismatched_national_scope_preserves_card(problem):
    source = api(); text = TEXT
    if problem == 'aggregate': source.expected['statistics'][0]['goals'] = 3
    elif problem == 'missing_game': source.rows.pop()
    elif problem == 'duplicate_team': source.expected['statistics'].append(deepcopy(source.expected['statistics'][0]))
    elif problem == 'later_period': text = text.replace('2020—{{н.в.}}', '2027—{{н.в.}}')
    elif problem == 'closed_period': text = text.replace('2020—{{н.в.}}', '2020—2025')
    else: source.expected['statistics'][0]['team']['name'] = 'Germany U21'
    operations, issues, _ = collect(text, source)
    assert operations == [] and issues


def test_keeper_card_uses_negative_concessions_and_does_not_use_aggregate_scored_goals():
    source = api(); source.expected['statistics'][0]['goals'] = 0
    operations, _, _ = collect(source=source, goalkeeper=True)
    assert operations[0]['new'] == {'appearances': 2, 'goals': -6}


def test_unlinked_youth_rows_do_not_block_the_verified_senior_card():
    # Kobel's actual draft has plain youth labels alongside a linked senior team.
    text = TEXT.replace('|2020—{{н.в.}}', '|2013—2014|{{Флаг Германии|15px}} Германия (до 17)|3 (-5)\n  |2020—{{н.в.}}')
    operations, _, _ = collect(text)
    assert len(operations) == 1
    assert operations[0]['target']['period'] == '2020—{{н.в.}}'
    result, status = execute_operation(text, MAPPING['wiki_name'], operations[0])
    assert status == 'ready' and 'Германия (до 17)|3 (-5)' in result


def test_unlinked_adult_row_prevents_claiming_unique_senior_card_identity():
    text = TEXT.replace('|2020—{{н.в.}}', '|2013—2014|Германия|3 (0)\n  |2020—{{н.в.}}')
    operations, issues, _ = collect(text)
    assert operations == [] and issues


def test_main_cycle_collects_national_card_and_preserves_global_date(monkeypatch):
    from tests.test_sofascore import API, Wiki as ClubWiki, prepare, match
    source = api()
    for row in source.rows:
        row['tournament'] = {'id': 851}
        row['home_team']['id'] = 4709
        row['player']['team_id'] = 4709
    class Combined(API):
        def national_statistics(self, pid): return source.national_statistics(pid)
        def match_statistics(self, pid, mid):
            data = source.match_statistics(pid, mid)
            data['team']['id'] = 4709
            return data
        def matches(self, pid, **kwargs):
            return {'complete': True, 'matches': [match(99)]+source.rows}
    old_identity = ClubWiki.get_page_identity
    def identity(self, title):
        if title == 'Сборная Германии по футболу': return {'title': title, 'qid': 'Q9000'}
        return old_identity(self, title)
    monkeypatch.setattr(ClubWiki, 'get_page_identity', identity)
    monkeypatch.setattr(ClubWiki, 'get_foreign_page_identity', lambda self, title, language: {'title': title, 'qid': 'Q9000'}, raising=False)
    package, plan, report = prepare(TEXT, Combined())
    national = [c for c in plan.changes if c.operation['id'].startswith('national-card-')]
    assert len(national) == 1 and national[0].status == 'ready'
    assert report['national_scopes'][0]['numbers'] == {'appearances': 2, 'goals': 2}
    assert 'обновление данных о сборной = 04.10.2026' in plan.preview
    _, replay, _ = prepare(plan.preview.replace('| дата рождения = 22.08.1999\n', ''), Combined())
    assert not replay.diff


def test_actual_orban_source_updates_card_from_all_72_verified_matches():
    import gzip, hashlib, json
    from pathlib import Path
    from wiki_stats.sofascore_client import _SofascoreAccount
    from wiki_stats.errors import UpdateError
    data = json.loads(gzip.decompress((Path(__file__).parent/'source-fixtures/orban-national-source.json.gz').read_bytes()))
    lookup = {r['cache_key']: r['data'] for r in data['responses']}
    class Replay(_SofascoreAccount):
        def __init__(self): pass
        def national_statistics(self, pid): return data['national']
        def request(self, path, params, **kwargs):
            key = hashlib.sha256(json.dumps([path, params], sort_keys=True).encode()).hexdigest()
            if key not in lookup: raise UpdateError('source_cache_missing', 'No new API calls in replay')
            return lookup[key]
    class HungaryWiki:
        def get_page_identity(self, title): return {'title': title, 'qid': 'Q172107'}
        def get_foreign_page_identity(self, title, language): return {'title': title, 'qid': 'Q172107'}
    text = TEXT.replace('Германии', 'Венгрии').replace('Германия', 'Венгрия').replace('2020—', '2018—')
    operations, issues, scopes = prepare_nationals(snapshot(text),
        {**MAPPING, 'api_id': 136924}, data['history'], Replay(), HungaryWiki(), {'remaining': 500}, NOW)
    assert len(operations) == 1 and operations[0]['new'] == {'appearances': 72, 'goals': 6}
    assert len(operations[0]['sources']) == 72 and len(scopes[0]['matches']) == 72
    result, status = execute_operation(text, MAPPING['wiki_name'], operations[0])
    assert status == 'ready' and '|72 (6)' in result
    assert not issues


@pytest.mark.parametrize('period,old_caps,expected_period',[('2020',2,'2020—{{н. в.}}'),
    ('2020—2026',2,'2020—2026'),('2020—2026',1,'2020—{{н. в.}}'),
    ('2020—2025',6,'2020—{{н. в.}}')])
def test_senior_period_preserves_explicit_end_until_new_matches(period,old_caps,expected_period):
    from dataclasses import replace
    from wiki_stats.change_planner import plan_article
    text=TEXT.replace('2020—{{н.в.}}',period).replace('|6 (0)','|'+str(old_caps)+' (2)')
    ops,issues,scopes=collect(text)
    assert not issues
    result=text
    for op in ops:result,_=execute_operation(result,MAPPING['wiki_name'],op)
    assert '|'+expected_period+'|' in result
    assert '|2 (2)' in result
    assert collect(result)[0]==[]
    if ops:
        page=snapshot(text)
        article={'title':page.title,'base_revid':page.revid,'player':MAPPING['wiki_name'],'operations':ops}
        plan=plan_article(page,article,'1.1')
        assert all(c.status=='ready' for c in plan.changes)
        assert not plan_article(replace(page,text=plan.preview),article,'1.1').diff


def test_correcting_goals_alone_does_not_reopen_an_explicit_end():
    text=TEXT.replace('2020—{{н.в.}}','2020—2026').replace('|6 (0)','|2 (0)')
    ops,_,_=collect(text)
    assert [op['type'] for op in ops]==['update_stats']
    after,_=execute_operation(text,MAPPING['wiki_name'],ops[0])
    assert '|2020—2026|' in after


def test_reopening_period_caps_and_date_apply_as_one_replayable_group():
    from dataclasses import replace
    from wiki_stats.national_card_coverage import close_national_card
    from wiki_stats.change_planner import plan_article
    from wiki_stats.schema_validator import validate_package
    text=TEXT.replace('2020—{{н.в.}}','2026—2026').replace('|6 (0)','|1 (0)')
    page=snapshot(text);ops,_,scopes=collect(text)
    ops,complete=close_national_card(page,MAPPING,ops,scopes,NOW)
    assert complete and len({op['group_id'] for op in ops})==1
    article={'title':page.title,'base_revid':page.revid,'player':MAPPING['wiki_name'],'operations':ops}
    validate_package({'schema_version':'1.1','package_id':'senior-reopened-period','generated_at':NOW.isoformat(),'articles':[article]})
    plan=plan_article(page,article,'1.1')
    assert all(c.status=='ready' for c in plan.changes)
    assert '|2026—{{н. в.}}|' in plan.preview and '|2 (2)' in plan.preview
    assert not plan_article(replace(page,text=plan.preview),article,'1.1').diff
