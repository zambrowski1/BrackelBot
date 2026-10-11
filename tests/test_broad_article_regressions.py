# SPDX-License-Identifier: MIT
"""Real article structures; numeric source refresh is tested separately."""
from pathlib import Path
from copy import deepcopy
import hashlib
import mwparserfromhell as mw
import pytest
from wiki_stats.errors import UpdateError
from wiki_stats.wikitext_parser import WikitextParser, ValueRef
from wiki_stats.season_labels import normalize_table_season
from wiki_stats.legacy_club_table import migration_operation, migrate
from wiki_stats.change_validator import references, apply_patches
from wiki_stats.infobox_binding import operation, bind
from wiki_stats.sbmatches_create import create
from wiki_stats.native_national_table import replacement_block
from tests.test_sbmatches_create import operation as sample_operation
from tests.stage23_helpers import MINI

ROOT=Path(__file__).parent/'fixtures'/'broad_articles'


@pytest.mark.parametrize('raw,expected',[
    ('[[Чемпионат Германии по футболу 2023/2024|2023/24]]','2023/24'),
    ('2023/2024','2023/24'),('2023—2024','2023/24'),
    ('{{аренда}} 2023/24','2023/24'),('2023/24 (аренда)','2023/24'),('2024','2024')])
def test_real_season_labels_are_interpreted_without_rewriting(raw,expected):
    assert normalize_table_season(raw)==expected


@pytest.mark.parametrize('raw',['2023/25','2023/2025','[[Клуб]]','{{неизвестный}}2023/24'])
def test_unverified_season_is_a_diagnostic_instead_of_valueerror(raw):
    with pytest.raises(UpdateError):normalize_table_season(raw)


@pytest.mark.parametrize('n,player,count',[(5,'Тимо Вернер',14),(6,'Джейдон Санчо',11),(10,'Ваут Вегхорст',17)])
def test_real_legacy_tables_preserve_numbers_and_references(n,player,count):
    text=(ROOT/f'{n:02}.wiki').read_text(encoding='utf8')
    op=migration_operation(text,player,'2026-10-12')
    result,status=migrate(text,player,op)
    table=WikitextParser(result).club_table()
    assert status=='ready' and len(table.seasons)==count
    assert references(text)==references(result)
    assert migrate(result,player,op)==(result,'already_applied')
    # A table migration changes formatting only; the card is byte-identical.
    original=next(t for t in WikitextParser(text).templates if t.name=='футболист')
    assert str(original.node)==str(next(t.node for t in WikitextParser(result).templates if t.name=='футболист'))


@pytest.mark.parametrize('n,player,qid',[(1,'Бернд Лено','Q309966'),(11,'Сержиньо Дест','Q57456989')])
def test_implicit_names_need_explicit_guarded_binding(n,player,qid):
    text=(ROOT/f'{n:02}.wiki').read_text(encoding='utf8')
    with pytest.raises(UpdateError):WikitextParser(text).infobox(player)
    op=operation(text,{'wiki_name':player,'qid':qid},'2026-10-12')
    result,status=bind(text,player,op)
    assert status=='ready' and WikitextParser(result).infobox(player)['имя'].text.strip()==player
    assert bind(result,player,op)==(result,'already_applied')
    with pytest.raises(UpdateError):bind(text+'\n',player,dict(op,entity={'name':'Другой игрок'}))
    changed=text.replace('{{Футболист','{{Футболист\n|номер=999',1)
    with pytest.raises(UpdateError):bind(changed,player,op)


def test_unicode_conceded_goals_patch_preserves_notes_and_unknown_is_not_zero():
    value=ValueRef(mw.parse('4 (−54)<ref>Источник</ref>'),0)
    numbers=value.numeric(pair=True)
    assert [n.value for n in numbers]==[4,-54]
    assert apply_patches(value.text,[numbers[1].patch(-55,'goals')])=='4 (-55)<ref>Источник</ref>'
    with pytest.raises(UpdateError):ValueRef(mw.parse('4 (−?)'),0).numeric(pair=True)


