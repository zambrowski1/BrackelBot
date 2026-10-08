# SPDX-License-Identifier: MIT
from copy import deepcopy
from dataclasses import replace
import pytest
from wiki_stats.change_planner import plan_article,plan_package
from wiki_stats.schema_validator import validate_package
from wiki_stats.errors import UpdateError
from wiki_stats.wikitext_parser import WikitextParser
from wiki_stats.change_validator import arithmetic_warnings,references
from stage23_helpers import *


def plan(ops,page=None):
    pkg=package(ops);validate_package(pkg)
    return plan_article(page or snapshot(),pkg['articles'][0],'1.1',FakeResolver())


def test_transfer_atomic_preserves_formatting():
    result=plan(transfer())
    assert [c.status for c in result.changes]==['ready']*3
    assert '|2026—2026|'+AUGSBURG in result.preview
    assert '|2026—{{н.в.}}|'+BVB+'|2 (1)' in result.preview
    assert '| нынешний клуб = '+BVB in result.preview
    assert references(MINI)==references(result.preview)
    assert 'Текст 4 (1) с [[ссылкой]]' in result.preview
    assert 'номер = 2<!-- сохранить -->' in result.preview
    assert '<!-- статистика -->' in result.preview
    assert len(result.groups)==1 and result.publishable


def test_return_to_previous_club_is_a_new_stint():
    result=plan(transfer('Вердер','Q13174'))
    assert all(c.status=='ready' for c in result.changes)
    assert '|2010—2012|'+WERDER in result.preview
    assert '|2026—{{н.в.}}|'+WERDER+'|2 (1)' in result.preview


def test_transfer_failure_rolls_back_everything():
    ops=transfer();ops[-1]['expected']='Неправильный клуб'
    result=plan(ops)
    assert result.preview==MINI
    assert not result.diff and not result.publishable
    assert [c.status for c in result.changes]==['group_blocked','group_blocked','old_value_mismatch']


def test_link_failure_rolls_back_period_closure():
    ops=transfer(qid='Q999')
    result=plan(ops)
    assert result.preview==MINI
    assert result.changes[0].status=='group_blocked'
    assert result.changes[1].status=='entity_mismatch'


@pytest.mark.parametrize('role,marker',[('loan','{{аренда}}'),('reserve','{{фарм-клуб}}')])
def test_loan_reserve_keep_parent_active(role,marker):
    op=transfer()[1];op['payload'].pop('previous');op['payload']['role']=role;op.pop('group_id')
    result=plan([op])
    assert result.changes[0].status=='ready'
    assert marker+BVB in result.preview
    assert '|2026—{{н.в.}}|'+AUGSBURG in result.preview
    assert '| нынешний клуб = '+AUGSBURG in result.preview


def test_unknown_years_refused():
    ops=transfer();ops[0]['payload']['period']='2026—?'
    result=plan(ops)
    assert result.preview==MINI
    assert result.changes[0].status=='unknown_period'


def test_unclosed_previous_stint_refused():
    ops=transfer()[1:]
    ops[0]['payload']['previous']['period']='2026—{{н.в.}}'
    result=plan(ops)
    assert result.preview==MINI
    assert result.changes[0].status=='previous_period_not_closed'


def test_repeat_transfer_idempotent():
    first=plan(transfer())
    second=plan(transfer(),replace(snapshot(),text=first.preview))
    assert [c.status for c in second.changes]==['already_applied']*3
    assert not second.diff


@pytest.mark.parametrize('age',[None,21,20,19,18,17])
def test_add_team_age_separate(age):
    result=plan([team_operation(age)])
    assert result.changes[0].status=='ready'
    assert U18 in result.preview
    assert '|2016—2017|' in result.preview
    assert '|2026—{{н.в.}}|' in result.preview
    assert '|2 (1)' in result.preview
    repeated=plan([team_operation(age)],replace(snapshot(),text=result.preview))
    assert repeated.changes[0].status=='already_applied'


@pytest.mark.parametrize('empty',[True,False])
def test_create_national_career_missing_or_empty(empty):
    parser=WikitextParser(MINI)
    ref=next(t for t in parser.templates if t.name=='футболист')
    value=ref.values()['национальная сборная']
    if empty:
        text=MINI[:value.start]+' \n'+MINI[value.start+len(value.text):]
    else:
        box=ref.node
        box.remove('национальная сборная')
        text=str(parser.code)
    result=plan([team_operation()],snapshot(text))
    assert result.changes[0].status=='ready'
    rows=WikitextParser(result.preview).career('национальная сборная','Кальвин Браккельман')
    assert len(rows)==1


def test_new_season_rowspans_totals():
    result=plan([season_operation()])
    assert result.changes[0].status=='ready'
    table=WikitextParser(result.preview).club_table()
    assert len(table.seasons)==2
    assert table.seasons[-1].season=='2026/27'
    assert table.grand_total[0].numeric()[0].value==6
    assert arithmetic_warnings(result.preview)==[]
    assert references(MINI)==references(result.preview)
    assert '|сезоны=2}}' in result.preview
    repeat=plan([season_operation()],replace(snapshot(),text=result.preview))
    assert repeat.changes[0].status=='already_applied'


def test_unknown_season_values_never_filled_zero():
    op=season_operation();del op['payload']['categories']['Еврокубки']
    result=plan([op]);assert result.changes[0].status=='incomplete_statistics'
    assert result.preview==MINI
    op=season_operation();op['payload']['categories']['Еврокубки']['goals']=None
    with pytest.raises(UpdateError):validate_package(package([op]))


