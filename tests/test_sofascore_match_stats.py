# SPDX-License-Identifier: MIT
from copy import deepcopy
from datetime import datetime, timezone

import pytest

from tests.test_sofascore import API, match, prepare, Wiki, Resolver, snapshot
from wiki_stats.errors import UpdateError
from wiki_stats.sofascore_match_stats import played_matches, reconstruct

NOW = datetime(2026, 10, 9, tzinfo=timezone.utc)
RECORD = {'team_id':2600, 'season_id':26, 'season':'2026/27', 'tournament_id':35}


@pytest.mark.parametrize('problem',[None,'disabled','named_bench','partial_roster',
    'contradictory_event','wrong_match','quota'])
def test_candidate_history_excludes_only_independently_proven_absence(problem):
    from tests.test_sofascore_calendar import historical_failure
    row,lineup,timeline=historical_failure()
    if problem=='named_bench':
        next(p for p in lineup['home']['players'] if p['is_substitute'])['player']['id']=136924
    elif problem=='partial_roster':lineup['home']['players'].pop(0)
    elif problem=='contradictory_event':
        timeline['incidents'].append({'type':'substitution','player_in':{'id':136924}})
        timeline['count']+=1
    elif problem=='wrong_match':timeline['match_id']=999
    class CandidateAPI:
        def match_statistics(self,pid,mid):
            raise UpdateError('quota_exhausted' if problem=='quota' else 'api_not_found','Unavailable')
        def match_lineups(self,mid):return lineup
        def match_incidents(self,mid):return timeline
    budget={'remaining':3}
    result=reconstruct(CandidateAPI(),136924,{'team_id':2675,'season':'2011/12','tournament_id':217},
                       [row],budget,allow_absent=problem!='disabled')
    if problem is None:
        assert result=={'appearances':0,'goals':0}
        assert budget['roster_proven_absences']==[1867301]
    else:
        assert result is None
        assert not budget.get('roster_proven_absences')
    if problem=='quota':assert budget['remaining']==0


@pytest.mark.parametrize('limit',[-1,2001,True,'160',None])
def test_invalid_recovery_limit_is_rejected_before_source_requests(limit):
    from wiki_stats.sofascore_cycle import prepare_player
    class NoCalls:
        def __getattr__(self,name):raise AssertionError('Invalid limit must not request source data')
    with pytest.raises(UpdateError) as error:
        prepare_player(snapshot(),{},NoCalls(),Wiki(),Resolver(),max_match_requests=limit,now=NOW)
    assert error.value.code=='request_limit_invalid'


@pytest.mark.parametrize('limit,recovered',[(0,False),(6,True)])
def test_recovery_budget_is_configurable_and_zero_does_not_fabricate_totals(limit,recovered):
    from wiki_stats.sofascore_cycle import prepare_player
    class Split(MatchAPI):
        def transfers(self,pid):
            return {'player_id':pid,'transfers':[{'transfer_date':'2026-08-01',
                'transfer_from':{'id':99},'transfer_to':{'id':2600}}]}
        def matches(self,*args,**kwargs):return {'complete':True,'matches':[match(1),match(2)]}
    mapping={'api_id':1,'qid':'Q114406918','title':'Игрок','wiki_name':'Кальвин Браккельман','birth_date':'1999-08-22'}
    text=snapshot().text.replace('| имя =','| дата рождения = 22.08.1999\n| имя =').replace('2025/26','2026/27')
    _,plan,report=prepare_player(snapshot(text),mapping,Split(),Wiki(),Resolver(),max_match_requests=limit,now=NOW)
    assert any(i['code']=='match_statistics_reconstructed' for i in report['review'])==recovered
    if not recovered:assert any(f['code']=='match_request_cap' for f in report['match_recovery_failures'])


@pytest.mark.parametrize('limit,expected',[(80,None),(160,{'appearances':34,'goals':0})])
def test_full_keeper_season_can_fit_and_reports_adapter_cap(limit,expected):
    class Keeper(MatchAPI):
        def match_statistics(self,pid,mid):
            data=super().match_statistics(pid,mid);data['statistics'].pop('goals_conceded');return data
        def match_lineups(self,mid):
            return {'match_id':mid,'is_confirmed':True,'home':{'players':[
                {'player':{'id':1},'position':'G','is_substitute':False}]},'away':{'players':[]}}
        def match_incidents(self,mid):
            return {'match_id':mid,'count':1,'incidents':[{'type':'period','text':'FT','is_live':False,
                'time':90,'home_score':0,'away_score':0}]}
    rows=[match(mid=i) for i in range(1,35)]
    for row in rows:row.update(home_score={'current':0},away_score={'current':0})
    budget={'remaining':limit}
    assert reconstruct(Keeper(),1,RECORD,rows,budget,goalkeeper=True)==expected
    if expected is None:assert budget['failures'][-1]['code']=='match_request_cap'
    else:assert not budget.get('failures')


class MatchAPI(API):
    def match_statistics(self, pid, mid):
        return {'match_id':mid, 'player':{'id':pid}, 'team':{'id':2600},
            'statistics':{'minutes_played':90, 'goals':1, 'goals_conceded':2}}


@pytest.mark.parametrize('goalkeeper,expected', [(False,2), (True,-4)])
def test_explicit_match_metrics_are_summed_with_keeper_sign(goalkeeper, expected):
    rows = played_matches({'complete':True,'matches':[match(1),match(2)]}, RECORD, NOW)
    if goalkeeper:
        for row in rows:row.update(home_score={'current':2},away_score={'current':2})
    budget = {'remaining':2}
    assert reconstruct(MatchAPI(), 1, RECORD, rows, budget, goalkeeper=goalkeeper) == {'appearances':2,'goals':expected}
    assert budget['remaining']==0