def test_native_national_table_requires_guard_and_never_duplicates():
    text=(ROOT/'09.wiki').read_text(encoding='utf8')
    block=replacement_block(text,'Чехии')
    # Isolated formatting operation with synthetic rows, not Schick statistics.
    fixture=MINI+'\n== Статистика выступлений за сборную ==\n'+block[2]
    op=sample_operation()
    with pytest.raises(UpdateError):create(fixture,'Кальвин Браккельман',op)
    op['payload'].update(expected_native_sha256=hashlib.sha256(block[2].encode()).hexdigest(),native_team_gen='Чехии')
    with pytest.raises(UpdateError) as exc:
        create(fixture,'Кальвин Браккельман',op)
    assert exc.value.code=='possible_national_statistics'
    assert replacement_block(fixture,'Чехии')[2]==block[2]
    with pytest.raises(UpdateError):create(fixture.replace('27 мая 2016','28 мая 2016'),'Кальвин Браккельман',op)


def test_native_national_identity_and_notes_are_not_discarded():
    text=(ROOT/'09.wiki').read_text(encoding='utf8')
    with pytest.raises(UpdateError):replacement_block(text,'Германии')
    with pytest.raises(UpdateError):replacement_block(text.replace('27 мая 2016','27 мая 2016<ref>Дата</ref>'),'Чехии')


def test_source_embedded_numbers_need_participation_and_proven_goal_closure():
    from wiki_stats.sofascore_national import embedded_numbers
    row={'player':{'statistics':{'minutes_played':60},'incidents':None}}
    assert embedded_numbers(row,1) is None
    assert embedded_numbers(row,1,goals_closed=True)=={'appearances':1,'goals':0}
    row['player']['statistics']['minutes_played']=None
    assert embedded_numbers(row,1,goals_closed=True) is None
    row['player']['statistics']['minutes_played']=60
    row['player']['incidents']={'goals':2,'penalty_goals':1}
    assert embedded_numbers(row,1)=={'appearances':1,'goals':2}


def test_missing_provider_response_is_reused_briefly_but_not_permanent():
    from tests.test_sofascore import Store,Session,response
    from wiki_stats.sofascore_client import SofascoreClient
    clock=[100]
    session=Session([response(status=404),response(data={'player':{'id':1}})])
    api=SofascoreClient(Store(),key='SYNTHETIC',session=session,clock=lambda:clock[0])
    for _ in range(2):
        with pytest.raises(UpdateError):api.request('/players/details',{'player_id':1})
    assert len(session.calls)==1
    clock[0]+=301
    assert api.request('/players/details',{'player_id':1})['player']['id']==1
    assert len(session.calls)==2


@pytest.mark.parametrize('english_title',['New Club FC','FC New Club'])
def test_unknown_source_club_requires_reversible_football_identity(english_title):
    from tests.test_entity_resolver import IdentityClient
    from wiki_stats.entity_resolver import EntityResolver
    from wiki_stats.source_team_identity import resolve_source_team
    class Wiki(IdentityClient):
        def get_foreign_page_identity(self,title,language):
            if title==english_title:return {'title':title,'qid':'Q999'}
            raise UpdateError('entity_not_found','Missing')
    wiki=Wiki()
    wiki.objects['Q999']={'sitelinks':{'ruwiki':{'title':'Новый клуб (футбольный клуб)'},'enwiki':{'title':english_title}},
        'claims':{'P31':[{'mainsnak':{'datavalue':{'value':{'id':'Q476028'}}}}],
                  'P17':[{'mainsnak':{'datavalue':{'value':{'id':'Q183'}}}}]}}
    team={'name':'New Club','country':{'alpha2':'DE'}}
    resolved=resolve_source_team(team,wiki,EntityResolver(wiki,[]))
    assert resolved.qid=='Q999' and resolved.display=='Новый клуб'
    with pytest.raises(UpdateError):resolve_source_team(dict(team,country={'alpha2':'FR'}),wiki,EntityResolver(wiki,[]))
    wiki.objects['Q999']['sitelinks']['enwiki']['title']='Other Club'
    with pytest.raises(UpdateError):resolve_source_team(team,wiki,EntityResolver(wiki,[]))


def test_national_section_does_not_block_independent_club_section():
    from tests.test_statistics_section import BARE,prepare
    raw=(ROOT/'09.wiki').read_text(encoding='utf8')
    block=replacement_block(raw,'Чехии')
    text=BARE.replace('== Примечания ==','== Статистика выступлений за сборную ==\n'+block[2]+'\n== Примечания ==')
    _,plan=prepare(text)
    assert plan.changes[0].status=='ready'
    assert plan.preview.count(block[2])==1
    assert plan.preview.index('=== Клубная статистика ===')<plan.preview.index('== Статистика выступлений за сборную ==')


