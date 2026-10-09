# SPDX-License-Identifier: MIT
from dataclasses import replace

import pytest

from wiki_stats.change_planner import plan_article
from stage23_helpers import MINI, FakeResolver, package, snapshot, transfer


@pytest.mark.parametrize('alias', ['{{нв}}', '{{НВ}}', '{{н.в.}}'])
def test_transfer_closes_current_period_alias_atomically(alias):
    page = snapshot(MINI.replace('{{н.в.}}', alias))
    ops = transfer()
    ops[0]['target']['period'] = '2026—' + alias
    plan = plan_article(page, package(ops)['articles'][0], '1.1', FakeResolver())
    assert [c.status for c in plan.changes] == ['ready'] * 3
    assert '|2026—2026|' in plan.preview
    assert plan.preview.count('|2026—{{н.в.}}|') == 1
    assert plan.preview.split('{{КлСтат|', 1)[1] == page.text.split('{{КлСтат|', 1)[1]
    repeated = plan_article(replace(page, text=plan.preview), package(ops)['articles'][0], '1.1', FakeResolver())
    assert not repeated.diff


def test_other_active_alias_blocks_second_primary_club():
    page = snapshot(MINI.replace('2010—2012', '2010—{{нв}}'))
    plan = plan_article(page, package(transfer())['articles'][0], '1.1', FakeResolver())
    assert 'previous_period_not_closed' in [c.status for c in plan.changes]
    assert plan.preview == page.text
    assert not plan.diff