@pytest.mark.parametrize('field,value', [('goals',None),('goals',False),('goals',-1),('minutes_played',float('nan'))])
def test_missing_or_invalid_metrics_are_not_zero(field, value):
    class Bad(MatchAPI):
        def match_statistics(self,*args):
            data=super().match_statistics(*args);data['statistics'][field]=value;return data
    assert reconstruct(Bad(),1,RECORD,[match()],{'remaining':1}) is None


def test_foreign_team_response_and_quota_failure_do_not_produce_partial_total():
    class Bad(MatchAPI):
        def match_statistics(self,*args):
            data=super().match_statistics(*args);data['team']['id']=99;return data
    assert reconstruct(Bad(),1,RECORD,[match()],{'remaining':1}) is None
    class Limited(MatchAPI):
        def match_statistics(self,*args):raise UpdateError('quota_exhausted','limit')
    budget={'remaining':5}
    assert reconstruct(Limited(),1,RECORD,[match()],budget) is None and budget['remaining']==0


def test_budget_is_checked_before_any_request():
    class NoCall(MatchAPI):
        def match_statistics(self,*args):raise AssertionError('budget exceeded')
    assert reconstruct(NoCall(),1,RECORD,[match(1),match(2)],{'remaining':1}) is None


@pytest.mark.parametrize('problem', ['incomplete','minutes','date','team','duplicate'])
def test_ambiguous_history_is_refused(problem):
    row=match();history={'complete':True,'matches':[row]}
    if problem=='incomplete':history['complete']=False
    elif problem=='minutes':row['player']['statistics']['minutes_played']=None
    elif problem=='date':row['start_time']='2026-10-08'
    elif problem=='team':row['home_team']['id']=99
    else:history['matches'].append(deepcopy(row))
    assert played_matches(history,RECORD,NOW) is None


def test_qualifying_matches_with_distinct_season_id_are_included():
    record={**RECORD,'tournament_id':679}
    main=match(1,tournament=679);qualifier=match(2,tournament=679)
    qualifier['season']={'id':999,'year':'26/27'}
    assert len(played_matches({'complete':True,'matches':[main,qualifier]},record,NOW,european=True))==2


def test_cup_knockout_round_is_not_a_league_playoff():
    row=match(tournament=217);row['round']={'cup_round_type':1}
    record={**RECORD,'tournament_id':217}
    assert len(played_matches({'complete':True,'matches':[row]},record,NOW,league=False))==1


@pytest.mark.parametrize('european', [False, True])
def test_bad_date_in_other_known_season_does_not_block_target(european):
    tid=679 if european else 35
    current=match(1,tournament=tid)
    old=match(2,tournament=tid);old['season']={'id':25,'year':'25/26'};old['start_time']='bad-date'
    rows=played_matches({'complete':True,'matches':[old,current]}, {**RECORD,'tournament_id':tid}, NOW, european=european)
    assert rows==[current]


def test_pipeline_recovers_own_club_instead_of_season_aggregate():
    class Split(MatchAPI):
        def transfers(self,pid):
            return {'player_id':pid,'transfers':[{'transfer_date':'2026-08-01',
                'transfer_from':{'id':99},'transfer_to':{'id':2600}}]}
        def matches(self,*args,**kwargs):
            return {'complete':True,'matches':[match(1),match(2)]}
    _,plan,report=prepare(api=Split())
    assert any(r['code']=='match_statistics_reconstructed' for r in report['review'])
    assert not any(r['code']=='multi_club_season_unproven' for r in report['review'])
    assert any(c.operation.get('new')=={'appearances':2,'goals':2} and c.status=='ready' for c in plan.changes)


def test_pipeline_european_scope_ignores_bad_date_in_another_season():
    class Europe(API):
        def seasons(self,pid):
            data=super().seasons(pid)
            data['tournament_seasons'].append({'tournament':{'id':679},'seasons':[{'id':26,'year':'26/27'}]})
            return data
        def matches(self,*args,**kwargs):
            old=match(99,tournament=679);old['season']={'id':25,'year':'25/26'};old['start_time']='bad-date'
            return {'complete':True,'matches':[match(),old]+[match(10+i,tournament=679) for i in range(5)]}
    _,plan,report=prepare(api=Europe())
    assert not any(r['code']=='european_scope_unproven' for r in report['review'])
    assert any(c.operation.get('competition',{}).get('category')=='Еврокубки' and c.status=='ready' for c in plan.changes)


def test_unchanged_article_produces_valid_explicit_collection_result():
    from wiki_stats.sofascore_cycle import prepare_player
    from wiki_stats.schema_validator import validate_package
    _, first, _ = prepare()
    mapping={'api_id':1,'qid':'Q114406918','title':'Игрок','wiki_name':'Кальвин Браккельман','birth_date':'1999-08-22'}
    package, plan, _ = prepare_player(snapshot(first.preview),mapping,API(),Wiki(),Resolver(),now=NOW)
    validate_package(package)
    article=package['articles'][0]
    assert article['operations']==[] and article['collection_result']=='no_confirmed_changes'
    assert not plan.diff and plan.preview==first.preview
    for key in ('collection_result','base_revid'):
        malformed=deepcopy(package);del malformed['articles'][0][key]
        with pytest.raises(UpdateError):validate_package(malformed)
    changed=deepcopy(package);changed['articles'][0]['operations']=prepare()[0]['articles'][0]['operations']
    with pytest.raises(UpdateError):validate_package(changed)
