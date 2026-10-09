# SPDX-License-Identifier: MIT
from copy import deepcopy
from dataclasses import replace

import pytest

from wiki_stats.change_planner import plan_article
from wiki_stats.change_validator import arithmetic_warnings, references
from wiki_stats.klstat_editor import check_table
from wiki_stats.schema_validator import validate_package
from wiki_stats.errors import UpdateError
from wiki_stats.structural_editor import table_layout
from wiki_stats.wikitext_parser import WikitextParser
from stage23_helpers import *


def planned(ops, text=MINI):
    pkg=package(ops); validate_package(pkg)
    return plan_article(snapshot(text),pkg['articles'][0],'1.1',FakeResolver())


def add_block():
    op=season_operation()
    op.update(id='block',type='add_table_club',entity={'name':'Боруссия Дортмунд','kind':'club',
              'wikidata_id':'Q41420','wikitext':BVB},season='2026/27')
    op['payload'].pop('insert_after_season')
    op['payload']['insert_after_club']=TABLE_AUGSBURG
    return op


def delete_row(text,season):
    row=next(r for r,l in table_layout(WikitextParser(text))[TABLE_AUGSBURG]['seasons'] if r.values()['1'].text==season)
    return operation('remove','remove_season',entity={'name':'Аугсбург','wikitext':TABLE_AUGSBURG},
        target={'structure':'club_table'},season=season,
        payload={'complete':True,'expected_wikitext':str(row.node),'reason':'Подтверждённая ошибочная строка; синтетический тест'})


def test_new_club_verified_totals_and_repeat():
    op=add_block(); first=planned([op])
    assert first.changes[0].status=='ready'
    assert len(check_table(first.preview)['rows'])==2
    assert not arithmetic_warnings(first.preview)
    assert references(first.preview)==references(MINI)
    assert first.preview.split('{{КлСтат|')[0]==MINI.split('{{КлСтат|')[0]
    assert planned([op],first.preview).changes[0].status=='already_applied'


def test_new_club_rejects_wrong_wikilink():
    op=add_block();op['entity']['wikitext']=WERDER
    assert planned([op]).changes[0].status=='entity_mismatch'


@pytest.mark.parametrize('before',[False,True])
def test_new_league_and_historical_season(before):
    op=season_operation();op['payload']['league_wikitext']='[[Вторая Бундеслига]]'
    if before:
        op['season']='2024/25'
        op['payload']['insert_before_season']=op['payload'].pop('insert_after_season')
    first=planned([op])
    assert first.changes[0].status=='ready'
    assert len(WikitextParser(first.preview).club_table().seasons)==2
    assert first.preview.count('{{КлСтат/Лига|')==2
    assert not arithmetic_warnings(first.preview)
    assert planned([op],first.preview).changes[0].status=='already_applied'
    deleted=planned([delete_row(first.preview,op['season'])],first.preview)
    assert deleted.changes[0].status=='ready'
    assert deleted.preview.count('{{КлСтат/Лига|')==1
    assert not arithmetic_warnings(deleted.preview)


def test_insert_gap_and_delete_with_rowspan_recalculation():
    op=season_operation();op['season']='2027/28'
    first=planned([op])
    gap=season_operation();gap['season']='2026/27'
    second=planned([gap],first.preview)
    assert second.changes[0].status=='ready'
    assert [r.season for r in WikitextParser(second.preview).club_table().seasons]==['2025/26','2026/27','2027/28']
    delete=delete_row(second.preview,'2026/27')
    third=planned([delete],second.preview)
    assert third.changes[0].status=='ready'
    assert not arithmetic_warnings(third.preview)
    assert '|сезоны=2}}' in third.preview
    assert not planned([delete],third.preview).diff


def test_changed_row_refused_and_atomic_group_rolled_back():
    text=planned([season_operation()]).preview
    delete=delete_row(text,'2026/27');delete['payload']['expected_wikitext']+='changed'
    delete['group_id']='atomic'
    stats=stats_operation(group_id='atomic')
    result=planned([stats,delete],text)
    assert result.preview==text
    assert [c.status for c in result.changes]==['group_blocked','old_value_mismatch']


@pytest.mark.parametrize('annotation',['<!-- сохранить -->','<ref>Источник</ref>'])
def test_deletion_preserves_references_and_comments(annotation):
    text=planned([season_operation()]).preview.replace('|2026/27|2|','|2026/27|2'+annotation+'|')
    result=planned([delete_row(text,'2026/27')],text)
    assert result.changes[0].status=='integrity_error'
    assert result.preview==text


