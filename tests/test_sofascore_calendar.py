# SPDX-License-Identifier: MIT
import json
import gzip
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import pytest

from wiki_stats.sofascore_calendar import recover_calendar, recover_european_calendar, confirmed_match_sheet_absence
from wiki_stats.sofascore_timeline import timeline_numbers
from wiki_stats.errors import UpdateError
from wiki_stats.sofascore_client import _SofascoreAccount


class CalendarAPI:
    def __init__(self):
        self.data=json.loads((Path(__file__).parent/'source-fixtures/raum-cup-2016-calendar.json').read_text(encoding='utf-8'))
        self.calls=[]
    def tournament_seasons(self, tid):
        self.calls.append(('seasons',tid));return self.data['season_index']
    def tournament_matches(self, tid, sid, *, request_budget):
        self.calls.append(('calendar',tid,sid));request_budget['remaining']-=3
        return {'tournament_id':tid,'season_id':sid,'complete':True,'matches':self.data['calendar']}
    def match_lineups(self, mid):
        self.calls.append(('roster',mid));return next(r['lineup'] for r in self.data['matches'] if r['match']['id']==mid)
    def match_incidents(self, mid):
        self.calls.append(('events',mid));return next(r['timeline'] for r in self.data['matches'] if r['match']['id']==mid)


def evaluate(api, budget=None, now=None):
    anchor={'season':'2016/17','team_id':2551,'numbers':{'appearances':5,'goals':0}}
    return recover_calendar(anchor,856076,api,budget or {'remaining':20},
                            now or datetime(2026,10,11,tzinfo=timezone.utc),217)


def test_real_raum_calendar_proves_zero_across_all_three_club_games():
    api=CalendarAPI();budget={'remaining':20};value=evaluate(api,budget)
    assert value['numbers']=={'appearances':0,'goals':0}
    assert set(value['match_ids'])=={7192634,7264722,7093256}
    assert len(value['calendar_match_ids'])==64 and budget['remaining']==10
    assert len(api.calls)==8


@pytest.mark.parametrize('problem', ['negative_id','duplicate','wrong_season','wrong_tournament',
    'naive_date','out_of_season','future_date','wrong_sport','unfinished','missing_starter',
    'duplicate_roster','unconfirmed','target_in_event','incomplete_score','bad_index','two_seasons'])
def test_corrupt_calendar_evidence_never_supplies_zero(problem):
    api=CalendarAPI();row=api.data['calendar'][0];sample=api.data['matches'][0]
    if problem=='negative_id':row['id']=-1
    elif problem=='duplicate':api.data['calendar'].append(deepcopy(row))
    elif problem=='wrong_season':row['season']['id']=999
    elif problem=='wrong_tournament':row['tournament']['id']=7
    elif problem=='naive_date':row['start_time']='2016-10-26T16:30:00'
    elif problem=='out_of_season':row['start_time']='2015-10-26T16:30:00Z'
    elif problem=='future_date':row['start_time']='2027-10-26T16:30:00Z'
    elif problem=='wrong_sport':row['home_team']['sport']['slug']='basketball'
    elif problem=='unfinished':
        target=next(r for r in api.data['calendar'] if r['id']==sample['match']['id'])
        target['is_finished']=False
    elif problem=='missing_starter':sample['lineup']['home']['players'].pop(0)
    elif problem=='duplicate_roster':sample['lineup']['home']['players'].append(deepcopy(sample['lineup']['home']['players'][-1]))
    elif problem=='unconfirmed':sample['lineup']['is_confirmed']=False
    elif problem=='target_in_event':
        sample['timeline']['incidents'].append({'type':'card','player':{'id':856076}})
        sample['timeline']['count']+=1
    elif problem=='incomplete_score':
        # A named substitute needs event evidence to establish nonparticipation.
        own='home' if sample['match']['home_team']['id']==2551 else 'away'
        bench=next(p for p in sample['lineup'][own]['players'] if p['is_substitute'])
        bench['player']['id']=856076
        goal=next(e for e in sample['timeline']['incidents'] if e.get('type')=='goal')
        sample['timeline']['incidents'].remove(goal);sample['timeline']['count']-=1
    elif problem=='bad_index':api.data['season_index']['tournament_id']=7
    else:
        season=next(s for s in api.data['season_index']['seasons'] if s['year']=='16/17')
        api.data['season_index']['seasons'].append(deepcopy(season));api.data['season_index']['count']+=1
    assert evaluate(api) is None


