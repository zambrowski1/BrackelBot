# SPDX-License-Identifier: MIT
from copy import deepcopy
from dataclasses import replace
from datetime import datetime,timezone
import pytest
from tests.test_sofascore import API,Wiki,Resolver,MINI,snapshot
from tests.test_statistics_section import evidence,BARE,resolver
from tests.stage23_helpers import TABLE_AUGSBURG,operation,package
from wiki_stats.statistics_section import render_section
from wiki_stats.wikitext_parser import WikitextParser
from wiki_stats.change_validator import arithmetic_warnings
from wiki_stats.structural_editor import recompute_totals,execute_operation
from wiki_stats.schema_validator import validate_package
from wiki_stats.sofascore_cycle import prepare_player


MAPPING={'api_id':1,'qid':'Q114406918','title':'Игрок','wiki_name':'Кальвин Браккельман','birth_date':'1999-08-22'}
NOW=datetime(2026,10,9,tzinfo=timezone.utc)


def test_all_seasons_includes_2016_transfer_without_protocol_recount():
    class OldSeason(API):
        def seasons(self,pid):
            return {'player_id':pid,'tournament_seasons':[{'tournament':{'id':35},'seasons':[{'id':16,'year':'16/17'}]}]}
        def matches(self,*args,**kwargs):return {'complete':False,'matches':[]}
        def transfers(self,pid):
            return {'player_id':pid,'transfers':[{'transfer_date':'2016-08-01','transfer_from':{'id':99},'transfer_to':{'id':2600}}]}
        def match_statistics(self,*args):raise AssertionError('No old match recount')
    text=MINI.split('{{КлСтат|')[0].replace('| имя =','| дата рождения = 22.08.1999\n| имя =')
    _,plan,report=prepare_player(snapshot(text),MAPPING,OldSeason(),Wiki(),Resolver(),statistics_since=0,now=NOW)
    assert '{{КлСтат/Сезон|2016/17|5|2}}' in plan.preview
    assert '|url=https://www.sofascore.com/football/player/1|' in plan.preview
    assert not any(i['code']=='split_season_unproven' for i in report['review'])


def test_source_link_rejects_markup_and_wrong_hosts():
    data=evidence();payload={**data['create_section'],'clubs':data['clubs']}
    from wiki_stats.errors import UpdateError
    for url in ['https://example.com/1','https://www.sofascore.com/football/player/1] {{Шаблон}}']:
        with pytest.raises(UpdateError):render_section({**payload,'source_url':url},resolver(),'2026-10-08')


def partial_table():
    d=evidence()
    payload={**d['create_section'],'clubs':d['clubs'],'complete':True,'allow_unknown':True}
    payload['clubs'][0]['rows'][0]['categories']['Кубок']=None
    return BARE+render_section(payload,resolver(),'2026-10-08')


def test_zero_display_sums_known_values_without_changing_source_payload():
    data=evidence()
    payload={**data['create_section'],'clubs':data['clubs'],'complete':True,'allow_unknown':True,'unknown_as_zero':True}
    payload['clubs'][0]['rows'][0]['categories']['Кубок']=None
    original=deepcopy(payload)
    text=render_section(payload,resolver(),'2026-10-08')
    assert payload==original
    assert '—' not in text and '?' not in text
    table=WikitextParser(text).club_table()
    assert table.seasons[0].values['4'].numeric()[0].value==0
    assert '{{КлСтат/ИтогоЗаКлуб||4|1|0|0|0|0}}' in text
    assert not arithmetic_warnings(text)


def test_unknown_display_keeps_known_league_and_omits_uncomputable_career_total():
    data=evidence()
    payload={**data['create_section'],'clubs':data['clubs'],'complete':True,'allow_unknown':True,
             'unknown_as_zero':False,'show_career_total':True}
    payload['clubs'][0]['rows'][0]['categories']['Кубок']=None
    text=render_section(payload,resolver(),'2026-10-08')
    table=WikitextParser(text).club_table()
    assert table.seasons[0].values['2'].numeric()[0].value==4
    assert table.seasons[0].values['4'].numeric()[0].value is None
    assert not table.grand_total and '?' not in text
    assert not arithmetic_warnings(text)


