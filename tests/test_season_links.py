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


@pytest.mark.parametrize('label',[
 '2025/26 {{аренда}}<!--пометка--><ref>Источник</ref>',
 '2025/26 (аренда)',
])
def test_annotations_inside_existing_link_are_preserved(label):
 old='[[Старая ссылка|'+label+']]'
 new=replacement(old,TITLE,'2025/2026')
 assert new=='[['+TITLE+'|'+label.replace('2025/26','2025/2026')+']]'
 assert references(new)==references(old)


def test_cell_footnotes_comments_and_loan_marker_survive_planning_and_replay():
 old='[[Чемпионат Германии по футболу 2025/26|2025/26 {{аренда}}]]<!--пометка--><ref>Источник</ref>'
 text=MINI.replace('2025/26',old)
 op,issues=prepare(text,'Кальвин Браккельман','2026-10-12',Wiki())
 assert op and not issues
 updated,status=execute(text,'Кальвин Браккельман',op,SimpleNamespace(client=Wiki()))
 assert status=='ready' and references(updated)==references(text)
 assert '2025/2026 {{аренда}}]]<!--пометка--><ref>Источник</ref>' in updated
 assert execute(updated,'Кальвин Браккельман',op,SimpleNamespace(client=Wiki()))==(updated,'already_applied')


def test_wrong_league_and_wrong_redirect_target_are_refused():
 op,_=prepare(MINI,'Кальвин Браккельман','2026-10-12',Wiki())
 with pytest.raises(UpdateError):
  execute(MINI.replace('Чемпионат Германии по футболу','Вторая Бундеслига'),'Кальвин Браккельман',op,SimpleNamespace(client=Wiki()))
 class Wrong(Wiki):
  def get_page_identity(self,title):return {'title':'Чемпионат Германии по футболу 2024/2025'}
 assert prepare(MINI,'Кальвин Браккельман','2026-10-12',Wrong())[0] is None


def test_unknown_season_is_reported_without_destroying_article():
 text=MINI.replace('2025/26','неизвестно')
 op,issues=prepare(text,'Кальвин Браккельман','2026-10-12',Wiki())
 assert op is None and issues[0]['reason']=='unknown_season'


def test_full_cycle_links_seasons_after_statistics_and_flags_and_replays():
 from datetime import datetime,timezone
 from tests.test_sofascore import Wiki as BaseWiki, API, Resolver
 from wiki_stats.sofascore_cycle import prepare_player
 class SeasonWiki(BaseWiki):
  def get_page_identity(self,title):
   if title.startswith('Чемпионат Германии по футболу '):return {'title':title,'qid':None}
   return super().get_page_identity(title)
 wiki=SeasonWiki();resolver=Resolver();resolver.client=wiki
 mapping={'api_id':1,'qid':'Q114406918','title':'Игрок','wiki_name':'Кальвин Браккельман','birth_date':'1999-08-22'}
 text=MINI.replace('| имя =','| дата рождения = 22.08.1999\n| имя =').replace('2025/26','2026/27')
 snap=snapshot(text);now=datetime(2026,10,9,tzinfo=timezone.utc)
 pkg,plan,report=prepare_player(snap,mapping,API(),wiki,resolver,now=now)
 assert any(op['type']=='normalize_season_links' for op in pkg['articles'][0]['operations'])
 assert all(c.status in {'ready','already_applied'} for c in plan.changes)
 assert '{{КлСтат/Сезон|[[Чемпионат Германии по футболу 2026/2027|2026/2027]]|5|2' in plan.preview
 assert not prepare_player(replace(snap,text=plan.preview),mapping,API(),wiki,resolver,now=now)[1].diff
