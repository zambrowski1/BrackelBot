from pathlib import Path
import gzip,json,re
from datetime import datetime,timezone
from types import SimpleNamespace
import pytest
from wiki_stats.dfb_club_profile import parse_closed_clubs
from wiki_stats.errors import UpdateError

NOW=datetime(2026,10,11,tzinfo=timezone.utc)
MAPPING={'api_id':136924,'qid':'Q2577379','birth_date':'1992-11-03','source_name':'Willi Orban','wiki_name':'Вилли Орбан'}

def source():
    return json.loads(gzip.decompress((Path(__file__).parent/'source-fixtures/dfb-orban-kaiserslautern-2012.json.gz').read_bytes()))

def test_full_34_rounds_prove_seven_caps_and_plain_text_away_goal():
    scope=parse_closed_clubs(MAPPING,source(),NOW)[0]
    assert scope['numbers']=={'appearances':7,'goals':1}
    assert len(scope['confirmed_calendar_match_ids'])==34 and len(scope['verified_absence_match_ids'])==27
    assert [r['appearance_proof'] for r in scope['matches']].count('entered_substitute')==3
    assert [r['appearance_proof'] for r in scope['matches']].count('starter')==4
    assert [r['match_id'] for r in scope['matches'] if r['goals']]==[1121066]
    assert scope['statistics_as_of']=='2013-06-30' and len(scope['sources'])==35

@pytest.mark.parametrize('fault',['pid','float_pid','qid','dob','name','bundle_pid','calendar_url','calendar_title','roster_dob',
    'missing_protocol','duplicate_protocol','bool_id','protocol_url','duplicate_metadata','wrong_team','wrong_score',
    'half_score','missing_lineup','duplicate_starter','missing_bench','wrong_bench','missing_subsection','late_sub',
    'wrong_outgoing','missing_goal_section','changed_goal','own_goal','calendar_date','calendar_score','duplicate_round','not_closed'])
def test_partial_or_inconsistent_primary_documents_cannot_certify_a_club_season(fault):
    data=source();mapping=dict(MAPPING);now=NOW
    first=data['protocols'][0];sub=next(p for p in data['protocols'] if p['match_id']==1121132)
    goal=next(p for p in data['protocols'] if p['match_id']==1121066)
    if fault=='pid':mapping['api_id']=228981
    elif fault=='float_pid':mapping['api_id']=136924.0
    elif fault=='qid':mapping['qid']='Q1'
    elif fault=='dob':mapping['birth_date']='1992-11-04'
    elif fault=='name':mapping['source_name']='Tim Kleindienst'
    elif fault=='bundle_pid':data['player_id']=True
    elif fault=='calendar_url':data['calendar']['url']=data['calendar']['url'].replace('dfb.de','example.com')
    elif fault=='calendar_title':data['calendar']['html']=data['calendar']['html'].replace('2. Bundesliga 2012/13','Bundesliga 2012/13')
    elif fault=='roster_dob':data['calendar']['html']=data['calendar']['html'].replace('03.11.1992','04.11.1992')
    elif fault=='missing_protocol':data['protocols'].pop()
    elif fault=='duplicate_protocol':data['protocols'][1]=first
    elif fault=='bool_id':first['match_id']=True
    elif fault=='protocol_url':first['url']=first['url'].replace('dfb.de','example.com')
    elif fault=='duplicate_metadata':first['html']='<meta property="og:url" content="https://example.com">'+first['html']
    elif fault=='wrong_team':first['html']=first['html'].replace('1. FC Kaiserslautern','Other club')
    elif fault=='wrong_score':first['html']=first['html'].replace('3:3','4:3')
    elif fault=='half_score':first['html']=first['html'].replace('(0:0)','(4:0)')
    elif fault=='missing_lineup':first['html']=first['html'].replace('Aufstellung','Other')
    elif fault=='duplicate_starter':first['html']=first['html'].replace('Tobias Sippel','Mathias Abel')
    elif fault=='missing_bench':sub['html']=sub['html'].replace('Auswechselspieler','Other')
    elif fault=='wrong_bench':
        before=sub['html'];sub['html']=re.sub(r'(?<=>)\s*Willi Orban\s*(?=</td>)','Other player',sub['html'],count=1);assert sub['html']!=before
    elif fault=='missing_subsection':sub['html']=sub['html'].replace('>Wechsel<','>Other<')
    elif fault=='late_sub':sub['html']=sub['html'].replace("90'","91'")
    elif fault=='wrong_outgoing':
        before,after=sub['html'].split('>Wechsel<',1)
        sub['html']=before+'>Wechsel<'+after.replace('Mitchell Weiser','Other player')
    elif fault=='missing_goal_section':goal['html']=goal['html'].replace('>Tore<','>Other<')
    elif fault=='changed_goal':
        before,after=goal['html'].split('>Tore<',1)
        middle,tail=after.split('>Karten<',1)
        goal['html']=before+'>Tore<'+middle.replace('Willi Orban','Other player')+'>Karten<'+tail
    elif fault=='own_goal':goal['html']=goal['html'].replace('Willi Orban','Willi Orban (ET)')
    elif fault=='calendar_date':data['calendar']['html']=data['calendar']['html'].replace('06.08.2012','07.08.2012')
    elif fault=='calendar_score':data['calendar']['html']=data['calendar']['html'].replace('3 : 3','4 : 3')
    elif fault=='duplicate_round':data['calendar']['html']=data['calendar']['html'].replace('/1-spieltag/','/2-spieltag/')
    else:now=datetime(2013,6,30,tzinfo=timezone.utc)
    with pytest.raises(UpdateError) as exc:parse_closed_clubs(mapping,data,now)
    assert exc.value.code=='dfb_club_calendar_unproven'