def test_source_is_inside_updated_marker_and_career_total_is_summed():
    data=evidence()
    payload={**data['create_section'],'clubs':data['clubs'],'complete':True,'allow_unknown':True,
             'unknown_as_zero':True,'show_career_total':True,
             'source_url':'https://www.sofascore.com/football/player/patrik-schick/280609'}
    payload['clubs'][0]['rows'][0]['categories']['Кубок']=None
    text=render_section(payload,resolver(),'2026-10-08')
    parser=WikitextParser(text)
    updated=next(t for t in parser.templates if t.name=='обновлено')
    assert '<ref>{{Cite web|url='+payload['source_url'] in updated.values()['2'].text
    assert 'Источник:' not in text
    assert '{{КлСтат/Итого|4|1|0|0|0|0}}' in text
    assert not arithmetic_warnings(text)


def test_source_addition_never_allows_an_old_reference_to_be_changed():
    from wiki_stats.change_validator import apply_patches
    from wiki_stats.models import Patch
    from wiki_stats.errors import UpdateError
    original='Текст <ref>Старый источник</ref>'
    added='<ref>Новый источник</ref>'
    assert apply_patches(original,[Patch(len(original),len(original),'',added,'source')],structural=True,added_references=[added])==original+added
    with pytest.raises(UpdateError):
        apply_patches(original,[Patch(0,len(original),original,added,'source')],structural=True,added_references=[added])


def test_unknowns_render_as_em_dashes_with_no_native_question_mark_totals():
    text=partial_table()
    assert '?' not in text and '|—|—|' in text
    assert '|игры=—|голы=—}}' in text
    assert '! scope="row" colspan="2" | Итого' in text
    assert not arithmetic_warnings(text)
    table=WikitextParser(text).club_table()
    assert table.seasons[0].values['4'].numeric()[0].value is None
    assert table.seasons[0].values['2'].numeric()[0].value==4
    assert recompute_totals(text)==text


def test_fill_unknown_cells_updates_raw_subtotal_and_replays_without_changes():
    text=partial_table()
    op=operation('fill','update_stats',entity={'name':'Аугсбург','wikitext':TABLE_AUGSBURG},
        target={'structure':'club_table'},season='2025/26',
        competition={'name':'Кубок','category':'Кубок','scope':'category'},
        expected={'appearances':None,'goals':None},new={'appearances':1,'goals':0})
    validate_package(package([op]))
    after,status=execute_operation(text,MAPPING['wiki_name'],op,resolver())
    assert status=='ready' and not arithmetic_warnings(after)
    assert '|игры=5|голы=1}}' in after
    assert execute_operation(after,MAPPING['wiki_name'],op,resolver())[1]=='already_applied'


def test_existing_total_is_preserved_when_one_category_is_unknown():
    text=partial_table().replace('4 || 1 || — || — || 0 || 0 || — || —',
        '4 || 1 || — || — || 0 || 0 || 10 || 2')
    assert recompute_totals(text)==text
    assert not arithmetic_warnings(text)


def test_aggregate_mode_accepts_valid_season_without_complete_protocols():
    class Aggregate(API):
        def statistics(self,pid,tid,sid,part):
            from wiki_stats.errors import UpdateError
            if part=='regularSeason':raise UpdateError('api_not_found','regularSeason unavailable')
            return super().statistics(pid,tid,sid,part)
        def matches(self,pid,max_pages=12):return {'complete':False,'matches':[]}
        def match_statistics(self,*args):raise AssertionError('must not recount season aggregate')
    text=MINI.replace('| имя =','| дата рождения = 22.08.1999\n| имя =')
    _,plan,report=prepare_player(snapshot(text),MAPPING,Aggregate(),Wiki(),Resolver(),statistics_since=2020,now=NOW)
    assert report['collection_mode']=='season_aggregates'
    assert report['covered_statistics']==1
    assert 'overall_league_unproven' not in {i['code'] for i in report['review']}
    assert any(c.operation['type']=='update_stats' for c in plan.changes)


