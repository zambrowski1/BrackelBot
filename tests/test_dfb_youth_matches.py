# SPDX-License-Identifier: MIT
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import gzip

import pytest

from tests.stage23_helpers import snapshot
from tests.test_dfb_youth_cycle import TEXT, Wiki
from tests.test_dfb_youth_profile import MAPPING, NOW, source
from wiki_stats.dfb_youth_profile import parse_closed_youth
from wiki_stats.dfb_youth_matches import full_youth_repair, shootout_score
from wiki_stats.errors import UpdateError
from wiki_stats.structural_editor import execute_operation
from wiki_stats.wikitext_parser import WikitextParser
from wiki_stats.sbmatches_editor import read_matches

TEAM = '[[Сборная Германии по футболу (до 21 года)|Германию до 21 года]]'
ENTITY = {'name': 'Сборная Германии по футболу (до 21 года)', 'kind': 'national_team',
          'age': 21, 'wikidata_id': 'Q21', 'page_title': 'Сборная Германии по футболу (до 21 года)',
          'wikitext': TEAM}
TABLE = '{{СбМатчи\n|игрок=Давида Раума\n|сборная=' + TEAM + '\n|возраст=21\n|соревнование=Товарищеский матч\n|матч1=01-01-2021 ;; Франция ;; 2:1 ;; 0\n}}'


def bundle():
    data = source()
    scope = next(s for s in parse_closed_youth(MAPPING, data, NOW) if s['age'] == 21)
    row = next(r for r in scope['matches'] if r['shootout'])
    with gzip.open(Path(__file__).parent / 'source-fixtures/dfb-raum-u21-shootout.html.gz', 'rt', encoding='utf-8') as f:
        data['protocols'] = [{'url': row['url'], 'html': f.read()}]
    return data, scope, row


def snap(text=TEXT + '\n' + TABLE):
    return replace(snapshot(text), title='Участник:Zambrowski/testbot4', revid=155529482)


def test_real_protocol_verifies_play_score_and_pure_shootout_without_subtraction():
    data, scope, row = bundle()
    assert shootout_score(row, MAPPING, data['protocols'][0]) == ([2, 2], [5, 6])


def test_complete_eight_matches_replace_fictional_one_and_preserve_other_table():
    data, scope, _ = bundle(); adult = TABLE.replace('|возраст=21', '|возраст=').replace(TEAM, '[[Сборная Германии по футболу|Германию]]')
    before = TEXT + '\n' + TABLE + '\n' + adult
    op = full_youth_repair(snap(before), MAPPING, scope, ENTITY, data, Wiki())
    assert len(op['payload']['matches']) == len(op['sources']) == 8
    assert op['payload']['totals'] == {'appearances': 8, 'goals': 0}
    assert op['payload']['matches'][5]['счёт'] == '2:2'
    assert op['payload']['matches'][5]['пенальти'] == '6:5'
    after, status = execute_operation(before, MAPPING['wiki_name'], op)
    assert status == 'ready' and adult in after
    assert after.count('01-01-2021 ;; Франция') == 1  # The adult fixture is preserved.
    table = next(t for t in WikitextParser(after).templates if t.name == 'сбматчи')
    assert len(read_matches(table)[1]) == 8
    assert execute_operation(after, MAPPING['wiki_name'], op) == (after, 'already_applied')


@pytest.mark.parametrize('fault', ['missing', 'wrong_url', 'wrong_date', 'wrong_team', 'wrong_player',
    'ordinary_goals', 'shootout_hits', 'score_header', 'missing_shootout', 'missing_lineup'])
def test_shootout_protocol_corruption_refuses_table_update(fault):
    data, scope, row = bundle(); protocol = data['protocols'][0]
    if fault == 'missing': data['protocols'] = []
    elif fault == 'wrong_url': protocol['url'] += '-wrong'
    elif fault == 'wrong_date': row['date'] = '2021-06-01'
    elif fault == 'wrong_team': row['home'] = 'Frankreich U 21'
    elif fault == 'wrong_player': protocol['html'] = protocol['html'].replace('David Raum', 'Other Player')
    elif fault == 'ordinary_goals': protocol['html'] = protocol['html'].replace('>Tore<', '>Missing<')
    elif fault == 'shootout_hits': protocol['html'] = protocol['html'].replace('trifft', 'vergibt')
    elif fault == 'score_header': row['display_score'] = [3, 4]
    elif fault == 'missing_shootout': protocol['html'] = protocol['html'].replace('Elfmeterschießen', 'Missing')
    else: protocol['html'] = protocol['html'].replace('Aufstellung', 'Missing')
    with pytest.raises(UpdateError) as error:
        full_youth_repair(snap(), MAPPING, scope, ENTITY, data, Wiki())
    assert error.value.code == ('national_identity_mismatch' if fault == 'wrong_team' else 'dfb_youth_score_unproven')


@pytest.mark.parametrize('fault', ['incomplete', 'nonzero_goals', 'wrong_metric', 'unknown_opponent',
                                  'mixed_age', 'competition', 'duplicate_table', 'duplicate_protocol'])
def test_incomplete_age_or_goal_coverage_never_produces_a_full_table(fault):
    data, scope, _ = bundle(); text = TEXT + '\n' + TABLE
    if fault == 'incomplete': scope['complete'] = False
    elif fault == 'nonzero_goals': scope['numbers']['goals'] = 1
    elif fault == 'wrong_metric': text = text.replace('|возраст=21', '|возраст=21\n|статистика=пропущенные голы')
    elif fault == 'unknown_opponent': scope['matches'][0]['away'] = 'Unknown U 21'
    elif fault == 'mixed_age': scope['matches'][0]['away'] = 'Slowenien U 20'
    elif fault == 'competition': scope['matches'][0]['competition'] = 'Other'
    elif fault == 'duplicate_table': text += '\n' + TABLE
    else: data['protocols'].append(deepcopy(data['protocols'][0]))
    with pytest.raises(UpdateError): full_youth_repair(snap(text), MAPPING, scope, ENTITY, data, Wiki())


def test_mainspace_cannot_receive_sandbox_full_repair():
    data, scope, _ = bundle()
    assert full_youth_repair(replace(snap(), title='Раум, Давид'), MAPPING, scope, ENTITY, data, Wiki()) is None


def test_youth_cycle_combines_card_and_full_table_in_valid_idempotent_package():
    from tests.test_dfb_youth_cycle import API
    from wiki_stats.dfb_youth_cycle import prepare_youth_nationals
    from wiki_stats.schema_validator import validate_package
    api = API(); api.bundle = bundle()[0]
    text = (TEXT.replace('|11 (1)', '|11 (1)\n|2020—2021|' + TEAM + '|8 (0)') + '\n' + TABLE)
    operations, issues, _ = prepare_youth_nationals(snap(text), MAPPING, api, Wiki(), NOW)
    assert len(operations) == 3
    assert not any(i.get('age') in (19, 21) for i in issues)
    validate_package({'schema_version': '1.1', 'package_id': 'dfb-full-youth',
                      'generated_at': NOW.isoformat(), 'articles': [{'title': snap().title,
                      'base_revid': 155529482, 'player': MAPPING['wiki_name'], 'operations': operations}]})
    for op in operations:
        text, status = execute_operation(text, MAPPING['wiki_name'], op)
        assert status == 'ready'
    repeat, _, _ = prepare_youth_nationals(snap(text), MAPPING, api, Wiki(), NOW)
    assert len(repeat) == 1 and repeat[0]['type'] == 'repair_sbmatches'
    assert execute_operation(text, MAPPING['wiki_name'], repeat[0]) == (text, 'already_applied')
