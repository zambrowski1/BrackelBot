import gzip,json
from pathlib import Path
from copy import deepcopy
from datetime import datetime,timezone
from types import SimpleNamespace
import pytest
from wiki_stats.dfb_youth_profile import parse_closed_youth
from wiki_stats.errors import UpdateError

MAPPING={'api_id':136924,'qid':'Q2577379','birth_date':'1992-11-03','wiki_name':'Вилли Орбан','source_name':'Willi Orban'}
NOW=datetime(2026,10,11,tzinfo=timezone.utc)

def source():
    with gzip.open(Path(__file__).parent/'source-fixtures/dfb-orban-u21-roster.json.gz','rt',encoding='utf-8') as f:return json.load(f)

def test_real_orban_two_caps_one_substitute_one_starter():
    scopes=parse_closed_youth(MAPPING,source(),NOW)
    assert len(scopes)==1
    s=scopes[0]
    assert s['numbers']=={'appearances':2,'goals':0} and s['complete'] is True
    assert [r['date'] for r in s['matches']]==['2014-11-13','2015-03-27']
    assert [r['appearance_proof'] for r in s['matches']]==['entered_substitute','starter']
    assert [r['display_score'] for r in s['matches']]==[[3,1],[2,2]]
    assert s['statistics_as_of']=='2015-03-27'  # Squad registration until2016 is not a cap.

@pytest.mark.parametrize('fault',['pid','float_pid','qid','dob','source_name','bundle_pid','foreign_roster','empty_roster','roster_dob','roster_name','all_players_url','summary_name','summary_caps','summary_goals','missing_protocol','duplicate_protocol','foreign_protocol','wrong_match_id','wrong_opponent','wrong_date','bench_only','late_entry','wrong_starter','missing_goals','incomplete_goals','inconsistent_score','metadata_duplicate','eligible_year'])
def test_roster_evidence_cannot_turn_missing_or_conflicting_data_into_zero(fault):
    x=source();m=deepcopy(MAPPING);now=NOW
    if fault=='pid':m['api_id']=228981
    elif fault=='float_pid':m['api_id']=136924.0
    elif fault=='qid':m['qid']='Q1'
    elif fault=='dob':m['birth_date']='1992-11-04'
    elif fault=='source_name':m['source_name']='Tim Kleindienst'
    elif fault=='bundle_pid':x['player_id']=True
    elif fault=='foreign_roster':x['roster']['url']=x['roster']['url'].replace('datencenter.dfb.de','example.com')
    elif fault=='empty_roster':x['roster']['html']=''
    elif fault=='roster_dob':x['roster']['html']=x['roster']['html'].replace('1992-11-03','1992-11-04')
    elif fault=='roster_name':x['roster']['html']=x['roster']['html'].replace('Willi Orban','Tim Kleindienst')
    elif fault=='all_players_url':x['all_players']['url']=x['all_players']['url'].replace('deutschland-u-21','deutschland-u-20')
    elif fault=='summary_name':x['all_players']['html']=x['all_players']['html'].replace('Orban, Willi','Orban, Other')
    elif fault in {'summary_caps','summary_goals'}:
        text=x['all_players']['html'];at=text.index('<td>Orban, Willi</td>')
        tail=text[at:];value='2' if fault=='summary_caps' else '0'
        tail=tail.replace('<td>'+value+'</td>','<td>3</td>',1);x['all_players']['html']=text[:at]+tail
    elif fault=='missing_protocol':x['protocols'].pop()
    elif fault=='duplicate_protocol':x['protocols'][1]=deepcopy(x['protocols'][0])
    elif fault=='foreign_protocol':x['protocols'][0]['url']=x['protocols'][0]['url'].replace('datencenter.dfb.de','example.com')
    elif fault=='wrong_match_id':x['protocols'][0]['match_id']=123
    elif fault=='wrong_opponent':x['protocols'][0]['html']=x['protocols'][0]['html'].replace('Netherlands','France')
    elif fault=='wrong_date':x['protocols'][0]['html']=x['protocols'][0]['html'].replace('13 Nov 2014','12 Nov 2014')
    elif fault=='bench_only':x['protocols'][0]['html']=x['protocols'][0]['html'].replace('Willi Orban&nbsp;for','Other player&nbsp;for')
    elif fault=='late_entry':x['protocols'][0]['html']=x['protocols'][0]['html'].replace("83'","90'")
    elif fault=='wrong_starter':x['protocols'][1]['html']=x['protocols'][1]['html'].replace('Willi Orban','Other player')
    elif fault=='missing_goals':x['protocols'][0]['html']=x['protocols'][0]['html'].replace('>Goals<','>Other section<')
    elif fault=='incomplete_goals':x['protocols'][0]['html']=x['protocols'][0]['html'].replace('Amin Younes','Willi Orban')
    elif fault=='inconsistent_score':x['protocols'][0]['html']=x['protocols'][0]['html'].replace('3:1','4:1')
    elif fault=='metadata_duplicate':x['protocols'][0]['html']='<meta property="og:url" content="https://example.com">'+x['protocols'][0]['html']
    else:now=datetime(2015,6,1,tzinfo=timezone.utc)
    with pytest.raises(UpdateError) as exc:parse_closed_youth(m,x,now)
    assert exc.value.code=='dfb_roster_youth_unproven'

def test_card_corrects_wrong_numbers_without_changing_birth_date_and_replays():
    from tests.stage23_helpers import MINI,U18,snapshot
    from tests.test_dfb_youth_cycle import Wiki
    from wiki_stats.dfb_youth_cycle import prepare_youth_nationals
    from wiki_stats.structural_editor import execute_operation
    text=MINI.replace('Кальвин Браккельман','Вилли Орбан').replace('|2016—2017|'+U18+'|6 (0)',
        '|2014—2016|{{Флаг Германии|20px}} [[Сборная Германии по футболу (до 21 года)|Германия (до 21)]]|9 (3)')
    api=SimpleNamespace(national_secondary_source=lambda pid:source())
    ops,issues,scopes=prepare_youth_nationals(snapshot(text),MAPPING,api,Wiki(),NOW)
    assert not issues and len(scopes)==1
    assert [op['type'] for op in ops]==['update_career_period','update_stats']
    result=text
    for op in ops:result,status=execute_operation(result,'Вилли Орбан',op);assert status=='ready'
    assert '|2014—2015|' in result and '|2 (0)' in result and '|9 (3)' not in result
    assert prepare_youth_nationals(snapshot(result),MAPPING,api,Wiki(),NOW)[0]==[]
    for op in ops:
        replay,status=execute_operation(result,'Вилли Орбан',op)
        assert replay==result and status=='already_applied'