def test_missing_categories_do_not_omit_a_season_and_result_is_idempotent():
    text=MINI.split('{{КлСтат|')[0].replace('| имя =','| дата рождения = 22.08.1999\n| имя =')
    _,plan,report=prepare_player(snapshot(text),MAPPING,API(),Wiki(),Resolver(),statistics_since=2020,now=NOW)
    # The provider only lists the league; no other category is fabricated.
    assert '{{КлСтат/Сезон|2026/27|5|2}}' in plan.preview
    _,second,_=prepare_player(replace(snapshot(text),text=plan.preview),MAPPING,API(),Wiki(),Resolver(),statistics_since=2020,now=NOW)
    assert not second.diff


def test_unknown_in_infobox_expected_values_is_rejected():
    op=operation('bad','update_stats',season=None,
        competition={'name':'Чемпионат','category':'all','scope':'league'},
        expected={'appearances':None,'goals':None},new={'appearances':5,'goals':2})
    from wiki_stats.errors import UpdateError
    with pytest.raises(UpdateError):validate_package(package([op]))


def test_partial_season_creation_keeps_known_cells_and_explicit_zero():
    text=partial_table()
    from tests.stage23_helpers import LEAGUE
    op=operation('next','add_season',entity={'name':'Аугсбург','wikitext':TABLE_AUGSBURG},
        target={'structure':'club_table'},season='2026/27',payload={
            'complete':True,'allow_unknown':True,'league_wikitext':LEAGUE,
            'insert_after_season':'2025/26','categories':{
                'Чемпионат':{'appearances':5,'goals':2},'Кубок':None,
                'Стыковые матчи':{'appearances':0,'goals':0}}})
    validate_package(package([op]))
    after,status=execute_operation(text,MAPPING['wiki_name'],op,resolver())
    assert status=='ready' and not arithmetic_warnings(after)
    assert '|2026/27|5|2|—|—|0|0|игры=—|голы=—}}' in after
    assert execute_operation(after,MAPPING['wiki_name'],op,resolver())[1]=='already_applied'


def test_plan_can_replace_unknown_cells_without_rewriting_source_refs():
    from wiki_stats.change_planner import plan_article
    text=partial_table()
    op=operation('fill','update_stats',entity={'name':'Аугсбург','wikitext':TABLE_AUGSBURG},
        target={'structure':'club_table'},season='2025/26',
        competition={'name':'Кубок','category':'Кубок','scope':'category'},
        expected={'appearances':None,'goals':None},new={'appearances':1,'goals':0})
    pkg=package([op]);plan=plan_article(snapshot(text),pkg['articles'][0],'1.1',resolver())
    assert plan.changes[0].status=='ready'
    assert '—' not in plan.preview.split('{{КлСтат|',1)[1] and not arithmetic_warnings(plan.preview)


def test_missing_match_history_does_not_block_season_table_or_invent_date():
    class NoHistory(API):
        def matches(self,pid,max_pages=12):return {'complete':False,'matches':[]}
    text=MINI.split('{{КлСтат|')[0].replace('| имя =','| дата рождения = 22.08.1999\n| имя =')
    _,plan,report=prepare_player(snapshot(text),MAPPING,NoHistory(),Wiki(),Resolver(),statistics_since=2020,now=NOW)
    assert '{{КлСтат/Сезон|2026/27|5|2}}' in plan.preview
    assert '{{обновлено|' not in plan.preview
    assert all(c.status in {'ready','already_applied'} for c in plan.changes)
    assert report['confirmed_match_date'] is None