def test_source_is_bound_to_article_club_and_season_before_use():
    from tests.stage23_helpers import MINI,snapshot
    from wiki_stats.dfb_club_cycle import club_secondary_scopes
    from wiki_stats.wikitext_parser import WikitextParser
    class Wiki:
        def get_page_identity(self,title):return {'title':title,'qid':'Q10'}
        def get_foreign_page_identity(self,title,language):return {'title':title,'qid':'Q10'}
    text=MINI.replace('Аугсбург (футбольный клуб)|Аугсбург','Кайзерслаутерн (футбольный клуб)|Кайзерслаутерн').replace('2025/26','2012/13')
    values,entities,issues,scopes=club_secondary_scopes(snapshot(text),MAPPING,
        SimpleNamespace(club_secondary_source=lambda pid:source()),Wiki(),WikitextParser(text).club_table(),NOW)
    assert not issues and values[('Q10','2012/13','Чемпионат')]['numbers']=={'appearances':7,'goals':1}
    assert scopes[0]['qid']=='Q10' and 'Кайзерслаутерн' in entities['Q10']['wikitext']
    class Foreign(Wiki):
        def get_foreign_page_identity(self,title,language):return {'title':title,'qid':'Q11'}
    assert club_secondary_scopes(snapshot(text),MAPPING,SimpleNamespace(club_secondary_source=lambda pid:source()),Foreign(),WikitextParser(text).club_table(),NOW)[0]=={}

def test_verified_historical_league_repairs_stale_table_when_api_has_no_season(monkeypatch):
    from tests.test_sofascore import API,prepare
    from tests.stage23_helpers import MINI
    actual=parse_closed_clubs(MAPPING,source(),NOW)[0]
    monkeypatch.setattr('wiki_stats.dfb_club_cycle.club_secondary_scopes',lambda *a,**k:(
        {('Q15755','2012/13','Чемпионат'):actual},{'Q15755':{'name':'Аугсбург'}},[],[actual]))
    class Missing(API):
        def statistics(self,*args):raise UpdateError('api_unavailable','No primary season')
    text=MINI.replace('2025/26','2012/13')
    _,plan,report=prepare(text,Missing())
    changes=[c for c in plan.changes if c.operation.get('season')=='2012/13' and c.operation['type']=='update_stats']
    assert len(changes)==1 and changes[0].status=='ready'
    assert changes[0].operation['new']=={'appearances':7,'goals':1}
    assert changes[0].source_verification=='dfb_collected_scope_checked'
    assert '{{КлСтат/Сезон|2012/13|7|1|' in plan.preview
    assert 'Неприкосновенный источник' in plan.preview and '<!-- статистика -->' in plan.preview
    # The synthetic prepare helper inserts its own DOB on every invocation.
    # Remove that helper-only field before replaying; production leaves DOB intact.
    _,replay,_=prepare(plan.preview.replace('| дата рождения = 22.08.1999\n',''),Missing())
    assert not replay.diff
