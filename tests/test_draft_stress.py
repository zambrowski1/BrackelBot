# SPDX-License-Identifier: MIT
"""Deterministic draft scenarios; verify invariants against an independent ledger."""
from copy import deepcopy
import random

import pytest

from wiki_stats.change_validator import references, arithmetic_warnings
from wiki_stats.klstat_editor import check_table
from tests.test_klstat_editor import planned
from stage23_helpers import MINI, season_operation


@pytest.mark.parametrize('seed',range(8))
@pytest.mark.parametrize('keeper',[False,True])
def test_historical_insertions_keep_independent_ledger_and_article_text(seed,keeper):
    rng=random.Random(seed)
    text=MINI
    if keeper:
        text=text.replace('{{Футболист\n','{{Футболист\n| позиция = вратарь\n')
        text=text.replace('|2025/26|4|1|','|2025/26|4|-1|').replace('|Итого|4|1|','|Итого|4|-1|').replace('{{КлСтат/Итого|4|1|','{{КлСтат/Итого|4|-1|')
    if seed%2:text=text.replace('\n','\r\n')
    head=text.split('{{КлСтат|')[0];tail=text.split('Конец статьи')[1]
    original_refs=references(text)
    ledger=deepcopy(check_table(text)['rows'])
    anchor='2025/26'
    for year in range(2024,2019,-1):
        op=season_operation();op['id']='historical-'+str(year);op['season']=f'{year}/{(year+1)%100:02d}'
        op['payload'].pop('insert_after_season');op['payload']['insert_before_season']=anchor
        for numbers in op['payload']['categories'].values():
            appearances=rng.randrange(0,35)
            numbers.update(appearances=appearances,goals=-rng.randrange(0,80) if keeper and appearances else rng.randrange(0,appearances+1))
        result=planned([op],text)
        assert result.changes[0].status=='ready'
        expected={'club':ledger[0]['club'],'season':op['season'],'categories':deepcopy(op['payload']['categories'])}
        ledger.insert(0,expected)
        actual=check_table(result.preview)
        assert actual['rows']==ledger
        assert not actual['warnings'] and not arithmetic_warnings(result.preview)
        assert references(result.preview)==original_refs
        assert result.preview.split('{{КлСтат|')[0]==head
        assert result.preview.split('Конец статьи')[1]==tail
        assert not planned([op],result.preview).diff
        text=result.preview;anchor=op['season']


@pytest.mark.parametrize('category',['Чемпионат','Национальный кубок','Еврокубки','Стыковые матчи'])
def test_one_unconfirmed_category_cannot_create_partial_season(category):
    op=season_operation();del op['payload']['categories'][category]
    result=planned([op])
    assert result.preview==MINI
    assert result.changes[0].status=='incomplete_statistics'
