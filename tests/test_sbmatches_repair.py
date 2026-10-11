# SPDX-License-Identifier: MIT
from copy import deepcopy
import hashlib
import pytest
from tests.stage23_helpers import MINI, package, snapshot
from wiki_stats.wikitext_parser import WikitextParser
from wiki_stats.schema_validator import validate_package
from wiki_stats.sbmatches_editor import read_matches
from wiki_stats.sbmatches_repair import repair
from wiki_stats.change_planner import plan_article
from wiki_stats.errors import UpdateError

TEAM='[[Сборная Германии по футболу|Германию]]'
OLD='{{СбМатчи\n|игрок=Игрока\n|сборная='+TEAM+'\n|обновлено=01-01-2020\n|матч1=15-11-2023 ;; Сербия ;; 1:0 ;; 2\n|матч3=31-02-2024 ;; Чехия ;; 0:0 ;; 0\n|пенальти3=4:4\n}}'
TEXT=MINI+'\n<!-- внешний комментарий -->\n'+OLD


def operation():
    return {'id':'repair','type':'repair_sbmatches','entity':{'name':'Германия','kind':'national_team','age':None,'wikitext':TEAM},
        'target':{'structure':'sbmatches','age':None},'as_of':'2026-10-08',
        'sources':[{'url':'https://www.sofascore.com/football/match/test#id:1','provenance':'sofascore','coverage_as_of':'2026-10-08'}],
        'payload':{'expected_template_sha256':hashlib.sha256(OLD.encode()).hexdigest(),
            'label':'Полигонная выборка: один подтверждённый матч',
            'matches':[{'дата':'11-09-2025','соперник':'[[Сборная Франции по футболу|Франция]]',
                'счёт':'1:0','голы':'0','соревнование':'Товарищеский матч'}]}}


def test_invalid_sandbox_table_is_repaired_and_replays_without_changes():
    op=operation();pkg=package([op]);pkg['articles'][0]['base_revid']=snapshot(TEXT).revid
    validate_package(pkg)
    plan=plan_article(snapshot(TEXT),pkg['articles'][0],'1.1')
    assert plan.changes[0].status=='ready'
    result=plan.preview
    assert result.startswith(MINI) and '<!-- внешний комментарий -->' in result
    table=next(t for t in WikitextParser(result).templates if t.name=='сбматчи')
    assert len(read_matches(table)[1])==1 and '31-02-2024' not in result
    assert 'Полигонная выборка:' in result
    assert repair(result,'Кальвин Браккельман',op)==(result,'already_applied')


def full_operation():
    from datetime import date,timedelta
    op=operation();rows=[];sources=[]
    for i in range(72):
        rows.append({'дата':(date(2018,1,1)+timedelta(days=21*i)).strftime('%d-%m-%Y'),
            'соперник':'[[Сборная Франции по футболу|Франция]]','счёт':'1:0',
            'голы':'1' if i<6 else '0','соревнование':'Товарищеский матч'})
        sources.append({'url':'https://www.sofascore.com/football/match/test#id:'+str(i+1),
                        'provenance':'sofascore','coverage_as_of':op['as_of']})
    op['sources']=sources
    op['payload'].update(label='Матчи игрока за сборную Германии',complete=True,
                         totals={'appearances':72,'goals':6},matches=rows)
    return op


def test_full_list_of_72_games_is_schema_valid_and_replays_without_losing_surroundings():
    op=full_operation();pkg=package([op]);pkg['articles'][0]['base_revid']=snapshot(TEXT).revid
    validate_package(pkg)
    result,status=repair(TEXT,'Кальвин Браккельман',op)
    assert status=='ready' and result.startswith(MINI) and '<!-- внешний комментарий -->' in result
    table=next(t for t in WikitextParser(result).templates if t.name=='сбматчи')
    assert len(read_matches(table)[1])==72
    assert 'Полигонная выборка:' not in result
    assert repair(result,'Кальвин Браккельман',op)==(result,'already_applied')


@pytest.mark.parametrize('problem',['appearance_total','goal_total','no_total','sample','missing_source'])
def test_full_replacement_refuses_incomplete_evidence_and_sample_cannot_claim_72_games(problem):
    op=full_operation()
    if problem=='appearance_total':op['payload']['totals']['appearances']=71
    elif problem=='goal_total':op['payload']['totals']['goals']=7
    elif problem=='no_total':op['payload'].pop('totals')
    elif problem=='sample':
        op['payload']['complete']=False;op['payload']['label']='Полигонная выборка: 72 матча'
    else:op['sources'].pop()
    with pytest.raises(UpdateError):repair(TEXT,'Кальвин Браккельман',op)


