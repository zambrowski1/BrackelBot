# SPDX-License-Identifier: MIT
"""Real source responses where announced stoppage is absent or exceeded."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from wiki_stats.sofascore_timeline import timeline_numbers

SAMPLES = json.loads((Path(__file__).parent / 'api_fixtures/timeline_terminal_clock_probes.json').read_text(encoding='utf-8'))


def evaluate(sample):
    pid = sample['pid']
    roster = sample['lineups']
    side = next(s for s in ('home', 'away') if any(p['player']['id'] == pid for p in roster[s]['players']))
    return timeline_numbers(sample['match'], pid, sample['match'][side+'_team']['id'], roster, sample['incidents'])


@pytest.mark.parametrize('index', [0, 1])
def test_actual_entry_and_complete_scoring_prove_one_appearance(index):
    assert evaluate(SAMPLES[index]) == {'appearances': 1, 'goals': 0}


@pytest.mark.parametrize('index', [0, 1])
@pytest.mark.parametrize('problem', ['missing_entry', 'incomplete_goals', 'unconfirmed_roster', 'zero_minutes', 'contradictory_exit'])
def test_terminal_clock_relaxation_preserves_independent_evidence_guards(index, problem):
    x = deepcopy(SAMPLES[index]);pid = x['pid'];events = x['incidents']['incidents']
    if problem == 'missing_entry':
        events[:] = [e for e in events if e.get('player_in', {}).get('id') != pid]
    elif problem == 'incomplete_goals':
        events.remove(next(e for e in events if e.get('type') == 'goal'))
    elif problem == 'unconfirmed_roster':
        x['lineups']['is_confirmed'] = False
    elif problem == 'zero_minutes':
        for side in ('home', 'away'):
            for p in x['lineups'][side]['players']:
                if p['player']['id'] == pid:p['statistics']['minutes_played'] = 0
    else:
        incoming = next(e for e in events if e.get('player_in', {}).get('id') == pid)
        events.append({'type': 'substitution', 'time': incoming['time']+1,
                       'player_out': {'id': pid}, 'player_in': {'id': 999999}})
    x['incidents']['count'] = len(events)
    assert evaluate(x) is None
