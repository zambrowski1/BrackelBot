# SPDX-License-Identifier: MIT
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from wiki_stats.sofascore_client import _SofascoreAccount
from wiki_stats.sofascore_match_stats import reconstruct
from wiki_stats.sofascore_score import match_score
from wiki_stats.sofascore_timeline import timeline_numbers

SOURCE = json.loads((Path(__file__).parent / 'source-fixtures/shootout-source.json').read_text(encoding='utf-8'))
LOOKUP = {r['cache_key']: r['data'] for r in SOURCE['responses']}


class Replay(_SofascoreAccount):
    def __init__(self):
        pass

    def request(self, path, params, **kwargs):
        key = hashlib.sha256(json.dumps([path, params], sort_keys=True).encode()).hexdigest()
        return LOOKUP[key]


@pytest.mark.parametrize('mid,pid,tid,keeper,expected', [
    (8594187, 556866, 4699, True, {'appearances': 0, 'goals': 0}),
    (8594187, 16206, 4699, True, {'appearances': 1, 'goals': -3}),
    (12813014, 856076, 4711, False, {'appearances': 0, 'goals': 0}),
    (12813014, 8959, 4711, True, {'appearances': 1, 'goals': -1}),
])
def test_actual_shootouts_count_playing_goals_and_prove_unused_substitutes(mid, pid, tid, keeper, expected):
    api = Replay()
    row = SOURCE['matches'][str(mid)]
    assert timeline_numbers(row, pid, tid, api.match_lineups(mid), api.match_incidents(mid),
                            goalkeeper=keeper) == expected


def test_shootout_inclusive_personal_conceded_metric_falls_back_to_verified_timeline():
    class Inflated(Replay):
        def match_statistics(self, pid, mid):
            return {'match_id': mid, 'player': {'id': pid}, 'team': {'id': 4699},
                    'statistics': {'minutes_played': 120, 'goals_conceded': 7}}
    budget = {'remaining': 3}
    assert reconstruct(Inflated(), 16206, {'team_id': 4699},
                       [SOURCE['matches']['8594187']], budget, goalkeeper=True) == {
                           'appearances': 1, 'goals': -3}
    assert budget['remaining'] == 0


@pytest.mark.parametrize('problem', ['boolean', 'negative', 'missing_penalties', 'tied_penalties',
                                    'mixed_current', 'wrong_display', 'wrong_periods', 'missing_display'])
def test_inconsistent_shootout_scores_are_refused(problem):
    row = deepcopy(SOURCE['matches']['8594187'])
    home, away = row['home_score'], row['away_score']
    if problem == 'boolean': home['current'] = True
    elif problem == 'negative': home['periods']['penalties'] = -1
    elif problem == 'missing_penalties': del away['periods']['penalties']
    elif problem == 'tied_penalties': home['periods']['penalties'] = away['periods']['penalties']
    elif problem == 'mixed_current': home['current'] = home['display']
    elif problem == 'wrong_display': home['display'] = 2
    elif problem == 'wrong_periods': home['periods']['normaltime'] = 99
    else:
        for score in (home, away):
            score.pop('display', None)
            score['periods'].pop('overtime', None)
    assert match_score(row) is None


def test_explicit_play_score_and_complete_periods_are_both_supported():
    row = deepcopy(SOURCE['matches']['8594187'])
    for side in ('home_score', 'away_score'):
        row[side]['current'] = 3
    assert match_score(row) == (3, 3)
    for side in ('home_score', 'away_score'):
        row[side].pop('display')
        row[side]['periods'].update(normaltime=3, overtime=0)
    assert match_score(row) == (3, 3)


@pytest.mark.parametrize('playing_goals', [0, 1, 3, 5])
@pytest.mark.parametrize('kicks', [(4, 5), (8, 9), (2, 0)])
def test_extra_shootout_rounds_never_change_the_playing_score(playing_goals, kicks):
    row = deepcopy(SOURCE['matches']['8594187'])
    for side, penalties in zip(('home_score', 'away_score'), kicks):
        row[side] = {'current': playing_goals+penalties, 'display': playing_goals,
                     'periods': {'normaltime': playing_goals, 'overtime': 0, 'penalties': penalties}}
    assert match_score(row) == (playing_goals, playing_goals)
    for side in ('home_score', 'away_score'):
        row[side]['current'] = playing_goals
    assert match_score(row) == (playing_goals, playing_goals)


TWO_LEG=json.loads((Path(__file__).parent/'source-fixtures/croatia-france-two-leg.json').read_text(encoding='utf8'))


def test_actual_two_leg_shootout_keeps_the_second_leg_loss_separate_from_kicks():
    from wiki_stats.national_match_format import format_scope
    from wiki_stats.sbmatches_editor import validate_row
    assert match_score(TWO_LEG)==(2,0)
    scope={'complete':True,'team':{'id':4715},'numbers':{'appearances':1,'goals':0},'statistics_as_of':'2025-03-23',
           'matches':[{'match':TWO_LEG,'date':'2025-03-23','numbers':{'appearances':1,'goals':0},'own_score':0,'opponent_score':2}]}
    rows,_=format_scope(scope,lambda team:{'wikitext':'Франция'})
    assert rows[0]['счёт']=='0:2' and 'пенальти' not in rows[0]
    assert rows[0]['примечание']=='По сумме двух матчей 2:2; серия пенальти 4:5'
    validate_row(rows[0])


@pytest.mark.parametrize('fault',['previous_leg','round_size','aggregate','missing_aggregate','negative_previous_score','current'])
def test_two_leg_exception_requires_complete_consistent_aggregate_proof(fault):
    row=deepcopy(TWO_LEG)
    if fault=='previous_leg':row.pop('previous_leg_event_id')
    elif fault=='round_size':row['cup_matches_in_round']=True
    elif fault=='aggregate':row['away_score']['periods']['aggregated']=3
    elif fault=='missing_aggregate':row['home_score']['periods'].pop('aggregated')
    elif fault=='negative_previous_score':
        for side in ('home_score','away_score'):row[side]['periods']['aggregated']=1
    else:row['home_score']['current']=2
    assert match_score(row) is None
