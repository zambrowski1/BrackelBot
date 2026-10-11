# SPDX-License-Identifier: MIT
from types import SimpleNamespace
import pytest
from tests.stage23_helpers import MINI, AUGSBURG, snapshot
from tests.test_dfb_tim_youth import MAPPING
from tests.test_dfb_club_profile import source, NOW
from wiki_stats.dfb_club_cycle import club_secondary_scopes
from wiki_stats.wikitext_parser import WikitextParser


class API:
    def club_secondary_source(self,pid):return source()


class Wiki:
    def __init__(self,fault=None):self.fault=fault
    def get_page_identity(self,title):
        return {'title':title,'qid':'Q101' if title=='Фрайбург' else 'Q102'}
    def get_foreign_page_identity(self,title,lang):
        qid='Q101' if title=='SC Freiburg' else 'Q103'
        if self.fault=='qid' and title=='SC Freiburg':qid='Q102'
        if self.fault=='redirect' and title=='SC Freiburg':title='SC Freiburg II'
        return {'title':title,'qid':qid}


def run(fault=None):
    text=MINI.replace('[[Аугсбург (футбольный клуб)|Аугсбург]]','[[Фрайбург]]').replace('2025/26','2015/16')
    table=WikitextParser(text).club_table()
    if fault=='duplicate':table=SimpleNamespace(seasons=table.seasons+[table.seasons[0]])
    return club_secondary_scopes(snapshot(text),MAPPING,API(),Wiki(fault),table,NOW,
                                 goalkeeper=fault=='keeper')


def test_primary_and_reserve_are_bound_to_the_right_article_club():
    values,entities,issues,scopes=run()
    assert not issues and len(scopes)==2
    assert set(values)=={('Q101','2015/16','Чемпионат'),('Q101','2015/16','Национальный кубок')}
    assert values[('Q101','2015/16','Чемпионат')]['numbers']=={'appearances':7,'goals':0}
    assert all(s['team_slug']=='sc-freiburg' for s in scopes)


@pytest.mark.parametrize('fault',['qid','redirect','duplicate','keeper'])
def test_ambiguous_team_or_keeper_metric_never_certifies_a_row(fault):
    values,entities,issues,scopes=run(fault)
    assert not values and not scopes