def test_last_season_cannot_leave_empty_block():
    result=planned([delete_row(MINI,'2025/26')])
    assert result.changes[0].status=='last_season'


def test_remove_block_recalculates_grand_total():
    first=planned([add_block()])
    layout=table_layout(WikitextParser(first.preview))[BVB]
    fragment=first.preview[layout['club'].start:layout['total'].start+len(str(layout['total'].node))]
    op=operation('block-remove','remove_table_club',entity={'name':'Боруссия','wikitext':BVB},target={'structure':'club_table'},
                 payload={'complete':True,'expected_wikitext':fragment,'reason':'Ошибочный блок, подтверждено оператором'})
    result=planned([op],first.preview)
    assert result.changes[0].status=='ready'
    assert len(check_table(result.preview)['rows'])==1
    assert not arithmetic_warnings(result.preview)
    assert not planned([op],result.preview).diff


def test_partial_categories_never_filled_with_zero():
    op=add_block();del op['payload']['categories']['Еврокубки']
    result=planned([op]);assert result.changes[0].status=='incomplete_statistics'
    assert result.preview==MINI


def test_check_reports_totals_discrepancy():
    report=check_table(MINI.replace('{{КлСтат/Итого|4|','{{КлСтат/Итого|99|'))
    assert report['warnings']
    assert report['rows'][0]['categories']['Чемпионат']=={'appearances':4,'goals':1}


def test_deletion_requires_reason_and_table_target():
    op=delete_row(MINI,'2025/26');op['payload']['reason']=' '
    with pytest.raises(UpdateError):validate_package(package([op]))
    op['payload']['reason']='test';op['target']={'structure':'career','field':'клубы'}
    with pytest.raises(UpdateError):validate_package(package([op]))


def evidence_for(rows):
    return {'player':'Кальвин Браккельман','as_of':'2026-10-08','sources':source(),
            'clubs':[{'entity':{'name':'Аугсбург','wikitext':TABLE_AUGSBURG},'rows':rows}]}


def covered(season,appearances):
    return {'season':season,'league_wikitext':LEAGUE,'categories':{
        c:{'appearances':appearances if c=='Чемпионат' else 0,'goals':1 if c=='Чемпионат' else 0}
        for c in ['Чемпионат','Национальный кубок','Еврокубки','Стыковые матчи']}}


def test_reconciliation_repairs_totals_adds_history_and_changes_existing_season():
    from wiki_stats.klstat_planner import prepare_table
    text=MINI.replace('{{КлСтат/Итого|4|','{{КлСтат/Итого|99|')
    evidence=evidence_for([covered('2026/27',6),covered('2025/26',5),covered('2024/25',3)])
    pkg,result=prepare_table(snapshot(text),evidence,FakeResolver())
    assert all(c.status in {'ready','already_applied'} for c in result.changes)
    report=check_table(result.preview)
    assert not report['warnings']
    assert [r['season'] for r in report['rows']]==['2024/25','2025/26','2026/27']
    assert [r['categories']['Чемпионат']['appearances'] for r in report['rows']]==[3,5,6]
    assert references(result.preview)==references(text)
    assert result.preview.split('{{КлСтат|')[0]==text.split('{{КлСтат|')[0]
    _,again=prepare_table(replace(snapshot(),text=result.preview),evidence,FakeResolver())
    assert not again.diff


def test_partial_source_does_not_delete_old_season():
    from wiki_stats.klstat_planner import prepare_table
    pkg,result=prepare_table(snapshot(),evidence_for([covered('2026/27',6)]),FakeResolver())
    assert [r.season for r in WikitextParser(result.preview).club_table().seasons]==['2025/26','2026/27']
    assert not any(op['type'].startswith('remove') for op in pkg['articles'][0]['operations'])


def test_reconciliation_wrong_competition_cannot_overwrite_stats():
    from wiki_stats.klstat_planner import prepare_table
    evidence=evidence_for([covered('2025/26',6)])
    evidence['clubs'][0]['rows'][0]['league_wikitext']='[[Вторая Бундеслига]]'
    with pytest.raises(UpdateError) as err:prepare_table(snapshot(),evidence,FakeResolver())
    assert err.value.code=='league_mismatch'
