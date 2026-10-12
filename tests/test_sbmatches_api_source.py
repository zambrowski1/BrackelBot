"""Replay the actual 12 October API scope, with no network or credentials."""
import gzip,json
from pathlib import Path
from dataclasses import replace
import pytest
from wiki_stats.national_team_identity import NationalTeamIdentity
from wiki_stats.national_match_format import format_scope
from wiki_stats.national_table_migration import operation,migrate
from wiki_stats.sofascore_sbmatches import full_repair
from wiki_stats.change_planner import plan_article
from wiki_stats.change_validator import references
from wiki_stats.schema_validator import validate_package
from wiki_stats.wikitext_parser import WikitextParser
from wiki_stats.sbmatches_editor import read_matches
from wiki_stats.errors import UpdateError
from tests.stage23_helpers import snapshot

ROOT=Path(__file__).parent


def scope():
    return json.loads(gzip.decompress((ROOT/'source-fixtures/schick-national-scope.json.gz').read_bytes()))['scope']


class Wiki:
    def __init__(self):
        catalogue=NationalTeamIdentity(self).catalogue
        self.ids={}
        for i,(country,row) in enumerate(catalogue['countries'].items(),1):
            self.ids[row['title']]=self.ids[row['english_title']]='Q'+str(i)
    def get_page_identity(self,title):return {'title':title,'qid':self.ids[title]}
    def get_foreign_page_identity(self,title,language):return self.get_page_identity(title)


def test_actual_old_schick_list_conflict_preserves_the_entire_table():
    old=(ROOT/'fixtures/schick-national-legacy.wiki').read_text(encoding='utf8')
    text,_=migrate(old,'Патрик Шик',operation(old,'Патрик Шик','2026-10-12'))
    with pytest.raises(UpdateError) as exc:
        full_repair(snapshot(text),{'wiki_name':'Патрик Шик','api_id':280609},scope(),Wiki())
    assert exc.value.code=='national_existing_match_unmatched'
    assert len(read_matches(next(t for t in WikitextParser(text).templates if t.name=='сбматчи'))[1])==50


def test_actual_api_scope_updates_a_matching_old_subset_and_replays_without_changes():
    source=scope();wiki=Wiki();rows,_=format_scope(source,NationalTeamIdentity(wiki).resolve)
    first=rows[0]
    text='{{Футболист|имя=Патрик Шик}}\n{{СбМатчи|игрок=Патрик Шик|сборная=Чехия\n'
    for key,value in first.items():text+='|'+key+'1='+value+'\n'
    text+='|примечание1=Сохранить заметку редактора\n|источник1=<ref>Старый источник</ref>\n}}\n== Примечания ==\n{{примечания}}'
    page=snapshot(text)
    op=full_repair(page,{'wiki_name':'Патрик Шик','api_id':280609},source,wiki)
    pkg={'schema_version':'1.1','package_id':'real-source-replay','generated_at':'2026-10-12T00:00:00Z',
         'articles':[{'title':page.title,'base_revid':page.revid,'player':'Патрик Шик','operations':[op]}]}
    validate_package(pkg);plan=plan_article(page,pkg['articles'][0],'1.1')
    assert plan.changes[0].status=='ready'
    t=next(t for t in WikitextParser(plan.preview).templates if t.name=='сбматчи')
    _,result=read_matches(t)
    assert len(result)==56 and sum(int(r['голы']) for r in result)==26
    assert result[0]['примечание']=='Сохранить заметку редактора'
    assert references(plan.preview)==references(text)
    assert not plan_article(replace(page,text=plan.preview),pkg['articles'][0],'1.1').diff
    assert full_repair(replace(page,text=plan.preview),{'wiki_name':'Патрик Шик','api_id':280609},source,wiki) is None