def test_complete_keeper_list_crosschecks_signed_conceded_total():
    op=full_operation();text=TEXT.replace('|игрок=','|статистика=пропущенные голы\n|игрок=')
    template=str(next(t for t in WikitextParser(text).templates if t.name=='сбматчи').node)
    op['payload']['expected_template_sha256']=hashlib.sha256(template.encode()).hexdigest()
    op['payload']['totals']['goals']=-6
    for row in op['payload']['matches']:
        row['счёт']='0:1';row['пропущенные голы']=row.pop('голы')
    result,status=repair(text,'Кальвин Браккельман',op)
    assert status=='ready'
    assert repair(result,'Кальвин Браккельман',op)[1]=='already_applied'
    op['payload']['totals']['goals']=6
    with pytest.raises(UpdateError):repair(text,'Кальвин Браккельман',op)


@pytest.mark.parametrize('problem',['changed','notes','score','date','source','markup'])
def test_repair_does_not_drop_notes_or_accept_unproven_structure(problem):
    text=TEXT;op=operation()
    if problem=='changed':text=text.replace('Сербия','Италия')
    elif problem=='notes':
        text=text.replace('|матч1=','|матч1=<!-- примечание -->');op['payload']['expected_template_sha256']=hashlib.sha256(text[text.index('{{СбМатчи'):].encode()).hexdigest()
    elif problem=='score':op['payload']['matches'][0]['голы']='2'
    elif problem=='date':op['payload']['matches'][0]['дата']='31-02-2025'
    elif problem=='source':op['sources']=[]
    else:op['payload']['matches'][0]['соперник']='Франция|поломка'
    with pytest.raises(UpdateError):repair(text,'Кальвин Браккельман',op)


def test_sample_repair_is_forbidden_in_articles_and_requires_revision():
    p=package([operation()]);p['articles'][0]['base_revid']=1
    validate_package(p)
    for title,revision in [('Игрок',1),(p['articles'][0]['title'],None)]:
        bad=deepcopy(p);bad['articles'][0]['title']=title
        if revision is None:bad['articles'][0].pop('base_revid')
        with pytest.raises(UpdateError):validate_package(bad)


def test_sample_collector_requires_operator_binding_and_two_verified_matches():
    from datetime import datetime,timezone
    from types import SimpleNamespace
    from tests.test_sofascore_timeline import SAMPLES
    from wiki_stats.sofascore_sbmatches import sample_repair
    from wiki_stats.test_pages import TEST_PLAYER_BINDINGS
    title='Участник:Zambrowski/testbot5';mapping=TEST_PLAYER_BINDINGS[title]
    sample=deepcopy(SAMPLES[0]);matches=[]
    for i,name in enumerate(['Armenia','Ireland']):
        row=deepcopy(sample['match']);row['id']+=i;row['start_time']=f'2025-11-{13+i:02}T17:00:00Z'
        row['tournament']['id']=11
        row['season']={'id':1,'year':'2026'}
        row['home_team'].update(name='Hungary',is_national=True,sport={'slug':'football'})
        row['away_team'].update(name=name,is_national=True,sport={'slug':'football'})
        matches.append(row)
    class API:
        def match_lineups(self,mid):
            data=deepcopy(sample['lineups']);data['match_id']=mid;return data
        def match_incidents(self,mid):
            data=deepcopy(sample['incidents']);data['match_id']=mid;return data
    class Wiki:
        def get_page_identity(self,title):return {'title':title,'qid':'Q1'}
    text=TEXT.replace(TEAM,'[[Сборная Венгрии по футболу|Венгрию]]')+'\n<!-- Вымышленные строки с намеренными ошибками. -->'
    page=SimpleNamespace(title=title,text=text);now=datetime(2026,10,10,tzinfo=timezone.utc)
    op=sample_repair(page,mapping,{'matches':matches},API(),Wiki(),now)
    assert len(op['payload']['matches'])==2 and op['payload']['matches'][0]['дата']=='13-11-2025'
    assert sample_repair(page,{**mapping,'api_id':1},{'matches':matches},API(),Wiki(),now) is None
    assert sample_repair(page,mapping,{'matches':matches[:1]},API(),Wiki(),now) is None

