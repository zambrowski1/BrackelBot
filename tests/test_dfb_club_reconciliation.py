# SPDX-License-Identifier: MIT
from copy import deepcopy
from pathlib import Path
import gzip,json
import pytest
from wiki_stats.dfb_club_reconciliation import omitted_verified_appearances
from wiki_stats.sofascore_timeline import timeline_numbers


def source():
    with gzip.open(Path(__file__).parent/'source-fixtures/tim-europe-proof.json.gz','rt',encoding='utf-8') as f:
        return json.load(f)


def arguments():
    proof=source(); rows=[m['row'] for m in proof['matches']]
    for m in proof['matches']:
        assert timeline_numbers(m['row'],228981,2538,m['lineup'],m['events'])=={'appearances':1,'goals':0}
    return ([{'team_id':2538,'match_reconstructed':True,'confirmed_match_ids':[r['id'] for r in rows],
              'numbers':{'appearances':2,'goals':0}}],
            {'complete':True,'matches':rows},
            {'numbers':{'appearances':1,'goals':0},'matches':[{'date':'2017-07-27'}]})


def test_real_partial_federation_list_omits_the_verified_85th_minute_substitution():
    assert omitted_verified_appearances(*arguments())==[7490305]
    proof=source()['matches'][1]
    assert any(e.get('type')=='substitution' and e.get('time')==85
               and (e.get('player_in') or {}).get('id')==228981 for e in proof['events']['incidents'])


@pytest.mark.parametrize('fault',['aggregate_only','incomplete_history','missing_history_row','duplicate_id',
                                 'unused_bench','scoring_uncertain','other_team','not_finished','naive_time',
                                 'wrong_source_date','duplicate_source_date','source_goals','source_total'])
def test_incomplete_secondary_list_is_not_inferred_from_unproven_primary_records(fault):
    records,history,secondary=arguments();record=records[0]
    if fault=='aggregate_only':record['match_reconstructed']=False
    elif fault=='incomplete_history':history['complete']=False
    elif fault=='missing_history_row':history['matches'].pop()
    elif fault=='duplicate_id':record['confirmed_match_ids'][1]=record['confirmed_match_ids'][0]
    elif fault=='unused_bench':record['numbers']['appearances']=1
    elif fault=='scoring_uncertain':record['numbers']['goals']=1
    elif fault=='other_team':record['team_id']=2527
    elif fault=='not_finished':history['matches'][1]['is_finished']=False
    elif fault=='naive_time':history['matches'][1]['start_time']='2017-08-03T19:05:00'
    elif fault=='wrong_source_date':secondary['matches'][0]['date']='2017-08-04'
    elif fault=='duplicate_source_date':secondary['matches']*=2
    elif fault=='source_goals':secondary['numbers']['goals']=1
    else:secondary['numbers']['appearances']=2
    assert omitted_verified_appearances(records,history,secondary) is None
