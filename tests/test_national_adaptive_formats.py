"""Contracts distilled from the fifty-article survey (see docs/FIFTY_ARTICLE_FORMATS.md)."""
import pytest
from wiki_stats.national_table_migration import operation,migrate
from wiki_stats.national_table_formats import classify
from wiki_stats.sbmatches_editor import read_matches
from wiki_stats.wikitext_parser import WikitextParser
from wiki_stats.change_validator import references
from wiki_stats.errors import UpdateError
import mwparserfromhell as mw

BOX='{{Футболист|имя=Патрик Шик|национальная сборная=[[Сборная Чехии по футболу|Чехия]]}}\n'
TABLE='''{| class="wikitable mw-collapsible mw-collapsed"
|+ Матчи Патрика Шика за сборную Чехии
|-
! № !! Дата !! Соперник !! {{comment|Счёт|С точки зрения Чехии}} !! Голы !! Турнир !! Стадион !! Прим.
|-
| 1 || 2 января 2025 || {{футбол|Франция}} || 2:2 (4:3 пен.) || 1 || Кубок || [[Прага]] || Вышел на замену<ref>Старый источник</ref>
|-
| 2 || 1 января 2025 || {{футбол|Германия}} || 1:0 || 0 || Кубок || [[Прага]] ||
|}
'''


def test_caption_tooltips_extra_columns_and_source_order_are_lossless():
    text=BOX+TABLE+'\n== Примечания ==\n{{примечания}}'
    op=operation(text,'Патрик Шик','2026-10-12');after,status=migrate(text,'Патрик Шик',op)
    t=next(t for t in WikitextParser(after).templates if t.name=='сбматчи')
    values,rows=read_matches(t)
    assert status=='ready' and references(after)==references(text)
    assert values['порядок'].text.strip()=='исходный'
    assert rows[0]['место']=='[[Прага]]' and rows[0]['пенальти']=='4:3'
    assert rows[0]['примечание']=='Вышел на замену'
    assert after.endswith('\n== Примечания ==\n{{примечания}}')
    assert migrate(after,'Патрик Шик',op)==(after,'already_applied')


def test_annual_summary_remains_beside_migrated_match_list():
    annual='{|\n|-\n! Год !! Игры !! Голы\n|-\n| 2025 || 2 || 1\n|}\n'
    text=BOX+annual+TABLE
    after,_=migrate(text,'Патрик Шик',operation(text,'Патрик Шик','2026-10-12'))
    assert annual in after


def test_goals_catalogue_is_not_an_appearance_list():
    table=mw.parse(TABLE.replace('Матчи Патрика Шика','Голы Патрика Шика')).filter_tags()[0]
    assert classify(table)=='goals_list'
    assert operation(BOX+str(table),'Патрик Шик','2026-10-12') is None


def test_goal_minutes_are_not_silently_replaced_with_zero():
    with pytest.raises(UpdateError,match='Минуты голов'):
        operation(BOX+TABLE.replace('|| 1 || Кубок','|| {{гол|44}} || Кубок'),'Патрик Шик','2026-10-12')


def test_recognized_summary_is_checked_and_its_reference_preserved():
    text=BOX+TABLE+"'''Итого: 2 матча / 1 гол; 1 победа, 1 ничья, 0 поражений'''<ref>Итоги</ref>."
    after,_=migrate(text,'Патрик Шик',operation(text,'Патрик Шик','2026-10-12'))
    assert references(text)==references(after) and '|источники=<ref>Итоги</ref>' in after
    with pytest.raises(UpdateError):operation(text.replace('2 матча','3 матча'),'Патрик Шик','2026-10-12')


def test_extra_time_link_and_penalty_prefix_are_preserved():
    from wiki_stats.national_table_formats import score_and_penalties
    assert score_and_penalties('0:0 ([[Послематчевые пенальти|п.]] 5:6)')==('0:0','5:6')
    text=BOX+TABLE.replace('2:2 (4:3 пен.)','2:2 ([[Дополнительное время|доп. вр.]])')
    after,_=migrate(text,'Патрик Шик',operation(text,'Патрик Шик','2026-10-12'))
    assert '[[Дополнительное время|доп. вр.]]' in after


def test_selecting_one_youth_age_keeps_the_other_table_untouched():
    box='{{Футболист|имя=Патрик Шик|национальная сборная=[[Сборная Чехии по футболу (до 18 лет)|Чехия U18]], [[Сборная Чехии по футболу (до 19 лет)|Чехия U19]]}}\n'
    younger=TABLE.replace('за сборную Чехии','за сборную Чехии до 18 лет')
    older=TABLE.replace('за сборную Чехии','за сборную Чехии до 19 лет')
    text=box+younger+older
    op=operation(text,'Патрик Шик','2026-10-12',18)
    after,status=migrate(text,'Патрик Шик',op)
    assert status=='ready' and older in after and younger not in after
    assert '|возраст=18' in after and after.count('{{СбМатчи')==1
    assert migrate(after,'Патрик Шик',op)==(after,'already_applied')