def test_british_club_flag_requires_verified_constituent_country():
    from tests.test_entity_resolver import IdentityClient
    from wiki_stats.entity_resolver import EntityResolver
    wiki=IdentityClient()
    claim=lambda q:{'mainsnak':{'datavalue':{'value':{'id':q}}}}
    wiki.objects['Q999']={'sitelinks':{'ruwiki':{'title':'Клуб Англии'}},
        'claims':{'P31':[claim('Q476028')],'P17':[claim('Q145')],'P159':[claim('Q1000')]}}
    wiki.objects['Q1000']={'claims':{'P131':[claim('Q21')]}}
    result=EntityResolver(wiki,[]).resolve({'name':'Клуб Англии','kind':'club','wikidata_id':'Q999'})
    assert result.flag=='Англии'
    wiki.objects['Q1000']['claims']['P131']=[claim('Q21'),claim('Q22')]
    with pytest.raises(UpdateError):EntityResolver(wiki,[]).resolve({'name':'Клуб Англии','kind':'club','wikidata_id':'Q999'})


def test_real_trapp_redirect_and_multiple_spells_are_preserved():
    from wiki_stats.klstat_normalization import operation,execute
    text=(ROOT/'02.wiki').read_text(encoding='utf8')
    op=operation(text,'Кевин Трапп','2026-10-12')
    result,status=execute(text,'Кевин Трапп',op)
    table=WikitextParser(result).club_table()
    assert status=='ready'
    original=[str(t.node) for t in WikitextParser(text).templates if t.name=='клстат/сезон']
    updated=[str(t.node) for t in WikitextParser(result).templates if t.name=='клстат/сезон']
    assert len(table.seasons)==21 and sorted(original)==sorted(updated)
    assert references(text)==references(result)
    assert execute(result,'Кевин Трапп',op)==(result,'already_applied')
    assert 'КлубнаяСтатистика/Лига' not in result
    assert '{{КлСтат/Сезон|2007/08|11|-6|-|-|-|-|-|-}}' in result


def test_implicit_name_operation_is_valid_in_mainspace_package():
    from wiki_stats.schema_validator import validate_package
    text=(ROOT/'01.wiki').read_text(encoding='utf8')
    op=operation(text,{'wiki_name':'Бернд Лено','qid':'Q309966'},'2026-10-12')
    article={'title':'Лено, Бернд','player':'Бернд Лено','subject_scope':'football_statistics','base_revid':1,'operations':[op]}
    package={'schema_version':'1.1','package_id':'name-binding','generated_at':'2026-10-12T00:00:00Z','articles':[article]}
    validate_package(package)
    del article['base_revid']
    with pytest.raises(UpdateError):validate_package(package)


@pytest.mark.parametrize('old',['','?','—','0'])
def test_unknown_shirt_number_uses_exact_guard_and_confirmed_club(old):
    from tests.stage23_helpers import AUGSBURG,FakeResolver,operation as make_op
    from wiki_stats.structural_editor import execute_operation
    text=MINI.replace('2<!-- сохранить -->',old)
    op=make_op('shirt','update_shirt_number',
        entity={'name':'Аугсбург','kind':'club','wikidata_id':'Q15755','wikitext':AUGSBURG},
        payload={'expected_raw':old,'number':3})
    result,status=execute_operation(text,'Кальвин Браккельман',op,FakeResolver())
    assert status=='ready' and '| номер = 3\n' in result
    assert execute_operation(result,'Кальвин Браккельман',op,FakeResolver())==(result,'already_applied')
    with pytest.raises(UpdateError):execute_operation(text.replace('| номер = '+old+'\n','| номер = 9\n'),'Кальвин Браккельман',op,FakeResolver())
    op['entity']['wikidata_id']='Q41420'
    with pytest.raises(UpdateError):execute_operation(text,'Кальвин Браккельман',op,FakeResolver())


def test_real_autonumber_national_table_cannot_be_duplicated():
    from wiki_stats.native_national_table import find_tables
    text=(ROOT/'02.wiki').read_text(encoding='utf8')
    assert len(find_tables(text))==1
    with pytest.raises(UpdateError) as exc:replacement_block(text,'Германии')
    assert exc.value.code=='needs_review'
    with pytest.raises(UpdateError):create(text,'Кевин Трапп',sample_operation())


