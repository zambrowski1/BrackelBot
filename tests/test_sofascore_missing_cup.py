# SPDX-License-Identifier: MIT
from copy import deepcopy
from datetime import datetime,timezone
from tests.test_sofascore_timeline import CUP_SAMPLES
from wiki_stats.sofascore_missing_cup import recover_cup


def setup(index):
    sample=CUP_SAMPLES[index];cup=deepcopy(sample['match'])
    year=int('20'+cup['season']['year'][:2])
    league={'season':f'{year}/{(year+1)%100:02}','season_id':99,'tournament_id':44,
        'team_id':2551,'numbers':{'appearances':1,'goals':0}}
    played=deepcopy(cup);played['id']=1;played['tournament']['id']=44
    played['season']={'id':99,'year':cup['season']['year']};played['player']['statistics']={'minutes_played':90}
    before=deepcopy(played);before['id']=2;before['season']['id']=98;before['start_time']=f'{year-1}-08-01T12:00:00Z'
    after=deepcopy(played);after['id']=3;after['season']['id']=100;after['start_time']=f'{year+1}-08-01T12:00:00Z'
    class API:
        def match_lineups(self,mid):assert mid==cup['id'];return sample['lineups']
        def match_incidents(self,mid):assert mid==cup['id'];return sample['incidents']
    return {'complete':True,'matches':[before,played,cup,after]},league,API(),cup


def test_actual_confirmed_benches_supply_unindexed_cup_zero():
    for index in (0,1):
        history,league,api,cup=setup(index)
        value=recover_cup(history,league,856076,api,{'remaining':2},datetime(2026,10,10,tzinfo=timezone.utc))
        assert value==({'appearances':0,'goals':0},[cup['id']])


def test_incomplete_history_and_mismatched_anchor_are_not_zero():
    for problem in ('incomplete','count','budget'):
        history,league,api,_=setup(1);budget={'remaining':2}
        if problem=='incomplete':history['complete']=False
        if problem=='count':league['numbers']['appearances']=2
        if problem=='budget':budget['remaining']=1
        assert recover_cup(history,league,856076,api,budget,datetime(2026,10,10,tzinfo=timezone.utc)) is None
