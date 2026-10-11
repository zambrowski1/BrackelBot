# SPDX-License-Identifier: MIT
from copy import deepcopy

import pytest

from tests.stage23_helpers import MINI, U18, snapshot
from tests.test_dfb_youth_profile import MAPPING, NOW, source
from wiki_stats.dfb_youth_cycle import TITLES, prepare_youth_nationals
from wiki_stats.schema_validator import validate_package
from wiki_stats.structural_editor import execute_operation

TEXT = MINI.replace('Кальвин Браккельман', MAPPING['wiki_name']).replace(
    '|2016—2017|' + U18 + '|6 (0)',
    '|2016—2018|{{Флаг Германии|20px}} [[Сборная Германии по футболу (до 19 лет)|Германия (до 19)]]|11 (1)')


class API:
    def __init__(self): self.bundle = source()
    def national_secondary_source(self, pid):
        assert pid == MAPPING['api_id']
        return self.bundle


class Wiki:
    def get_page_identity(self, title):
        age = next(age for age, titles in TITLES.items() if titles[0] == title)
        return {'title': title, 'qid': 'Q' + str(age)}
    def get_foreign_page_identity(self, title, language):
        age = next(age for age, titles in TITLES.items() if titles[1] == title)
        assert language == ('de' if age == 19 else 'en')
        return {'title': title, 'qid': 'Q' + str(age)}


def test_wrong_raum_u19_total_and_end_year_are_corrected_together_and_replay_cleanly():
    operations, issues, scopes = prepare_youth_nationals(snapshot(TEXT), MAPPING, API(), Wiki(), NOW)
    assert len(scopes) == 4
    assert [op['type'] for op in operations] == ['update_career_period', 'update_stats']
    validate_package({'schema_version': '1.1', 'package_id': 'dfb-youth-real',
                      'generated_at': NOW.isoformat(), 'articles': [{'title': 'Участник:Zambrowski/testbot4',
                      'base_revid': 1, 'player': MAPPING['wiki_name'], 'operations': operations}]})
    result = TEXT
    for op in operations:
        result, status = execute_operation(result, MAPPING['wiki_name'], op)
        assert status == 'ready'
    assert '|2016—2017|' in result and '|5 (1)' in result and '|11 (1)' not in result
    assert prepare_youth_nationals(snapshot(result), MAPPING, API(), Wiki(), NOW)[0] == []
    replay = result
    for op in operations:
        replay, status = execute_operation(replay, MAPPING['wiki_name'], op)
        assert status == 'already_applied'
    assert replay == result


@pytest.mark.parametrize('fault', ['keeper', 'incomplete', 'different_identity', 'duplicate_row', 'start_year'])
def test_unsafe_youth_card_changes_are_not_prepared(fault):
    api = API(); wiki = Wiki(); text = TEXT; keeper = False
    if fault == 'keeper': keeper = True
    elif fault == 'incomplete': api.bundle['fragments'].pop()
    elif fault == 'different_identity':
        wiki.get_foreign_page_identity = lambda *args: {'title': args[0], 'qid': 'Q999'}
    elif fault == 'duplicate_row':
        line = '|2016—2018|{{Флаг Германии|20px}} [[Сборная Германии по футболу (до 19 лет)|Германия (до 19)]]|11 (1)'
        text = text.replace(line, line + '\n' + line)
    else: text = text.replace('2016—2018', '2015—2018')
    operations, issues, _ = prepare_youth_nationals(snapshot(text), MAPPING, api, wiki, NOW, goalkeeper=keeper)
    assert operations == [] and issues
