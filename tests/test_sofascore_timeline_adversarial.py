# SPDX-License-Identifier: MIT
"""Independent football scenarios with known participation and goal ownership."""
from copy import deepcopy

import pytest

from wiki_stats.sofascore_timeline import timeline_numbers

PID=10


def scenario(*, keeper=False, substitute=False, minutes=None):
    row={'id':1,'is_finished':True,'status':{'type':'finished'},
         'home_team':{'id':100},'away_team':{'id':200},
         'home_score':{'current':0},'away_score':{'current':0}}
    player={'player':{'id':PID},'is_substitute':substitute,'position':'G' if keeper else 'F'}
    if minutes is not None:player['statistics']={'minutes_played':minutes}
    roster={'match_id':1,'is_confirmed':True,'home':{'players':[player]},'away':{'players':[]}}
    events=[{'type':'period','text':'FT','time':90,'is_live':False,'home_score':0,'away_score':0}]
    return row,roster,events


def goal(time,*,home=False,scorer=20,added=0,kind='regular'):
    return {'type':'goal','time':time,'added_time':added,'class':kind,
            'is_home':home,'player':{'id':scorer},'home_score':int(home),'away_score':int(not home)}


def substitution(time,*,incoming=False,added=0,home=True):
    return {'type':'substitution','time':time,'added_time':added,'is_home':home,
            'player_in':{'id':PID if incoming else 30},'player_out':{'id':30 if incoming else PID}}


def evaluate(data,*,keeper=False):
    row,roster,events=data
    goals=[e for e in events if e.get('type')=='goal']
    if goals:
        row['home_score']['current']=goals[-1]['home_score']
        row['away_score']['current']=goals[-1]['away_score']
        events[0].update(home_score=goals[-1]['home_score'],away_score=goals[-1]['away_score'])
    return timeline_numbers(row,PID,100,roster,{'match_id':1,'count':len(events),'incidents':events},goalkeeper=keeper)


@pytest.mark.parametrize('incoming',[False,True])
@pytest.mark.parametrize('goal_time,expected',[(29,-1),(30,None),(31,0)])
def test_keeper_change_assigns_conceded_goal_only_to_keeper_on_pitch(incoming,goal_time,expected):
    data=scenario(keeper=True,substitute=incoming)
    data[2].extend([substitution(30,incoming=incoming),goal(goal_time)])
    if incoming and expected is not None:expected=-1-expected
    assert evaluate(data,keeper=True)==(None if expected is None else {'appearances':1,'goals':expected})


@pytest.mark.parametrize('kind',['regular','penalty'])
def test_goal_by_target_player_cannot_belong_to_opponent(kind):
    data=scenario();data[2].append(goal(20,scorer=PID,kind=kind))
    assert evaluate(data) is None


def test_rescinded_red_card_does_not_end_keeper_participation():
    data=scenario(keeper=True)
    data[2].extend([{'type':'card','class':'red','time':30,'rescinded':True,'player':{'id':PID}},goal(60)])
    assert evaluate(data,keeper=True)=={'appearances':1,'goals':-1}


def test_substitute_minutes_may_include_confirmed_stoppage_time():
    data=scenario(substitute=True,minutes=13)
    data[2].extend([substitution(83,incoming=True),{'type':'injuryTime','time':90,'length':5}])
    assert evaluate(data)=={'appearances':1,'goals':0}


def test_reported_minutes_do_not_override_confirmed_entry_and_complete_goals():
    data=scenario(substitute=True,minutes=30);data[2].append(substitution(83,incoming=True))
    assert evaluate(data)=={'appearances':1,'goals':0}


@pytest.mark.parametrize('problem',['wrong_team','after_end','negative_added','boolean_added'])
def test_impossible_substitution_cannot_confirm_participation(problem):
    data=scenario();event=substitution(60)
    if problem=='wrong_team':event['is_home']=False
    elif problem=='after_end':event['time']=100
    elif problem=='negative_added':event['added_time']=-1
    else:event['added_time']=False
    data[2].append(event)
    assert evaluate(data) is None


@pytest.mark.parametrize('added',[0,1,5,10])
def test_goal_after_keeper_stoppage_time_substitution_is_not_his_conceded_goal(added):
    data=scenario(keeper=True);data[2].extend([substitution(90,added=added),goal(90,added=added+1)])
    assert evaluate(data,keeper=True)=={'appearances':1,'goals':0}


def test_duplicate_terminal_period_does_not_confirm_clean_sheet():
    data=scenario(keeper=True);data[2].append(deepcopy(data[2][0]))
    assert evaluate(data,keeper=True) is None
