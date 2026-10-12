from types import SimpleNamespace
from dataclasses import replace
import pytest
from tests.stage23_helpers import MINI,snapshot,package
from wiki_stats.season_links import prepare,execute,replacement,candidates
from wiki_stats.change_planner import plan_article
from wiki_stats.schema_validator import validate_package
from wiki_stats.change_validator import references
from wiki_stats.errors import UpdateError

TITLE='Чемпионат Германии по футболу 2025/2026'
class Wiki:
 def get_page_identity(self,title):
  if title!=TITLE:raise UpdateError('entity_not_found','Missing')
  return {'title':title,'qid':None}


def test_full_years_are_not_truncated_when_linking():
 assert replacement('2026/2027','Чемпионат Германии по футболу 2026/2027','2026/2027')=='[[Чемпионат Германии по футболу 2026/2027|2026/2027]]'


def test_link_operation_preserves_numbers_refs_and_replays():
 op,issues=prepare(MINI,'Кальвин Браккельман','2026-10-12',Wiki())
 assert op and not issues
 pkg=package([op]);pkg['generated_at']='2026-10-12T00:00:00Z';pkg['articles'][0]['base_revid']=snapshot(MINI).revid
 validate_package(pkg)
 resolver=SimpleNamespace(client=Wiki());plan=plan_article(snapshot(MINI),pkg['articles'][0],'1.1',resolver)
 assert plan.changes[0].status=='ready' and '[['+TITLE+'|2025/2026]]' in plan.preview
 assert references(MINI)==references(plan.preview)
 assert not plan_article(replace(snapshot(MINI),text=plan.preview),pkg['articles'][0],'1.1',resolver).diff


@pytest.mark.parametrize('season',['2009/10','2027/28'])
def test_before_2010_and_future_seasons_are_not_changed(season):
 text=MINI.replace('2025/26',season)
 assert prepare(text,'Кальвин Браккельман','2026-10-12',Wiki())==(None,[])


def test_missing_article_does_not_create_red_link():
 text=MINI.replace('2025/26','2024/25')
 op,issues=prepare(text,'Кальвин Браккельман','2026-10-12',Wiki())
 assert op is None and issues[0]['code']=='season_article_missing'