def test_open_season_and_small_budget_do_not_create_partial_numbers():
    api=CalendarAPI()
    assert evaluate(api,now=datetime(2017,1,1,tzinfo=timezone.utc)) is None
    assert not api.calls
    api=CalendarAPI();budget={'remaining':9}
    assert evaluate(api,budget) is None and budget['failures'][-1]['code']=='match_request_cap'
    assert not any(c[0]=='roster' for c in api.calls)


def test_quota_failure_does_not_become_zero():
    class Failed(CalendarAPI):
        def match_lineups(self,mid):raise UpdateError('quota_exhausted','quota')
    budget={'remaining':20}
    assert evaluate(Failed(),budget) is None
    assert budget['remaining']==0 and budget['failures'][-1]['code']=='quota_exhausted'


def test_absent_player_requires_explicit_calendar_mode():
    sample=CalendarAPI().data['matches'][0]
    assert timeline_numbers(sample['match'],856076,2551,sample['lineup'],sample['timeline']) is None
    assert timeline_numbers(sample['match'],856076,2551,sample['lineup'],sample['timeline'],allow_absent=True)=={'appearances':0,'goals':0}


class PageAPI(_SofascoreAccount):
    def __init__(self):
        rows=CalendarAPI().data['calendar'];self.calls=[]
        self.pages=[{'tournament_id':217,'season_id':11746,'page':i+1,
                     'count':len(rows[i*30:(i+1)*30]),'matches':rows[i*30:(i+1)*30],
                     'has_next_page':i<2} for i in range(3)]
    def request(self,path,params,**kwargs):
        self.calls.append((path,params));return self.pages[params['page']-1]


def test_actual_calendar_pagination_counts_every_request_and_finishes():
    api=PageAPI();budget={'remaining':3}
    value=api.tournament_matches(217,11746,request_budget=budget)
    assert value['complete'] and len(value['matches'])==64
    assert budget['remaining']==0 and len(api.calls)==3
    assert all(c[0]=='/tournaments/matches' and c[1]['span']=='last' for c in api.calls)


@pytest.mark.parametrize('problem',['duplicate','count','wrong_season','wrong_page','boolean_tournament','empty_intermediate'])
def test_bad_calendar_pages_are_not_marked_complete(problem):
    api=PageAPI();d=api.pages[1]
    if problem=='duplicate':d['matches'][0]=deepcopy(api.pages[0]['matches'][0])
    elif problem=='count':d['count']-=1
    elif problem=='wrong_season':d['season_id']=999
    elif problem=='wrong_page':d['page']=1
    elif problem=='boolean_tournament':d['tournament_id']=True
    else:d['matches']=[];d['count']=0
    with pytest.raises(UpdateError) as exc:api.tournament_matches(217,11746)
    assert exc.value.code=='incomplete_matches'


def test_page_limit_and_budget_limit_preserve_incomplete_status():
    api=PageAPI()
    assert api.tournament_matches(217,11746,max_pages=2)['complete'] is False
    api=PageAPI()
    with pytest.raises(UpdateError) as exc:api.tournament_matches(217,11746,request_budget={'remaining':2})
    assert exc.value.code=='match_request_cap' and len(api.calls)==2


class EuropeanAPI(CalendarAPI):
    def __init__(self):
        self.data=json.loads(gzip.decompress((Path(__file__).parent/'source-fixtures/raum-europe-2016.json.gz').read_bytes()))
        self.calls=[]
    def tournament_seasons(self,tid):
        self.calls.append(('seasons',tid));return next(x for x in self.data['indices'] if x['tournament_id']==tid)
    def tournament_matches(self,tid,sid,*,request_budget):
        self.calls.append(('calendar',tid,sid));scope=next(s for s in self.data['scopes'] if s['tournament_id']==tid)
        request_budget['remaining']-=len(scope['pages'])
        return {'tournament_id':tid,'season_id':sid,'complete':scope['complete'],'matches':scope['matches']}


