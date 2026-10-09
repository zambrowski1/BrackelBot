# SPDX-License-Identifier: MIT
from copy import deepcopy
import pytest
from wiki_stats.errors import UpdateError
from wiki_stats.source_validation import roster_members, transfer_history


def record(rows):
    return {'data': {'response': rows}}


def transfer():
    return {'date': '2026-10-08', 'teams': {'out': {'id': 10}, 'in': {'id': 20}}}


def test_explicit_empty_transfer_history_is_valid():
    assert transfer_history(record([]), 1) == []
    assert transfer_history(record([{'player': {'id': 1}, 'transfers': []}]), 1) == []


@pytest.mark.parametrize('rows', [None, {}, [None], [{'player': {'id': 1}}],
                                 [{'player': {'id': 2}, 'transfers': []}],
                                 [{'player': {'id': True}, 'transfers': []}],
                                 [{'player': {'id': 1}, 'transfers': None}],
                                 [{'player': {'id': 1}, 'transfers': []}] * 2])
def test_incomplete_transfer_history_is_not_assumed_empty(rows):
    with pytest.raises(UpdateError):
        transfer_history(record(rows), 1)


@pytest.mark.parametrize('day', [None, '', '2026-99-01', '20261008', '2026-10-08T12:00:00Z'])
def test_transfer_requires_calendar_date(day):
    value = transfer(); value['date'] = day
    with pytest.raises(UpdateError):
        transfer_history(record([{'player': {'id': 1}, 'transfers': [value]}]), 1)


def test_transfer_retains_history_without_summing_or_guessing():
    values = [transfer(), {**transfer(), 'date': '2026-10-09'}]
    assert transfer_history(record([{'player': {'id': 1}, 'transfers': values}]), 1) == values


@pytest.mark.parametrize('tid', [None, True, 0, '10'])
def test_transfer_unknown_club_requires_review(tid):
    value = deepcopy(transfer()); value['teams']['out']['id'] = tid
    with pytest.raises(UpdateError):
        transfer_history(record([{'player': {'id': 1}, 'transfers': [value]}]), 1)


@pytest.mark.parametrize('rows', [[], None, [{'team': {'id': 10}}],
                                 [{'team': {'id': 20}, 'players': [{'id': 1}]}],
                                 [{'team': {'id': 10}, 'players': [{'id': 1}, {'id': 1}]}],
                                 [{'team': {'id': 10}, 'players': [{'id': True}]}],
                                 [{'team': {'id': 10}, 'players': []}]])
def test_roster_must_be_complete_and_unambiguous(rows):
    with pytest.raises(UpdateError):
        roster_members(record(rows), 10)


def test_valid_roster_returns_exact_membership():
    assert roster_members(record([{'team': {'id': 10}, 'players': [{'id': 1}, {'id': 2}]}]), 10) == {1, 2}
