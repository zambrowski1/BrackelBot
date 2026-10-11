# SPDX-License-Identifier: MIT
from copy import deepcopy
from datetime import datetime, timezone
import gzip, json
from pathlib import Path
import pytest
from tests.test_dfb_tim_youth import MAPPING
from wiki_stats.dfb_club_profile import parse_closed_clubs
from wiki_stats.errors import UpdateError

NOW = datetime(2026, 10, 11, tzinfo=timezone.utc)


def source():
    with gzip.open(Path(__file__).parent/'source-fixtures/dfb-tim-clubs.json.gz', 'rt', encoding='utf-8') as f:
        return json.load(f)


def test_real_old_seasons_and_reserve_teams_are_separate_complete_scopes():
    scopes = parse_closed_clubs(MAPPING, source(), NOW)
    assert len(scopes) == 29
    numbers = {(s['team_slug'], s['season'], s['category']): s['numbers'] for s in scopes}
    for key, expected in [
        (('energie-cottbus-ii','2012/13','Чемпионат'), (4,0)),
        (('energie-cottbus','2013/14','Чемпионат'), (2,0)),
        (('sc-freiburg','2015/16','Чемпионат'), (7,0)),
        (('sc-freiburg-ii','2015/16','Чемпионат'), (8,2)),
        (('sc-freiburg-ii','2018/19','Чемпионат'), (2,1)),
        (('sc-freiburg-ii','2019/20','Чемпионат'), (1,0)),
        (('1-fc-heidenheim','2019/20','Стыковые матчи'), (2,2)),
    ]:
        assert numbers[key] == dict(zip(('appearances','goals'), expected))
    assert all(s['complete'] and len(s['matches']) == s['numbers']['appearances'] for s in scopes)


@pytest.mark.parametrize('fault', ['missing', 'duplicate', 'wrong_id', 'wrong_source', 'foreign_team',
                                  'missing_match', 'duplicate_match', 'wrong_season_date',
                                  'wrong_relegation_date', 'oversized', 'wrong_player', 'wrong_birthday'])
def test_unproven_club_scopes_cannot_be_used_to_certify_a_season(fault):
    data = source(); mapping = deepcopy(MAPPING)
    frame = next(f for f in data['fragments'] if '/regionalliga-nordost/' in f['url'])
    if fault == 'missing': data['fragments'].remove(frame)
    elif fault == 'duplicate': data['fragments'].append(deepcopy(frame))
    elif fault == 'wrong_id': frame['frame_id'] += '-other'
    elif fault == 'wrong_source': frame['url'] = frame['url'].replace('tim-kleindienst', 'other-player')
    elif fault == 'foreign_team': frame['html'] = frame['html'].replace('Energie Cottbus II', 'SC Freiburg II')
    elif fault == 'oversized': frame['html'] = 'x' * 2_000_001
    elif fault == 'wrong_player': mapping['api_id'] = 856076
    elif fault == 'wrong_birthday': mapping['birth_date'] = '1995-08-30'
    elif fault == 'wrong_relegation_date':
        f = next(f for f in data['fragments'] if '/relegation-bundesliga/' in f['url'])
        f['html'] = f['html'].replace('06.07.2020', '07.07.2020')
    else:
        from wiki_stats.dfb_evidence import _Document
        d = _Document(); d.feed(frame['html'])
        rows = [n for n in d.root.nodes() if n.has('c-MatchTable-row')][1:]
        if fault == 'wrong_season_date':
            import re
            frame['html'] = re.sub(r'\b([0-9]{2}\.[0-9]{2})\.2013\b', r'\1.2014', frame['html'])
        else:
            # Replace one complete DOM row, retaining the surrounding source frame.
            from wiki_stats.dfb_evidence import _Node
            # Rows have independently identifiable match protocol URLs.
            links = [n.attrs['href'] for n in rows[0].nodes() if n.tag == 'a' and '/datencenter/' in n.attrs.get('href','')]
            assert links
            if fault == 'missing_match':
                frame['html'] = frame['html'].replace('c-MatchTable-row', 'ignored-row', 2)
            else:
                # Two different rows now identify the same protocol.
                second = [n.attrs['href'] for n in rows[1].nodes() if n.tag == 'a' and '/datencenter/' in n.attrs.get('href','')]
                assert second
                frame['html'] = frame['html'].replace(second[0], links[0])
    with pytest.raises(UpdateError):
        parse_closed_clubs(mapping, data, NOW)
