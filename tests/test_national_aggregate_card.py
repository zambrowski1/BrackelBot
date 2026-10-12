from copy import deepcopy
import pytest
from tests.test_sofascore_national_cycle import TEXT, MAPPING, NOW, Wiki, api
from tests.stage23_helpers import snapshot
from wiki_stats.national_aggregate_card import prepare
from wiki_stats.national_team_identity import NationalTeamIdentity


def source():
    a=api(); item=a.expected['statistics'][0]
    item.update(appearances=8,goals=3)
    entity=NationalTeamIdentity(Wiki()).resolve(item['team'])
    return item,entity,{'complete':False,'matches':a.rows}


def test_card_can_use_aggregate_without_a_complete_match_list():
    item,entity,history=source()
    operations=prepare(snapshot(TEXT),MAPPING,item,entity,history,Wiki(),NOW)
    assert [op['type'] for op in operations]==['update_stats','update_date']
    assert operations[0]['new']=={'appearances':8,'goals':3}
    assert all(op['group_id']==operations[0]['group_id'] for op in operations)
    assert not any('sbmatch' in op['type'] for op in operations)


@pytest.mark.parametrize('problem',['regression','bool','no_participation','duplicate','future','keeper_scored_goals'])
def test_aggregate_needs_numeric_metrics_and_a_verified_last_appearance(problem):
    item,entity,history=source();keeper=False
    if problem=='regression':item['appearances']=5
    elif problem=='bool':item['goals']=True
    elif problem=='no_participation':
        for game in history['matches']:game['player']['statistics']['minutes_played']=0
    elif problem=='duplicate':history['matches'].append(deepcopy(history['matches'][0]))
    elif problem=='future':
        for game in history['matches']:game['start_time']='2027-01-01T00:00:00Z'
    else:keeper=True
    assert prepare(snapshot(TEXT),MAPPING,item,entity,history,Wiki(),NOW,goalkeeper=keeper)==[]
