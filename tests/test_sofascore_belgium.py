import gzip,json
from copy import deepcopy
from pathlib import Path
from datetime import datetime,timezone
import pytest
from wiki_stats.sofascore_cycle import _numbers,absent_category_zero,confirmed_match_date
from wiki_stats.sofascore_match_stats import played_matches
from tests.test_sofascore import API,prepare,match

NOW=datetime(2026,10,11,tzinfo=timezone.utc)

def fixture():
    with gzip.open(Path(__file__).parent/'source-fixtures/gent-season-2020-v82.json.gz','rt',encoding='utf-8') as f:
        x=json.load(f)
    _,numbers=_numbers(x['aggregate'],228981,38,28216,'overall')
    record={'season':'2020/21','team_id':2903,'tournament_id':38,'season_id':28216,'numbers':numbers}
    return x,record

def test_real_gent_aggregate_and_history_exclude_bench_friendlies_and_europe():
    x,r=fixture()
    assert r['numbers']=={'appearances':15,'goals':1}
    rows=played_matches(x['history'],r,NOW)
    assert len(rows)==15
    assert sum(m['player']['statistics']['minutes_played'] for m in rows)==565
    assert 8833065 not in {m['id'] for m in rows}
    assert 8833046 not in {m['id'] for m in rows}
    assert 8972333 not in {m['id'] for m in rows}
    # penalty_goals is a subset of goals, not another goal.
    assert x['aggregate']['statistics']['penalty_goals']==1
    assert r['numbers']['goals']==1

@pytest.mark.parametrize('fault',['missing_played','wrong_owner','duplicate_bench','unknown_minutes','incomplete','wrong_bench_flag'])
def test_real_gent_damaged_history_cannot_confirm_aggregate(fault):
    x,r=fixture();h=x['history']
    played=next(m for m in h['matches'] if m['id']==8833001)
    bench=next(m for m in h['matches'] if m['id']==8833065)
    if fault=='missing_played': h['matches'].remove(played)
    elif fault=='wrong_owner':played['home_team']['id']=played['away_team']['id']=99
    elif fault=='duplicate_bench':h['matches'].append(deepcopy(bench))
    elif fault=='unknown_minutes':played['player']['statistics']=None
    elif fault=='incomplete':h['complete']=False
    else:bench['player']['is_on_bench']=False
    assert played_matches(h,r,NOW) is None

@pytest.mark.parametrize('category',['Национальный кубок','Стыковые матчи','Прочие'])
def test_belgian_league_cannot_prove_domestic_zero_with_german_ids(category):
    x,r=fixture()
    assert absent_category_zero(x['history'],r,category,NOW) is None

@pytest.mark.parametrize('tid,count',[(38,1),(35,0)])
def test_belgian_league_playoffs_count_but_german_relegation_does_not(tid,count):
    m=match(tournament=tid);m['round']={'name':'Championship playoffs','cup_round_type':1}
    r={'season':'2026/27','team_id':2600,'tournament_id':tid,'season_id':26,'numbers':{'appearances':1,'goals':0}}
    h={'complete':True,'matches':[m]}
    assert len(played_matches(h,r,NOW))==count
    assert confirmed_match_date(h,{(2600,tid,26)},NOW)==('2026-10-08' if count else None)

class Belgium(API):
    def seasons(self,pid):
        return {'player_id':pid,'statistics_types':{'38':{'26':['overall','home','away']}},
                'tournament_seasons':[{'tournament':{'id':38},'seasons':[{'id':26,'year':'26/27'}]}]}
    def matches(self,pid,max_pages=12):
        return {'complete':True,'matches':[match(i,tournament=38) for i in range(1,6)]}

def test_pipeline_supports_belgian_league():
    _,plan,evidence=prepare(api=Belgium())
    assert not any(r['code'] in {'competition_unmapped','overall_league_unproven'} for r in evidence['review'])
    assert '{{КлСтат/Сезон|2026/27|5|2' in plan.preview

def test_advertised_overall_still_requires_complete_belgian_history():
    class Incomplete(Belgium):
        def matches(self,*args,**kwargs):return {'complete':False,'matches':[]}
    _,_,evidence=prepare(api=Incomplete())
    assert any(r['code']=='overall_league_unproven' for r in evidence['review'])
