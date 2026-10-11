from pathlib import Path
from datetime import datetime,timezone
import gzip,json
import pytest
from wiki_stats.dfb_club_cup_calendar import parse_cup_calendar
from wiki_stats.dfb_club_profile import parse_closed_clubs
from wiki_stats.errors import UpdateError
from tests.test_dfb_club_calendar import MAPPING,NOW,source as league_source

def source():
    return json.loads(gzip.decompress((Path(__file__).parent/'source-fixtures/dfb-orban-kaiserslautern-cup-2012.json.gz').read_bytes()))

def test_both_complete_protocols_and_round_two_elimination_prove_zero_played_caps():
    scope=parse_cup_calendar(MAPPING,source(),NOW)[0]
    assert scope['numbers']=={'appearances':0,'goals':0}
    assert scope['complete'] is True and scope['category']=='Национальный кубок'
    assert scope['confirmed_calendar_match_ids']==[1005991,1544345]
    assert scope['verified_absence_match_ids']==[1005991,1544345]
    assert scope['elimination_match_id']==1544345
    assert scope['matches']==[] and len(scope['sources'])==3

@pytest.mark.parametrize('fault',['pid','float_pid','qid','dob','name','bundle_pid','foreign_calendar','roster_dob',
    'calendar_title','missing_protocol','duplicate_protocol','bool_id','foreign_protocol','missing_lineup',
    'missing_bench','missing_substitutions','missing_goals','wrong_score','wrong_half','wrong_round',
    'no_elimination','first_round_loss','calendar_date','calendar_kickoff','not_closed'])
def test_a_partial_cup_calendar_or_protocol_cannot_turn_unknown_statistics_into_zero(fault):
    data=source();mapping=dict(MAPPING);now=NOW
    p=data['protocols'][1]
    if fault=='pid':mapping['api_id']=228981
    elif fault=='float_pid':mapping['api_id']=136924.0
    elif fault=='qid':mapping['qid']='Q1'
    elif fault=='dob':mapping['birth_date']='1992-11-04'
    elif fault=='name':mapping['source_name']='Tim Kleindienst'
    elif fault=='bundle_pid':data['player_id']=True
    elif fault=='foreign_calendar':data['calendar']['url']=data['calendar']['url'].replace('dfb.de','example.com')
    elif fault=='roster_dob':data['calendar']['html']=data['calendar']['html'].replace('03.11.1992','04.11.1992')
    elif fault=='calendar_title':data['calendar']['html']=data['calendar']['html'].replace('DFB-Pokal 2012/13','DFB-Pokal 2013/14')
    elif fault=='missing_protocol':data['protocols'].pop()
    elif fault=='duplicate_protocol':data['protocols'][1]=data['protocols'][0]
    elif fault=='bool_id':p['match_id']=True
    elif fault=='foreign_protocol':p['url']=p['url'].replace('dfb.de','example.com')
    elif fault=='missing_lineup':p['html']=p['html'].replace('Aufstellung','Other')
    elif fault=='missing_bench':p['html']=p['html'].replace('Auswechselspieler','Other')
    elif fault=='missing_substitutions':p['html']=p['html'].replace('>Wechsel<','>Other<')
    elif fault=='missing_goals':p['html']=p['html'].replace('>Tore<','>Other<')
    elif fault=='wrong_score':p['html']=p['html'].replace('4:0','5:0')
    elif fault=='wrong_half':p['html']=p['html'].replace('(1:0)','(5:0)')
    elif fault=='wrong_round':data['calendar']['html']=data['calendar']['html'].replace('/2-runde/','/1-runde/')
    elif fault=='no_elimination':data['calendar']['html']=data['calendar']['html'].replace('4 : 0','0 : 4')
    elif fault=='first_round_loss':data['calendar']['html']=data['calendar']['html'].replace('1 : 3','3 : 1')
    elif fault=='calendar_date':data['calendar']['html']=data['calendar']['html'].replace('31.10.2012','30.10.2012')
    elif fault=='calendar_kickoff':data['calendar']['html']=data['calendar']['html'].replace('20:30 Uhr','20:31 Uhr')
    else:now=datetime(2013,6,30,tzinfo=timezone.utc)
    with pytest.raises(UpdateError) as exc:parse_cup_calendar(mapping,data,now)
    assert exc.value.code=='dfb_club_cup_calendar_unproven'

def test_combined_club_source_retains_league_and_cup_with_distinct_category_proofs():
    bundle=league_source();bundle.update(kind='dfb_full_club_calendar_and_cup',cup=source())
    scopes=parse_closed_clubs(MAPPING,bundle,NOW)
    assert [(s['category'],s['numbers']) for s in scopes]==[
        ('Чемпионат',{'appearances':7,'goals':1}),('Национальный кубок',{'appearances':0,'goals':0})]
    assert set(scopes[0]['sources']).isdisjoint(scopes[1]['sources'])
    bundle['cup']['protocols'].pop()
    with pytest.raises(UpdateError):parse_closed_clubs(MAPPING,bundle,NOW)

def test_primary_cup_zero_repairs_stale_nonzero_cell_and_preserves_text_on_replay(monkeypatch):
    from tests.test_sofascore import API,prepare
    from tests.stage23_helpers import MINI
    actual=parse_cup_calendar(MAPPING,source(),NOW)[0]
    monkeypatch.setattr('wiki_stats.dfb_club_cycle.club_secondary_scopes',lambda *a,**k:(
        {('Q15755','2012/13','Национальный кубок'):actual},{'Q15755':{'name':'Аугсбург'}},[],[actual]))
    class Missing(API):
        def statistics(self,*args):raise UpdateError('api_unavailable','No primary season')
    text=MINI.replace('2025/26','2012/13').replace('{{КлСтат/Сезон|2012/13|4|1|0|0|','{{КлСтат/Сезон|2012/13|4|1|9|3|')
    _,plan,_=prepare(text,Missing())
    changes=[c for c in plan.changes if c.operation.get('competition',{}).get('category')=='Национальный кубок' and c.operation['type']=='update_stats']
    assert len(changes)==1 and changes[0].status=='ready'
    assert changes[0].operation['new']=={'appearances':0,'goals':0}
    assert changes[0].source_verification=='dfb_collected_scope_checked'
    assert 'Неприкосновенный источник' in plan.preview and '<!-- статистика -->' in plan.preview
    _,replay,_=prepare(plan.preview.replace('| дата рождения = 22.08.1999\n',''),Missing())
    assert not replay.diff
