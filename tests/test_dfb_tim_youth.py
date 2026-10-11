# SPDX-License-Identifier: MIT
from datetime import datetime, timezone
import gzip
import json
from pathlib import Path

import pytest

from wiki_stats.dfb_youth_profile import parse_closed_youth
from wiki_stats.errors import UpdateError

MAPPING = {'api_id': 228981, 'source_name': 'Tim Kleindienst',
           'wiki_name': 'Тим Кляйндинст', 'birth_date': '1995-08-31'}
NOW = datetime(2026, 10, 11, tzinfo=timezone.utc)


def source():
    with gzip.open(Path(__file__).parent / 'source-fixtures/dfb-tim-youth.json.gz',
                   'rt', encoding='utf-8') as handle:
        return json.load(handle)


def test_real_tim_u20_complete_career():
    scopes = parse_closed_youth(MAPPING, source(), NOW)
    assert len(scopes) == 1
    assert scopes[0]['age'] == 20
    assert scopes[0]['numbers'] == {'appearances': 10, 'goals': 0}
    assert len(scopes[0]['matches']) == 10
    assert scopes[0]['first_date'] == '2014-09-03'
    assert scopes[0]['statistics_as_of'] == '2016-03-26'


def test_complete_counts_do_not_make_a_match_before_birth_valid():
    data = source()
    for fragment in data['fragments']:
        fragment['html'] = fragment['html'].replace('03.09.2014', '03.09.1990')
    with pytest.raises(UpdateError) as exc:
        parse_closed_youth(MAPPING, data, NOW)
    assert exc.value.code == 'dfb_youth_unproven'
