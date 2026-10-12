from copy import deepcopy
from dataclasses import replace
from datetime import datetime,timezone
import pytest
from tests.stage23_helpers import MINI,snapshot,package
from tests.test_statistics_section import resolver
from wiki_stats.sbmatches_reconcile import reconcile,compose,sha,external_marker
from wiki_stats.sofascore_sbmatches import full_repair
from wiki_stats.wikitext_parser import WikitextParser
from wiki_stats.sbmatches_editor import read_matches
from wiki_stats.schema_validator import validate_package
from wiki_stats.change_validator import references
from wiki_stats.change_planner import plan_article
from wiki_stats.errors import UpdateError

PLAYER='Кальвин Браккельман'
TEAM='[[Сборная Германии по футболу|Германия]]'
TEXT=MINI+'\n=== Сборная ===\n{{обновлено|2025-01-01|<ref name="date">Источник даты</ref>}}\n'+'''{{СбМатчи
|игрок=Кальвин Браккельман
|сборная=[[Сборная Германии по футболу|Германия]]
|заголовок=Матчи за сборную Германии
|дата1=2025-01-01
|соперник1={{футбол|FRA}}
|счёт1=1:0
|голы1=0
|соревнование1=[[Товарищеский матч|Товарищеский матч с подробностями]]
|место1=[[Париж]]
|примечание1=Вышел на замену
|источник1=<ref name="old">{{Cite web|url=https://example.com/match|title=Старый источник}}</ref>
}}'''


def template(text=TEXT):return next(t for t in WikitextParser(text).templates if t.name=='сбматчи')


def operation(text=TEXT):
    op={'id':'api-sb','type':'reconcile_sbmatches','as_of':'2026-01-01','entity':{'name':'Германия','kind':'national_team','age':None,'wikitext':TEAM},
        'target':{'structure':'sbmatches','age':None},'sources':[{'url':'https://www.sofascore.com/football/match/test#id:'+str(i),'provenance':'sofascore','coverage_as_of':'2026-01-01'} for i in [1,2]],
        'payload':{'complete':True,'expected_template_sha256':sha(str(template(text).node)),'replacement_sha256':'0'*64,
            'totals':{'appearances':2,'goals':2},'old_indices':[1,0],
            'matches':[{'дата':'01-01-2025','соперник':'Франция','счёт':'2:0','голы':'1','соревнование':'Товарищеский матч'},
                       {'дата':'01-01-2026','соперник':'Ирландия','счёт':'1:1','голы':'1','соревнование':'Товарищеский матч'}]}}
    marker=external_marker(text,template(text))
    if marker:op['payload']['external_updated_sha256']=sha(str(marker.node))
    op['payload']['replacement_sha256']=sha(compose(template(text),op))
    return op


def test_complete_api_list_corrects_numbers_adds_match_and_preserves_editor_details():
    op=operation();validate_package(package([op]))
    after,status=reconcile(TEXT,PLAYER,op)
    assert status=='ready' and references(after)==references(TEXT)
    values,rows=read_matches(template(after))
    assert len(rows)==2 and rows[0]['голы']=='1' and rows[0]['счёт']=='2:0'
    assert rows[0]['соперник']=='{{футбол|FRA}}'
    assert rows[0]['место']=='[[Париж]]' and rows[0]['примечание']=='Вышел на замену'
    assert 'подробностями' in rows[0]['соревнование']
    assert '<ref name="old">' in values['источник1'].text
    assert '#id:1' in values['источник1'].text
    assert '{{обновлено|2026-01-01|' in after
    assert 'обновлено' not in values
    assert reconcile(after,PLAYER,op)==(after,'already_applied')


@pytest.mark.parametrize('fault',['stale','missing_old','duplicate_mapping','wrong_total','date','bad_link','marker','metric'])
def test_incomplete_or_conflicting_update_never_deletes_old_data(fault):
    text=TEXT;op=operation()
    if fault=='stale':text=text.replace('Вышел на замену','Старая заметка изменена')
    elif fault=='missing_old':op['payload']['old_indices']=[0,0]
    elif fault=='duplicate_mapping':op['payload']['old_indices']=[1,1]
    elif fault=='wrong_total':op['payload']['totals']['goals']=99
    elif fault=='date':op['payload']['matches'][0]['дата']='02-01-2025'
    elif fault=='bad_link':op['sources'][0]['url']='https://example.com/match'
    elif fault=='marker':text=text.replace('{{обновлено|2025-01-01','{{обновлено|2025-02-01')
    else:op['payload']['matches'][0]['голы']='3'
    with pytest.raises(UpdateError):reconcile(text,PLAYER,op)


def test_old_matches_can_be_inserted_before_existing_rows_without_losing_refs():
    op=operation()
    op['payload']['matches'][1]['дата']='01-01-2024'
    op['payload']['matches'].reverse();op['payload']['old_indices']=[0,1]
    op['payload']['replacement_sha256']=sha(compose(template(),op))
    after,_=reconcile(TEXT,PLAYER,op)
    assert references(after)==references(TEXT)
    assert read_matches(template(after))[1][0]['дата']=='01-01-2024'


def test_plan_and_revision_recheck_are_idempotent():
    page=snapshot(TEXT);pkg=package([operation()]);pkg['articles'][0]['base_revid']=page.revid
    plan=plan_article(page,pkg['articles'][0],'1.1')
    assert plan.changes[0].status=='ready'
    assert not plan_article(replace(page,text=plan.preview),pkg['articles'][0],'1.1').diff
    assert plan_article(replace(page,revid=page.revid+1),pkg['articles'][0],'1.1').changes[0].status=='revision_conflict'