def evaluate_europe(api):
    return recover_european_calendar({'season':'2016/17','team_id':2551,'numbers':{'appearances':5,'goals':0}},
        856076,api,{'remaining':100},datetime(2026,10,11,tzinfo=timezone.utc))


def test_real_full_european_calendars_confirm_fuerth_nonparticipation():
    api=EuropeanAPI();value=evaluate_europe(api)
    assert value['numbers']=={'appearances':0,'goals':0}
    assert [(x['tournament_id'],len(x['calendar_match_ids'])) for x in value['calendars']]==[(7,217),(679,469)]
    assert all(not x['match_ids'] for x in value['calendars'])
    assert len(api.calls)==4


def test_missing_second_european_calendar_never_returns_first_subtotal():
    api=EuropeanAPI();api.data['scopes'][1]['complete']=False
    assert evaluate_europe(api) is None


@pytest.mark.parametrize('season',['2008/09','2021/22','2026/27'])
def test_two_competitions_cannot_prove_other_historical_or_conference_seasons(season):
    api=EuropeanAPI()
    assert recover_european_calendar({'season':season,'team_id':2551,'numbers':{'appearances':5,'goals':0}},
        856076,api,{'remaining':100},datetime(2026,10,11,tzinfo=timezone.utc)) is None
    assert not api.calls


def historical_failure():
    data=json.loads((Path(__file__).parent/'source-fixtures/calendar-failures.json').read_text(encoding='utf-8'))
    row=next(r for r in data['matches'] if r['id']==1867301)
    lineup=next(r for r in data['responses'] if 'home' in r)
    timeline=next(r for r in data['responses'] if 'incidents' in r)
    return row,lineup,timeline


def test_actual_unnamed_orban_is_proven_absent_independently_of_missing_goal_event():
    row,lineup,timeline=historical_failure()
    assert timeline_numbers(row,136924,2675,lineup,timeline,allow_absent=True) is None
    assert confirmed_match_sheet_absence(row,136924,2675,lineup,timeline)


@pytest.mark.parametrize('problem',['starter','bench','partial_roster','duplicate','unconfirmed','contradictory_event','wrong_match'])
def test_match_sheet_absence_does_not_ignore_ambiguous_or_named_player(problem):
    row,lineup,timeline=historical_failure()
    if problem in ('starter','bench'):
        p=next(p for p in lineup['home']['players'] if p['is_substitute']==(problem=='bench'))
        p['player']['id']=136924
    elif problem=='partial_roster':lineup['home']['players'].pop(0)
    elif problem=='duplicate':lineup['home']['players'].append(deepcopy(lineup['home']['players'][-1]))
    elif problem=='unconfirmed':lineup['is_confirmed']=False
    elif problem=='contradictory_event':
        timeline['incidents'].append({'type':'substitution','player_in':{'id':136924}});timeline['count']+=1
    else:timeline['match_id']=999
    assert not confirmed_match_sheet_absence(row,136924,2675,lineup,timeline)


def test_actual_2020_july_final_keeps_explicit_2019_season_scope():
    data=json.loads((Path(__file__).parent/'source-fixtures/calendar-failures.json').read_text(encoding='utf-8'))
    final=next(r for r in data['matches'] if r['id']==8693646)
    class JulyAPI(CalendarAPI):
        def tournament_matches(self,tid,sid,*,request_budget):
            return {'tournament_id':tid,'season_id':sid,'complete':True,'matches':[final]}
    api=JulyAPI()
    result=recover_calendar({'season':'2019/20','team_id':5885,'numbers':{'appearances':22,'goals':4}},
        228981,api,{'remaining':20},datetime(2026,10,11,tzinfo=timezone.utc),217)
    assert result['numbers']=={'appearances':0,'goals':0} and result['calendar_match_ids']==[8693646]
