# SPDX-License-Identifier: MIT
from copy import deepcopy
from types import SimpleNamespace
import pytest
from tests.test_new_statistics_competitions import AllCompetitions,prepare
from tests.test_statistics_section import evidence,resolver
from tests.stage23_helpers import MINI,TABLE_AUGSBURG,operation
from wiki_stats.competition_labels import category_label
from wiki_stats.competition_notes import column_notes,LINKS
from wiki_stats.statistics_section import render_section
from wiki_stats.wikitext_parser import WikitextParser
from wiki_stats.structural_editor import execute_operation
from wiki_stats.change_validator import arithmetic_warnings
from wiki_stats.errors import UpdateError


def test_three_column_notes_list_only_included_tournaments_and_replay():
    pkg,plan,report=prepare(AllCompetitions())
    header=next(t for t in WikitextParser(plan.preview).templates if t.name=='клстат')
    assert str(header.node).count('{{efn|')==3
    assert LINKS[217] in str(header.node) and LINKS[21] in str(header.node)
    assert LINKS[7] in str(header.node) and LINKS[799] in str(header.node)
    assert LINKS[679] not in str(header.node)
    assert plan.preview.count('{{комментарии}}')==1
    assert not arithmetic_warnings(plan.preview)
    from wiki_stats.change_planner import plan_article
    from tests.test_sofascore import Resolver
    from tests.stage23_helpers import snapshot
    repeated=plan_article(snapshot(plan.preview),pkg['articles'][0],'1.1',Resolver())
    assert not repeated.diff


def test_failed_category_does_not_claim_included_tournaments():
    api=AllCompetitions();api.missing_cup=True
    _,plan,_=prepare(api)
    header=next(t for t in WikitextParser(plan.preview).templates if t.name=='клстат')
    assert 'Кубки{{efn' not in str(header.node)
    assert 'Еврокубки{{efn' in str(header.node)


def test_existing_three_notes_and_hidden_comment_survive_numeric_update():
    notes=['Национальный кубок{{efn|[[Кубок Германии по футболу|Кубок Германии]]}}',
           'Еврокубки{{efn|[[Лига чемпионов УЕФА]], [[Лига Европы УЕФА]]}}',
           'Стыковые матчи{{efn|Матчи за место в лиге}}']
    header='{{КлСтат|Чемпионат|'+'|'.join(notes)+'}}'
    comment='<!-- При обновлении данных не забывайте, пожалуйста, о ячейках «Всего» и о дате. -->'
    text=MINI.replace('{{КлСтат|Чемпионат|Национальный кубок|Еврокубки|Стыковые матчи}}',comment+'\n'+header)
    op=operation('update','update_stats',entity={'name':'Аугсбург','wikitext':TABLE_AUGSBURG},
        target={'structure':'club_table'},season='2025/26',
        competition={'name':notes[0],'category':notes[0],'scope':'category'},
        expected={'appearances':0,'goals':0},new={'appearances':2,'goals':1})
    updated,_=execute_operation(text,'Кальвин Браккельман',op)
    assert header in updated and comment in updated
    assert not arithmetic_warnings(updated)
    assert category_label(WikitextParser(updated).club_table().categories[1])=='Национальный кубок'


@pytest.mark.parametrize('bad',['{{Непроверенный шаблон}}','<ref>Источник</ref>','[[Кубок]]\nНовая строка'])
def test_note_cannot_inject_templates_tags_or_new_lines(bad):
    data=evidence();payload={**data['create_section'],'clubs':data['clubs'],
                          'header_notes':{'Кубок':[bad]}}
    with pytest.raises(UpdateError):render_section(payload,resolver(),'2026-10-08')


def test_footnotes_are_emitted_for_full_career_table_as_well():
    data=evidence();payload={**data['create_section'],'full_career':True,'clubs':data['clubs'],
                          'header_notes':{'Кубок':[LINKS[217]]}}
    text=render_section(payload,resolver(),'2026-10-08')
    assert '{{КлСтат/Итого|' in text and text.count('{{комментарии}}')==1


def test_only_rows_in_the_output_contribute_notes_without_duplicates():
    clubs=[{'entity':{'wikidata_id':'Q1'},'rows':[{'season':'2026/27',
           'categories':{'Кубки':{'appearances':1,'goals':0}}}]}]
    rows=[{'resolved':SimpleNamespace(qid=qid),'season':season,'category':'Кубки','tournament_id':tid}
          for qid,season,tid in [('Q1','2026/27',217),('Q1','2026/27',217),('Q2','2026/27',19),('Q1','2020/21',328)]]
    assert column_notes(rows,clubs,['Кубки'])=={'Кубки':[LINKS[217]]}