def competition(category='Еврокубки'):
    return operation('europe','add_competition',entity={'name':'Аугсбург','wikitext':TABLE_AUGSBURG},
            target={'structure':'club_table'},season='2025/26',competition={'name':'Лига Европы','category':category,
            'scope':'category','mapping_rule':'ВЫМЫШЛЕННЫЙ ТЕСТ: полный итог категории, остальные турниры отсутствуют'},
            expected={'appearances':0,'goals':0},new={'appearances':2,'goals':1})


def test_new_european_competition_existing_category():
    result=plan([competition()]);assert result.changes[0].status=='ready'
    assert '{{КлСтат/Сезон|2025/26|4|1|0|0|2|1|0|0}}' in result.preview
    assert arithmetic_warnings(result.preview)==[]


def test_structure_proposal_never_automatically_executed():
    op=competition('Кубок лиги');op['payload']={'structure_proposal':'ТЕСТ: нужна новая пара столбцов Кубок лиги'}
    result=plan([op]);assert result.changes[0].status=='structure_change_required'
    assert 'новая пара' in result.changes[0].diff
    assert result.preview==MINI and not result.publishable


def test_recompute_confirmed_known_totals():
    text=MINI.replace('{{КлСтат/Итого|4|1|','{{КлСтат/Итого|99|1|')
    op=operation('totals','update_totals',target={'structure':'club_table'},payload={'complete':True})
    result=plan([op],snapshot(text));assert result.changes[0].status=='ready'
    assert arithmetic_warnings(result.preview)==[]
    unknown=MINI.replace('{{КлСтат/Сезон|2025/26|4|','{{КлСтат/Сезон|2025/26|?|')
    assert plan([op],snapshot(unknown)).changes[0].status=='unsupported_structure'


def test_date_uses_source_coverage():
    op=operation('date','update_date',payload={'field':'обновление данных о клубе','expected_value':'04.10.2026'})
    result=plan([op]);assert result.changes[0].status=='ready'
    assert '| обновление данных о клубе = 08.10.2026' in result.preview
    assert '| обновление данных о сборной = 04.10.2026' in result.preview


def test_multi_row_group_stats_old_mismatch():
    op1=stats_operation(group_id='stats-group')
    op2=stats_operation('national','stats-group')
    op2['target']={'structure':'career','field':'национальная сборная','period':'2016—2017'}
    op2['entity']={'name':'Германия до 18','wikitext':U18}
    op2['competition']={'name':'Все матчи','category':'all','scope':'national_all'}
    op2['expected']={'appearances':6,'goals':0};op2['new']={'appearances':7,'goals':1}
    good=plan([op1,op2]);assert all(c.status=='ready' for c in good.changes)
    op2['expected']['goals']=99
    bad=plan([op1,op2]);assert bad.preview==MINI and bad.changes[0].status=='group_blocked'


def test_statistical_groups_overlap_via_derived_totals():
    op1=competition();op2=deepcopy(op1);op2['id']='other';op2['competition'].update(name='Стыковые матчи',category='Стыковые матчи')
    result=plan([op1,op2]);assert all(c.status=='ready' for c in result.changes)
    # Different categories touch independent totals and may remain separate.
    op2=deepcopy(op1);op2['id']='same';result=plan([op1,op2])
    assert all(c.status=='patch_conflict' for c in result.changes)
    assert result.preview==MINI


def test_schema_requires_atomic_transfer_and_rejects_secrets():
    with pytest.raises(UpdateError):validate_package(package([transfer()[1]]))
    pkg=package([stats_operation()]);pkg['password']='should-not-be-stored'
    with pytest.raises(UpdateError):validate_package(pkg)


def test_real_captured_test_page_and_legacy_json(client):
    from pathlib import Path
    from wiki_stats.json_loader import load_package
    pkg=load_package(Path(__file__).parents[1]/'examples/BrackelBot-testbot-demo.json')
    plans,errors=plan_package(pkg,client)
    assert not errors and plans[0].changes[0].status=='ready'
    assert '5 (2)' in plans[0].preview
    assert references(plans[0].snapshot.text)==references(plans[0].preview)


def test_all_generated_scenarios_against_full_fixture():
    from pathlib import Path
    from wiki_stats.json_loader import load_package
    root=Path(__file__).parents[1]/'examples/stage23'
    backup=__import__('json').loads((root/'00-comprehensive-baseline.backup.json').read_text(encoding='utf-8'))
    page=PageSnapshot(TEST_TITLE,backup['revid'],backup['timestamp'],backup['text'],2)
    expected={'08-mismatch-rolls-back-all':'old_value_mismatch','10-structure-proposal':'structure_change_required',
              '11-incomplete-statistics':'incomplete_statistics','12-unknown-years':'unknown_period',
              '13-wrong-borussia-qid':'entity_mismatch'}
    for path in sorted(root.glob('*.json')):
        if path.name.endswith('.backup.json'): continue
        pkg=load_package(path)
        # FakeResolver used only for offline edits; real identity checks have
        # independent resolver tests, plus separately recorded live GET checks.
        resolver=FakeResolver()
        if path.stem=='13-wrong-borussia-qid':
            class WrongResolver:
                def resolve(self,entity):raise UpdateError('entity_mismatch','Incorrect Borussia QID')
            resolver=WrongResolver()
        result=plan_article(page,pkg['articles'][0],'1.1',resolver)
        statuses={c.status for c in result.changes}
        if path.stem in expected:
            assert expected[path.stem] in statuses,(path.name,statuses)
            assert result.preview==page.text
        elif path.stem=='14-recompute-totals':
            assert statuses=={'already_applied'}
        else:
            assert statuses=={'ready'},(path.name,[(c.status,c.message) for c in result.changes])
            assert references(page.text)==references(result.preview)
            assert arithmetic_warnings(result.preview)==[]
