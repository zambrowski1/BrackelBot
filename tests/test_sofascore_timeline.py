# SPDX-License-Identifier: MIT
from copy import deepcopy
from pathlib import Path
import json
import pytest
from wiki_stats.sofascore_timeline import timeline_numbers
from wiki_stats.sofascore_match_stats import reconstruct
from wiki_stats.errors import UpdateError

SAMPLES=json.loads((Path(__file__).parent/'api_fixtures/timeline_probes.json').read_text(encoding='utf-8'))
CUP_SAMPLES=json.loads((Path(__file__).parent/'api_fixtures/cup_bench_probes.json').read_text(encoding='utf-8'))
STOPPAGE_SAMPLE=json.loads((Path(__file__).parent/'api_fixtures/timeline_stoppage_probe.json').read_text(encoding='utf-8'))


def evaluate(sample):
    return timeline_numbers(sample['match'],sample['pid'],sample['match']['player']['team_id'],
        sample['lineups'],sample['incidents'],goalkeeper=sample['pid']==556866)


@pytest.mark.parametrize('index',[0,1])
def test_real_cup_bench_including_extra_time(index):
    assert evaluate(CUP_SAMPLES[index])=={'appearances':0,'goals':0}


def test_extra_time_requires_consistent_normal_and_final_scores():
    x=deepcopy(CUP_SAMPLES[0])
    next(e for e in x['incidents']['incidents'] if e.get('text')=='FT')['home_score']=9
    assert evaluate(x) is None


def test_actual_raum_substitution_includes_stoppage_minutes():
    assert evaluate(STOPPAGE_SAMPLE)=={'appearances':1,'goals':0}


def test_actual_raum_entry_and_complete_goals_do_not_require_announced_stoppage():
    x=deepcopy(STOPPAGE_SAMPLE)
    x['incidents']['incidents']=[e for e in x['incidents']['incidents'] if e['type']!='injuryTime']
    x['incidents']['count']=len(x['incidents']['incidents'])
    assert evaluate(x)=={'appearances':1,'goals':0}


@pytest.mark.parametrize('index,expected',[(0,{'appearances':1,'goals':0}),
    (1,{'appearances':0,'goals':0}),(2,{'appearances':1,'goals':0}),(3,{'appearances':1,'goals':-1})])
def test_live_qualification_bench_clean_sheet_and_keeper_substitution(index,expected):
    assert evaluate(SAMPLES[index])==expected


@pytest.mark.parametrize('problem',['unconfirmed','wrong_match','incomplete_goals','missing_substitution',
    'bad_score','shootout','duplicate_player','unknown_class','same_minute'])
def test_ambiguous_timeline_does_not_supply_zero(problem):
    x=deepcopy(SAMPLES[3]);events=x['incidents']['incidents']
    if problem=='unconfirmed':x['lineups']['is_confirmed']=False
    elif problem=='wrong_match':x['incidents']['match_id']=1
    elif problem=='incomplete_goals':events.remove(next(e for e in events if e['type']=='goal'))
    elif problem=='missing_substitution':events.remove(next(e for e in events if e['type']=='substitution' and e.get('player_out',{}).get('id')==x['pid']))
    elif problem=='bad_score':x['match']['home_score']['current']=2
    elif problem=='shootout':next(e for e in events if e['text']=='FT')['text']='AP'
    elif problem=='duplicate_player':x['lineups']['home']['players'].extend(deepcopy(x['lineups']['away']['players']))
    elif problem=='unknown_class':next(e for e in events if e['type']=='goal')['class']='unknown'
    else:
        next(e for e in events if e['type']=='substitution' and e.get('player_out',{}).get('id')==x['pid'])['time']=14
        x['lineups']['away']['players'][0]['statistics']['minutes_played']=14
    x['incidents']['count']=len(events)
    assert evaluate(x) is None


def test_own_goal_is_not_a_scoring_players_goal():
    x=deepcopy(SAMPLES[0]);g=next(e for e in x['incidents']['incidents'] if e['type']=='goal')
    g['class']='ownGoal';g['player']={'id':x['pid']}
    assert evaluate(x)=={'appearances':1,'goals':0}


@pytest.mark.parametrize('code',['api_not_found','source_cache_missing'])
def test_fallback_works_when_personal_statistics_are_unavailable(code):
    x=SAMPLES[0]
    class API:
        def match_statistics(self,*args):raise UpdateError(code,'no personal statistics')
        def match_lineups(self,*args):return x['lineups']
        def match_incidents(self,*args):return x['incidents']
    record={'team_id':x['match']['player']['team_id']}
    budget={'remaining':3}
    assert reconstruct(API(),x['pid'],record,[x['match']],budget)=={'appearances':1,'goals':0}
    assert budget['remaining']==0