def test_real_named_national_caption_with_notes_is_preserved():
    from wiki_stats.native_national_table import find_tables
    text=(ROOT/'06.wiki').read_text(encoding='utf8')
    assert len(find_tables(text))==1
    with pytest.raises(UpdateError):replacement_block(text,'Англии')
    with pytest.raises(UpdateError):create(text,'Джейдон Санчо',sample_operation())


def test_existing_legacy_totals_can_be_corrected_without_new_statistics():
    from wiki_stats.structural_editor import recompute_totals
    from wiki_stats.change_validator import arithmetic_warnings
    text=(ROOT/'06.wiki').read_text(encoding='utf8')
    op=migration_operation(text,'Джейдон Санчо','2026-10-12')
    converted=migrate(text,'Джейдон Санчо',op)[0]
    assert arithmetic_warnings(converted)
    fixed=recompute_totals(converted)
    assert not arithmetic_warnings(fixed)
    seasons=lambda s:[str(t.node) for t in WikitextParser(s).templates if t.name=='клстат/сезон']
    assert seasons(converted)==seasons(fixed)
    assert references(converted)==references(fixed)
    assert recompute_totals(fixed)==fixed


def test_derived_national_summary_is_inside_replacement_guard():
    text=(ROOT/'09.wiki').read_text(encoding='utf8')
    block=replacement_block(text,'Чехии')
    summary="\n'''Итого: 2 матча / 1 гол; 1 победа'''"
    text=text[:block[1]]+summary+text[block[1]:]
    assert replacement_block(text,'Чехии')[2].endswith(summary)


def test_formatting_date_does_not_override_or_block_numeric_coverage():
    from tests.stage23_helpers import AUGSBURG,FakeResolver,snapshot,operation as make_op
    from wiki_stats.change_planner import plan_article
    text=MINI.replace('| имя = Кальвин Браккельман\n','')
    binding=operation(text,{'wiki_name':'Кальвин Браккельман','qid':'Q114406918'},'2026-10-12')
    shirt=make_op('shirt','update_shirt_number',
        entity={'name':'Аугсбург','kind':'club','wikidata_id':'Q15755','wikitext':AUGSBURG},
        payload={'expected_number':2,'number':3})
    shirt['group_id']=binding['group_id']
    shirt['as_of']='2021-10-09'
    for source in shirt['sources']:source['coverage_as_of']=shirt['as_of']
    page=snapshot(text)
    article={'title':page.title,'player':'Кальвин Браккельман','base_revid':page.revid,'operations':[binding,shirt]}
    plan=plan_article(page,article,'1.1',FakeResolver())
    assert all(c.status=='ready' for c in plan.changes)
    assert plan.changes[1].operation['as_of']=='2021-10-09'
    assert '| номер = 3<!-- сохранить -->' in plan.preview
    other=deepcopy(shirt);other['id']='different-numeric-date';other['as_of']='2022-10-09'
    for source in other['sources']:source['coverage_as_of']=other['as_of']
    article['operations'].append(other)
    rejected=plan_article(page,article,'1.1',FakeResolver())
    assert rejected.preview==text
    assert any(c.status=='source_coverage_mismatch' for c in rejected.changes)


def test_real_unused_bench_matches_require_independent_appearance_closure():
    import json
    from datetime import datetime,timezone
    from wiki_stats.sofascore_match_stats import played_matches
    evidence=json.loads((ROOT/'karius-bench-history.json').read_text(encoding='utf8'))
    history,record=evidence['history'],evidence['record']
    now=datetime(2026,10,12,tzinfo=timezone.utc)
    rows=played_matches(history,record,now)
    assert len(history['matches'])==30 and len(rows)==4
    assert all(r['player']['statistics']['minutes_played']>0 for r in rows)
    mismatch=deepcopy(record);mismatch['numbers']['appearances']=5
    assert played_matches(history,mismatch,now) is None
    assert played_matches(dict(history,complete=False),record,now) is None
    uncertain=deepcopy(history)
    uncertain['matches'][0]['player']['is_on_bench']=False
    assert played_matches(uncertain,record,now) is None
