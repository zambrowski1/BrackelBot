# SPDX-License-Identifier: MIT
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from wiki_stats.change_planner import plan_article
from wiki_stats.change_validator import arithmetic_warnings, references
from wiki_stats.schema_validator import validate_package
from wiki_stats.klstat_planner import prepare_table
from wiki_stats.errors import UpdateError
from stage23_helpers import *

BARE=MINI.split('{{КлСтат|')[0]+'== Примечания ==\n{{примечания}}\n== Ссылки ==\n* Ссылка\n[[Категория:Тест]]\n'


def resolver():
    result=FakeResolver()
    result.client=Mock()
    result.client.get_page_identity.return_value={'title':'Бундеслига','qid':'Q82595'}
    return result


def evidence():
    return {'player':'Кальвин Браккельман','as_of':'2026-10-08','sources':source(),
        'create_section':{'categories':['Чемпионат','Кубок','Стыковые матчи'],
                          'statistics_as_of':'2026-10-04','full_career':False},
        'clubs':[{'entity':{'name':'Аугсбург','kind':'club','wikidata_id':'Q15755','wikitext':TABLE_AUGSBURG},
                  'rows':[{'season':'2025/26','league_wikitext':LEAGUE,'categories':{
                    c:{'appearances':4 if c=='Чемпионат' else 0,'goals':1 if c=='Чемпионат' else 0}
                    for c in ['Чемпионат','Кубок','Стыковые матчи']}}]}]}


def prepare(text=BARE,data=None):
    return prepare_table(snapshot(text),data or evidence(),resolver())


@pytest.mark.parametrize('crlf',[False,True])
def test_new_section_verified_totals_placement_and_repeated_operation(crlf):
    text=BARE.replace('\n','\r\n') if crlf else BARE
    pkg,plan=prepare(text)
    assert plan.changes[0].status=='ready'
    assert not arithmetic_warnings(plan.preview)
    assert plan.preview.index('== Статистика выступлений ==')<plan.preview.index('== Примечания ==')
    assert '{{обновлено|2026-10-04}}' in plan.preview
    assert 'полный итог карьеры не заявляется' in plan.preview
    assert '{{КлСтат/Итого|4|1|0|0|0|0}}' in plan.preview
    assert references(plan.preview)==references(text)
    # Only the inserted section differs; article content remains byte-for-byte.
    insertion=plan.groups[0].patches
    assert len(insertion)==1 and insertion[0].before==''
    assert plan.preview[:insertion[0].start]+plan.preview[insertion[0].start+len(insertion[0].after):]==text
    repeated=plan_article(snapshot(plan.preview),pkg['articles'][0],'1.1',resolver())
    assert repeated.changes[0].status=='already_applied'
    assert not repeated.diff


@pytest.mark.parametrize('existing',['== Статистика ==\n','=== Выступления за клубы ===\n','{| class="wikitable"\n| Сезон || Матчи\n|}\n'])
def test_other_statistics_formats_block_creation(existing):
    pkg,plan=prepare(BARE+existing)
    assert plan.changes[0].status=='statistics_exists'
    assert plan.preview==BARE+existing


def test_existing_klstat_does_not_duplicate_even_with_other_numbers():
    pkg,plan=prepare()
    text=MINI
    result=plan_article(snapshot(text),pkg['articles'][0],'1.1',resolver())
    assert result.changes[0].status=='statistics_exists'
    assert result.preview==text


def test_incomplete_category_and_unverified_club_block_creation():
    data=evidence();del data['clubs'][0]['rows'][0]['categories']['Кубок']
    _,plan=prepare(data=data)
    assert plan.changes[0].status=='incomplete_statistics'
    assert not plan.diff
    data=evidence();data['clubs'][0]['entity']['wikitext']=WERDER
    assert prepare(data=data)[1].changes[0].status=='entity_mismatch'


def test_multiple_leagues_have_correct_rowspans():
    data=evidence();rows=data['clubs'][0]['rows'];next_row=deepcopy(rows[0]);next_row['season']='2026/27'
    next_row['league_wikitext']='[[Вторая Бундеслига]]';rows.append(next_row)
    _,plan=prepare(data=data)
    assert plan.changes[0].status=='ready'
    assert plan.preview.count('{{КлСтат/Лига|')==2
    assert '{{КлСтат/Итого|8|2|0|0|0|0}}' in plan.preview


def test_unknown_date_is_not_replaced_with_fetch_date():
    data=evidence();data['create_section']['statistics_as_of']='2026-10-09'
    _,plan=prepare(data=data)
    assert plan.changes[0].status=='source_coverage_mismatch'
    del data['create_section']['statistics_as_of']
    with pytest.raises(UpdateError):prepare(data=data)


def test_full_career_label_requires_explicit_flag():
    data=evidence();data['create_section']['full_career']=True
    _,plan=prepare(data=data)
    assert plan.changes[0].status=='ready'
    assert 'полный итог карьеры не заявляется' not in plan.preview


def test_category_markup_and_unavailable_league_block_creation():
    data=evidence();data['create_section']['categories'][1]='{{опасный шаблон}}'
    assert prepare(data=data)[1].changes[0].status=='unsupported_structure'
    r=resolver();r.client.get_page_identity.side_effect=UpdateError('entity_not_found','Нет лиги')
    _,plan=prepare_table(snapshot(BARE),evidence(),r)
    assert plan.changes[0].status=='entity_not_found'


def test_no_footer_places_section_before_categories():
    text=BARE.split('== Примечания ==')[0]+'[[Категория:Тест]]\n'
    _,plan=prepare(text)
    assert plan.changes[0].status=='ready'
    assert plan.preview.index('== Статистика выступлений ==')<plan.preview.index('[[Категория:Тест]]')


def test_create_publishes_through_normal_recheck_and_backup(tmp_path):
    from wiki_stats.publisher import Publisher
    from wiki_stats.transactions import approve_change
    from wiki_stats.models import Mode
    pkg,plan=prepare()
    approve_change(plan,'create-klstat')
    client=MemoryClient(snapshot(BARE))
    result=Publisher(backup_dir=tmp_path).publish(plan,client,mode=Mode.MANUAL,resolver=resolver())
    assert len(client.writes)==1 and client.writes[0][1]==plan.preview
    assert result['backup']


def test_section_creation_is_not_an_automatic_operation():
    from wiki_stats.publication_policy import LOW_RISK
    assert 'create_statistics_section' not in LOW_RISK
