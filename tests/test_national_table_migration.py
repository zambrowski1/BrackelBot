from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import pytest
from wiki_stats.national_table_migration import operation,migrate,parse_table
from wiki_stats.native_national_table import find_tables
from wiki_stats.change_validator import references
from wiki_stats.change_planner import plan_article
from wiki_stats.schema_validator import validate_package
from wiki_stats.sbmatches_editor import read_matches
from wiki_stats.wikitext_parser import WikitextParser
from wiki_stats.errors import UpdateError
from tests.stage23_helpers import snapshot

PLAYER='Патрик Шик'
DAY='2026-10-12'
FIXTURE=Path(__file__).parent/'fixtures/schick-national-legacy.wiki'


def text():return FIXTURE.read_text(encoding='utf-8')


def sb(value):return next(t for t in WikitextParser(value).templates if t.name=='сбматчи')


def package(value,op):
    return {'schema_version':'1.1','package_id':'national-format-test','generated_at':DAY+'T00:00:00Z',
            'articles':[{'title':value.title,'base_revid':value.revid,'player':PLAYER,'operations':[op]}]}


@pytest.mark.parametrize('crlf',[False,True])
def test_fifty_schick_matches_are_lossless_and_only_exact_table_is_replaced(crlf):
    before=text()
    if crlf:before=before.replace('\n','\r\n')
    op=operation(before,PLAYER,DAY)
    after,status=migrate(before,PLAYER,op)
    offset,node=find_tables(before)[0]
    assert status=='ready' and op['payload']['match_count']==50
    assert before[:offset]==after[:offset]
    assert before[offset+len(str(node)):]==after[offset+len(str(sb(after).node)):]
    assert len(read_matches(sb(after))[1])==50
    assert not find_tables(after) and after.count('{{СбМатчи')==1
    assert '{{обновлено|2025-12-17}}' in after
    assert '|соперник1={{футбол|Мальта}}' in after
    assert 'ЧЕ-2020. Групповая стадия' in after
    assert migrate(after,PLAYER,op)==(after,'already_applied')
    assert operation(after,PLAYER,DAY) is None


def test_row_and_caption_sources_preserve_exact_contents_and_order():
    before=text().replace('Матчи за сборную Чехии','Матчи за сборную Чехии<ref name="caption">Общий источник</ref>',1)
    before=before.replace('|27 мая 2016','|27 мая 2016<ref name="d">Источник даты</ref>',1)
    before=before.replace('|{{футбол|Мальта}}','|{{футбол|Мальта}}<ref name="m">{{Cite web|url=https://example.com/match|title=Матч}}</ref>',1)
    after,_=migrate(before,PLAYER,operation(before,PLAYER,DAY))
    assert references(before)==references(after)
    assert '|источник1=<ref name="d">' in after
    assert len(read_matches(sb(after))[1])==50


@pytest.mark.parametrize('fault',['other_country','other_player','missing_number','extra_column','comment','combined_cell','second_table','unknown_table','old_total','wrong_score'])
def test_ambiguous_or_unsupported_table_is_never_removed(fault):
    before=text()
    if fault=='other_country':before=before.replace('Матчи за сборную Чехии','Матчи за сборную Германии')
    elif fault=='other_player':before=before.replace('Матчи за сборную Чехии','Матчи Ивана Петрова за сборную Чехии')
    elif fault=='missing_number':before=before.replace('|align=center|2\n','|align=center|3\n',1)
    elif fault=='extra_column':before=before.replace('!!Соревнование','!!Соревнование!!Минуты',1)
    elif fault=='comment':before=before.replace('|27 мая 2016','|27 мая 2016<!-- сохранить -->',1)
    elif fault=='combined_cell':before=before.replace('|align=center|1\n','|rowspan=2|1\n',1)
    elif fault=='second_table':before+=str(find_tables(before)[0][1])
    elif fault=='unknown_table':before=before.replace('== Примечания ==','{| class="wikitable"\n! Дата !! Соперник\n| 2020 || Германия\n|}\n== Примечания ==')
    elif fault=='old_total':before=before.replace('|}\n',"|}\n'''Итого: 50 матчей'''\n",1)
    elif fault=='wrong_score':before=before.replace('|align=center|6:0','|align=center|0:0',1)
    with pytest.raises(UpdateError):operation(before,PLAYER,DAY)
    assert '{{СбМатчи' not in before


@pytest.mark.parametrize('fault',['cell','layout','payload_count','payload_rows','existing_template','changed_template'])
def test_stale_hash_and_duplicate_guards(fault):
    before=text();op=operation(before,PLAYER,DAY)
    if fault=='cell':before=before.replace('|align=center|6:0','|align=center|7:0',1)
    elif fault=='layout':before=before.replace('class="wikitable','class="wikitable extra',1)
    elif fault=='payload_count':op['payload']['match_count']=49
    elif fault=='payload_rows':op['payload']['matches_sha256']='0'*64
    elif fault=='existing_template':before+='\n{{СбМатчи|игрок=Патрик Шик}}'
    else:
        before,_=migrate(before,PLAYER,op)
        before=before.replace('|голы1=1','|голы1=2',1)
    with pytest.raises(UpdateError):migrate(before,PLAYER,op)


def test_schema_and_planner_bind_to_revision_and_replay_without_diff():
    before=text();page=snapshot(before);op=operation(before,PLAYER,DAY);pkg=package(page,op)
    validate_package(pkg)
    plan=plan_article(page,pkg['articles'][0],'1.1')
    assert plan.changes[0].status=='ready'
    repeated=plan_article(replace(page,text=plan.preview),pkg['articles'][0],'1.1')
    assert not repeated.diff and repeated.changes[0].status=='already_applied'
    changed=plan_article(replace(page,revid=page.revid+1),pkg['articles'][0],'1.1')
    assert not changed.diff and changed.changes[0].status=='revision_conflict'


def test_conversion_passes_publication_recheck_with_mock_client(tmp_path):
    from wiki_stats.publisher import Publisher
    from wiki_stats.transactions import approve_change
    from wiki_stats.models import Mode
    from tests.stage23_helpers import MemoryClient
    page=snapshot(text());op=operation(page.text,PLAYER,DAY)
    plan=plan_article(page,package(page,op)['articles'][0],'1.1')
    approve_change(plan,op['id'])
    client=MemoryClient(page)
    Publisher(backup_dir=tmp_path).publish(plan,client,mode=Mode.MANUAL)
    assert len(client.writes)==1 and client.writes[0][1]==plan.preview


def test_command_is_independent_of_database_and_football_provider(tmp_path,capsys):
    import json
    from wiki_stats.server_cli import main
    fixtures=tmp_path/'fixtures';fixtures.mkdir()
    (fixtures/'page.wiki').write_text(text(),encoding='utf-8')
    (fixtures/'page.json').write_text(json.dumps({'title':'Шик, Патрик','revid':123,'timestamp':'2026-10-12T00:00:00Z'}),encoding='utf-8')
    out=tmp_path/'output'
    assert main(['--provider','paused','prepare-national-table','Шик, Патрик','--player',PLAYER,'--fixtures',str(fixtures),'--output-dir',str(out)])==0
    assert '{{СбМатчи' in (out/'after.wiki').read_text(encoding='utf-8')
    assert (out/'package.json').exists() and (out/'changes.diff').exists()
