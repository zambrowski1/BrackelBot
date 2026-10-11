# SPDX-License-Identifier: MIT
from copy import deepcopy
from datetime import datetime,timezone
import gzip,json
from pathlib import Path
import pytest
from wiki_stats.sofascore_current_other import prove_current_other,recover_current_other
from wiki_stats.errors import UpdateError

SOURCE=json.loads(gzip.decompress((Path(__file__).parent/'source-fixtures/current-other-orban.json.gz').read_bytes()))
NOW=datetime(2026,10,11,tzinfo=timezone.utc)


def evaluate(data=None,now=NOW):
    d=data or SOURCE
    return prove_current_other(d['history'],d['anchor'],d['league_calendar'],d['supercup_calendar'],now)


def test_actual_orban_other_zero_is_proved_without_counting_telekom_friendly():
    proof=evaluate()
    assert proof is not None and proof['numbers']=={'appearances':0,'goals':0}
    assert len(proof['league_match_ids'])==5 and 16242805 in proof['excluded_friendly_ids']
    assert proof['expires_on']=='2027-05-27' and len(proof['primary_sources'])==2


@pytest.mark.parametrize('problem',['partial_history','partial_league','partial_cup','cup_participant',
 'wrong_year','wrong_season','wrong_date','wrong_aggregate','missing_game','duplicate',
 'unknown_competition','playoff','missing_minutes','boolean_minutes','nan_minutes','wrong_friendly',
 'unfinished_own','different_match_time'])
def test_current_other_never_infers_zero_from_ambiguous_or_incomplete_sources(problem):
    d=deepcopy(SOURCE)
    row=next(r for r in d['history']['matches'] if r.get('tournament',{}).get('id')==35 and r['start_time'].startswith('2026-10-10'))
    if problem=='partial_history':d['history']['complete']=False
    elif problem=='partial_league':d['league_calendar']['complete']=False
    elif problem=='partial_cup':d['supercup_calendar']['complete']=False
    elif problem=='cup_participant':d['supercup_calendar']['matches'][0]['home_team']['id']=36360
    elif problem=='wrong_year':d['anchor']['season']='2025/26'
    elif problem=='wrong_season':d['league_calendar']['season_id']=1
    elif problem=='wrong_date':d['supercup_calendar']['matches'][0]['start_time']='2026-08-21T12:00:00Z'
    elif problem=='wrong_aggregate':d['anchor']['numbers']['appearances']=4
    elif problem=='missing_game':d['history']['matches'].remove(row)
    elif problem=='duplicate':d['history']['matches'].append(deepcopy(row))
    elif problem=='unknown_competition':row['tournament']['id']=99999
    elif problem=='playoff':row['round']={'name':'Relegation playoffs'}
    elif problem=='missing_minutes':row['player']['statistics'].pop('minutes_played')
    elif problem=='boolean_minutes':row['player']['statistics']['minutes_played']=True
    elif problem=='nan_minutes':row['player']['statistics']['minutes_played']=float('nan')
    elif problem=='unfinished_own':row['is_finished']=False;row['status']['type']='inprogress'
    elif problem=='different_match_time':row['start_time']='2026-10-10T16:30:01Z'
    else:next(r for r in d['history']['matches'] if r.get('tournament',{}).get('id')==889)['tournament']['name']='Official Cup'
    assert evaluate(d) is None


def test_published_playoff_schedule_cannot_prove_zero_after_its_expiry():
    assert evaluate(now=datetime(2027,5,27,tzinfo=timezone.utc)) is None


def test_recovery_accounts_for_calendar_requests_and_preserves_quota_failure():
    class API:
        def tournament_seasons(self,tid):
            assert tid==799
            return {'tournament_id':799,'count':1,'seasons':[{'id':95403,'year':'26/27'}]}
        def tournament_matches(self,tid,sid,request_budget):
            if request_budget['remaining']<=0:raise UpdateError('match_request_cap','Limit')
            request_budget['remaining']-=1
            return SOURCE['league_calendar' if tid==35 else 'supercup_calendar']
    budget={'remaining':3}
    assert recover_current_other(SOURCE['history'],SOURCE['anchor'],API(),budget,NOW)['numbers']=={'appearances':0,'goals':0}
    assert budget['remaining']==0
    class Limited(API):
        def tournament_seasons(self,tid):raise UpdateError('quota_exhausted','Free limit')
    budget={'remaining':10}
    assert recover_current_other(SOURCE['history'],SOURCE['anchor'],Limited(),budget,NOW) is None
    assert budget['remaining']==0 and budget['failures'][0]['code']=='quota_exhausted'


def test_main_cycle_corrects_current_other_and_records_primary_source_provenance():
    from tests.test_sofascore import API,Wiki,Resolver
    from tests.stage23_helpers import MINI,snapshot
    from wiki_stats.sofascore_cycle import prepare_player
    history=deepcopy(SOURCE['history']);league=deepcopy(SOURCE['league_calendar'])
    for row in history['matches']+league['matches']:
        for side in ('home_team','away_team'):
            if row[side]['id']==36360:row[side]['id']=999900
        if (row.get('player') or {}).get('team_id')==36360:row['player']['team_id']=999900
    class Combined(API):
        def details(self,pid):
            data=super().details(pid);data['team']['id']=999900;return data
        def seasons(self,pid):
            data=super().seasons(pid);data['tournament_seasons'][0]['seasons'][0]['id']=97464
            return data
        def statistics(self,*args):
            data=super().statistics(*args);data['statistics']['goals']=1;return data
        def matches(self,*args,**kwargs):return history
        def tournament_seasons(self,tid):
            if tid!=799:raise UpdateError('api_not_found','Unrelated test competition unavailable')
            return {'tournament_id':799,'count':1,'seasons':[{'id':95403,'year':'26/27'}]}
        def tournament_matches(self,tid,sid,request_budget):
            request_budget['remaining']-=1
            return league if tid==35 else SOURCE['supercup_calendar']
    text=MINI.replace('Стыковые матчи','Прочие').replace('2025/26','2026/27')
    text=text.replace('| имя =','| дата рождения = 22.08.1999\n| имя =')
    text=text.replace('{{КлСтат/Сезон|2026/27|4|1|0|0|0|0|0|0}}','{{КлСтат/Сезон|2026/27|4|1|0|0|0|0|9|9}}')
    mapping={'api_id':1,'qid':'Q114406918','title':'Игрок','wiki_name':'Кальвин Браккельман','birth_date':'1999-08-22'}
    package,plan,report=prepare_player(snapshot(text),mapping,Combined(),Wiki(),Resolver(),now=NOW)
    proofs=[i for i in report['review'] if i['code']=='current_other_from_competition_calendars']
    assert proofs,report['review']
    proof=proofs[0]
    assert proof['numbers']=={'appearances':0,'goals':0}
    operation=next(c for c in plan.changes if c.operation.get('competition',{}).get('category')=='Прочие')
    assert operation.status=='ready' and operation.operation['new']=={'appearances':0,'goals':0}
    assert {'dfl','club_official'}<={s['provenance'] for s in operation.operation['sources']}
    assert not any(i['code']=='zero_from_complete_match_history' and i.get('category')=='Прочие' for i in report['review'])
    _,replay,_=prepare_player(snapshot(plan.preview),mapping,Combined(),Wiki(),Resolver(),now=NOW)
    assert not replay.diff
