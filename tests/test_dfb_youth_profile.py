# SPDX-License-Identifier: MIT
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import json
from pathlib import Path

import pytest

from wiki_stats.dfb_youth_profile import parse_closed_youth
from wiki_stats.errors import UpdateError

NOW = datetime(2026, 10, 11, tzinfo=timezone.utc)
MAPPING = {'api_id': 856076, 'wiki_name': 'Давид Раум', 'source_name': 'David Raum',
           'birth_date': '1998-04-22', 'title': 'Раум, Давид', 'qid': 'Q28540571'}


def source():
    with gzip.open(Path(__file__).parent / 'source-fixtures' / 'dfb-raum-youth.json.gz',
                   'rt', encoding='utf-8') as stream:
        return json.load(stream)


def test_real_federation_totals_and_complete_individual_match_lists():
    scopes = parse_closed_youth(MAPPING, source(), NOW)
    assert [(s['age'], s['numbers']['appearances'], s['numbers']['goals']) for s in scopes] == [
        (19, 5, 1), (20, 6, 0), (21, 8, 0), (23, 3, 0)]
    assert [(s['first_date'], s['statistics_as_of']) for s in scopes] == [
        ('2016-11-13', '2017-07-09'), ('2017-09-04', '2018-03-27'),
        ('2020-11-12', '2021-06-06'), ('2021-07-22', '2021-07-28')]
    for scope in scopes:
        assert len(scope['matches']) == scope['numbers']['appearances']
        assert len({r['url'] for r in scope['matches']}) == len(scope['matches'])
        assert all(url.startswith('https://datencenter.dfb.de/') for url in scope['sources'])


def test_shootout_is_explicit_and_never_exposed_as_verified_play_score():
    scope = next(s for s in parse_closed_youth(MAPPING, source(), NOW) if s['age'] == 21)
    match = next(r for r in scope['matches'] if r['date'] == '2021-05-31')
    assert match['shootout'] is True and match['display_score'] == [5, 6]
    assert 'score' not in match  # Needs the full protocol before SbMatches formatting.


@pytest.mark.parametrize('fault', ['player', 'bool_player', 'name', 'dob', 'nation',
    'profile_url', 'origin', 'missing_frame', 'duplicate_frame', 'foreign_frame',
    'wrong_frame_id', 'wrong_team', 'future_match', 'missing_score', 'oversized',
    'malformed', 'eligible_player', 'bad_mapping'])
def test_unproven_source_is_rejected_instead_of_producing_partial_updates(fault):
    data = source(); mapping = deepcopy(MAPPING); now = NOW
    if fault == 'player': data['player_id'] = 136924
    elif fault == 'bool_player': data['player_id'] = True
    elif fault == 'name': mapping['source_name'] = 'Willi Orban'
    elif fault == 'dob': mapping['birth_date'] = '1998-04-23'
    elif fault == 'nation': data['profile']['html'] = data['profile']['html'].replace('Deutschland', 'Frankreich')
    elif fault == 'profile_url': data['profile']['url'] = data['profile']['url'].replace('datencenter.dfb.de', 'example.org')
    elif fault == 'origin': data['profile']['url'] += '/wrong'
    elif fault == 'missing_frame': data['fragments'].pop()
    elif fault == 'duplicate_frame': data['fragments'].append(deepcopy(data['fragments'][0]))
    elif fault == 'foreign_frame': data['fragments'][0]['url'] = data['fragments'][0]['url'].replace('david-raum', 'willi-orban')
    elif fault == 'wrong_frame_id': data['fragments'][0]['frame_id'] += '-wrong'
    elif fault == 'wrong_team':
        data['fragments'][0]['html'] = data['fragments'][0]['html'].replace('Deutschland', 'Frankreich')
    elif fault == 'future_match': now = datetime(2015, 1, 1, tzinfo=timezone.utc)
    elif fault == 'missing_score':
        import re
        data['fragments'][0]['html'] = re.sub(r'([0-9]+)\s*:\s*([0-9]+)', '?:?', data['fragments'][0]['html'])
    elif fault == 'oversized': data['fragments'][0]['html'] = 'x' * 2_000_001
    elif fault == 'malformed': data = None
    elif fault == 'eligible_player': now = datetime(2020, 1, 1, tzinfo=timezone.utc)
    elif fault == 'bad_mapping': mapping['api_id'] = '856076'
    with pytest.raises(UpdateError) as error:
        parse_closed_youth(mapping, data, now)
    assert error.value.code == 'dfb_youth_unproven'
