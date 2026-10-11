# SPDX-License-Identifier: MIT
from copy import deepcopy
from datetime import datetime,timezone

import pytest

from tests.test_sofascore import match
from tests.test_sofascore_calendar import historical_failure
from wiki_stats.errors import UpdateError
from wiki_stats.sofascore_national import collect_national_scope

NOW=datetime(2026,10,11,tzinfo=timezone.utc)


class NationalAPI:
    def __init__(self):
        self.rows=[match(1),match(2)]
        for row in self.rows:
            for side in ('home_team','away_team'):
                row[side]['is_national']=True;row[side]['sport']={'slug':'football'}
            row['home_score']={'current':2};row['away_score']={'current':3}
        self.expected={'player_id':1,'statistics':[{'team':self.rows[0]['home_team'],
                       'appearances':2,'goals':2}]}
        self.calls=[]
    def national_statistics(self,pid):self.calls.append('aggregate');return self.expected
    def match_statistics(self,pid,mid):
        self.calls.append(mid)
        return {'match_id':mid,'player':{'id':pid},'team':{'id':2600},
                'statistics':{'minutes_played':90,'goals':1,'goals_conceded':3}}


def evaluate(api,budget=None,**kwargs):
    return collect_national_scope(1,2600,{'complete':True,'matches':api.rows},api,
                                  budget if budget is not None else {'remaining':10},NOW,**kwargs)


def test_full_scope_crosschecks_individual_matches_against_aggregate_and_orders_dates():
    api=NationalAPI();api.rows.reverse();budget={'remaining':3}
    result=evaluate(api,budget)
    assert result['complete'] and result['numbers']=={'appearances':2,'goals':2}
    assert [r['match']['id'] for r in result['matches']]==[1,2]
    assert budget['remaining']==0


def test_goalkeeper_aggregate_scored_goals_is_never_used_as_conceded_total():
    api=NationalAPI();api.expected['statistics'][0]['goals']=0
    result=evaluate(api,goalkeeper=True)
    assert result['numbers']=={'appearances':2,'goals':-6}
    assert result['source_aggregate']['goals']==0


@pytest.mark.parametrize('problem',['missing_match','extra_match','duplicate','wrong_player',
    'wrong_team','club_opponent','ambiguous_aggregate','aggregate_goals','bool_apps',
    'naive_date','negative_score','over_scored','partial_history','budget'])
def test_incomplete_or_inconsistent_scope_never_exports_a_partial_list(problem):
    api=NationalAPI();budget={'remaining':10}
    if problem=='missing_match':api.rows.pop()
    elif problem=='extra_match':
        extra=deepcopy(api.rows[0]);extra['id']=3;api.rows.append(extra)
    elif problem=='duplicate':api.rows.append(deepcopy(api.rows[0]))
    elif problem=='wrong_player':api.expected['player_id']=99
    elif problem=='wrong_team':api.rows[1]['home_team']['id']=99
    elif problem=='club_opponent':api.rows[1]['away_team']['is_national']=False
    elif problem=='ambiguous_aggregate':api.expected['statistics'].append(deepcopy(api.expected['statistics'][0]))
    elif problem=='aggregate_goals':api.expected['statistics'][0]['goals']=3
    elif problem=='bool_apps':api.expected['statistics'][0]['appearances']=True
    elif problem=='naive_date':api.rows[0]['start_time']='2026-10-01T12:00:00'
    elif problem=='negative_score':api.rows[0]['away_score']['current']=-1
    elif problem=='over_scored':api.rows[0]['home_score']['current']=0
    elif problem=='budget':budget['remaining']=2
    if problem=='partial_history':
        assert collect_national_scope(1,2600,{'complete':False,'matches':api.rows},api,budget,NOW) is None
        assert not api.calls
    else:assert evaluate(api,budget) is None
    assert budget['failures']


def test_future_games_do_not_enter_the_national_total():
    api=NationalAPI();future=deepcopy(api.rows[0]);future.update(id=99,start_time='2026-11-01T12:00:00Z')
    api.rows.append(future)
    assert evaluate(api)['numbers']['appearances']==2
    assert 99 not in api.calls


def test_actual_unnamed_player_is_excluded_even_if_scoring_events_are_missing():
    row,lineup,timeline=historical_failure()
    for side in ('home_team','away_team'):row[side]['is_national']=True
    class AbsentAPI:
        def national_statistics(self,pid):
            return {'player_id':pid,'statistics':[{'team':next(row[k] for k in ('home_team','away_team')
                if row[k]['id']==2675),'appearances':0,'goals':0}]}
        def match_statistics(self,*args):raise UpdateError('api_not_found','No personal match statistics')
        def match_lineups(self,mid):return lineup
        def match_incidents(self,mid):return timeline
    row['player']={'team_id':2675}
    result=collect_national_scope(136924,2675,{'complete':True,'matches':[row]},AbsentAPI(),
                                 {'remaining':4},NOW)
    assert result['matches']==[] and result['verified_absence_match_ids']==[1867301]


def test_quota_failure_refuses_the_entire_scope_and_preserves_the_guard():
    class Limited(NationalAPI):
        def match_statistics(self,*args):raise UpdateError('quota_exhausted','Free limit')
    budget={'remaining':10}
    assert evaluate(Limited(),budget) is None and budget['remaining']==0


@pytest.mark.parametrize('problem',[None,'budget','aggregate'])
def test_actual_orban_full_history_and_public_match_responses_prove_72_appearances_and_six_goals(problem):
    import gzip,hashlib,json
    from pathlib import Path
    from wiki_stats.sofascore_client import _SofascoreAccount
    data=json.loads(gzip.decompress((Path(__file__).parent/'source-fixtures/orban-national-source.json.gz').read_bytes()))
    if problem=='aggregate':data['national']['statistics'][0]['goals']=7
    lookup={r['cache_key']:r['data'] for r in data['responses']}
    class ActualReplay(_SofascoreAccount):
        def __init__(self):pass
        def national_statistics(self,pid):
            assert pid==136924
            return data['national']
        def request(self,path,params,**kwargs):
            key=hashlib.sha256(json.dumps([path,params],sort_keys=True).encode()).hexdigest()
            if key not in lookup:raise UpdateError('source_cache_missing','No external API requests in test')
            return lookup[key]
    budget={'remaining':20 if problem=='budget' else 500}
    result=collect_national_scope(136924,4709,data['history'],ActualReplay(),budget,NOW)
    if problem:
        assert result is None
        assert any(f['code']==('match_request_cap' if problem=='budget' else 'national_aggregate_mismatch')
                   for f in budget['failures'])
    else:
        assert result['complete'] and result['numbers']=={'appearances':72,'goals':6}
        assert len(result['matches'])==72 and len({m['match']['id'] for m in result['matches']})==72
        assert not budget.get('failures')
