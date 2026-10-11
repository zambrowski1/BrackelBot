# SPDX-License-Identifier: MIT
from copy import deepcopy
from datetime import datetime, timezone

import pytest

from tests.test_sofascore import match
from wiki_stats.sofascore_cycle import absent_category_zero


def fixture(minutes=0):
    league = {'season': '2025/26', 'team_id': 2600, 'tournament_id': 35,
              'season_id': 25, 'numbers': {'appearances': 1, 'goals': 0}}
    old = match(1, day='2024-01-01')
    old['season'] = {'id': 24, 'year': '23/24'}
    played = match(2, day='2025-09-01')
    played['season'] = {'id': 25, 'year': '25/26'}
    cup = match(3, day='2025-10-01', tournament=217, minutes=minutes)
    cup['season'] = {'id': 52, 'year': '25/26'}
    latest = match(4, day='2026-09-01')
    return {'complete': True, 'matches': [old, played, cup, latest]}, league


def evaluate(history, league):
    return absent_category_zero(history, league, 'Национальный кубок',
                                datetime(2026, 10, 11, tzinfo=timezone.utc))


@pytest.mark.parametrize('minutes', [-1, float('-inf'), float('nan'), None, False, '0', 1])
def test_unplayed_cup_requires_explicit_numeric_zero(minutes):
    history, league = fixture(minutes)
    assert evaluate(history, league) is None


@pytest.mark.parametrize('minutes', [0, 0.0])
def test_confirmed_unplayed_cup_accepts_numeric_zero(minutes):
    history, league = fixture(minutes)
    assert evaluate(history, league) == {'appearances': 0, 'goals': 0}


@pytest.mark.parametrize('problem', ['duplicate', 'boolean', 'missing', 'negative'])
def test_zero_proof_rejects_ambiguous_history_ids(problem):
    history, league = fixture()
    if problem == 'duplicate':
        history['matches'].append(deepcopy(history['matches'][2]))
    elif problem == 'missing':
        history['matches'][2].pop('id')
    else:
        history['matches'][2]['id'] = True if problem == 'boolean' else -3
    assert evaluate(history, league) is None
