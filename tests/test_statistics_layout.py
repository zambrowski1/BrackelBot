from wiki_stats.statistics_layout import operation, execute
from wiki_stats.errors import UpdateError
from wiki_stats.change_validator import references
import pytest


@pytest.mark.parametrize('newline',['\n','\r\n'])
def test_existing_section_moves_as_a_whole_and_replay_does_nothing(newline):
    text='Начало\n== Достижения ==\nКубок\n== Статистика выступлений ==\n=== Клубная ===\nТаблица\n=== Сборная ===\nМатчи\n== Примечания ==\n<ref>Источник</ref>\n'.replace('\n',newline)
    op=operation(text,'Игрок','2026-10-12')
    result,status=execute(text,'Игрок',op)
    assert status=='ready'
    assert result.index('== Статистика выступлений ==') < result.index('== Достижения ==')
    assert '=== Сборная ==='+newline+'Матчи' in result
    assert references(result)==references(text)
    assert execute(result,'Игрок',op)==(result,'already_applied')
    with pytest.raises(UpdateError):execute(text+'Правка','Игрок',op)


def test_only_exact_old_bot_notice_is_removed():
    notice="''В таблице представлены только подтверждённые сезоны 2020—2027 годов; полный итог карьеры не заявляется.''\n"
    text='Авторский текст о статистике\n'+notice+'{{обновлено|2026-10-10}}\n'
    op=operation(text,'Игрок','2026-10-12')
    result,_=execute(text,'Игрок',op)
    assert result=='Авторский текст о статистике\n{{обновлено|2026-10-10}}\n'


def test_old_partial_bot_output_no_longer_claims_a_full_career_total():
    from tests.stage23_helpers import MINI
    from wiki_stats.change_validator import arithmetic_warnings
    notice="''В таблице представлены только подтверждённые сезоны 2020—2027 годов; полный итог карьеры не заявляется.''\n"
    text=MINI.replace('{{КлСтат|',notice+'{{КлСтат|',1)
    op=operation(text,'Кальвин Браккельман','2026-10-12')
    result,_=execute(text,'Кальвин Браккельман',op)
    assert notice not in result
    assert '{{КлСтат/Итого|' not in result
    assert not arithmetic_warnings(result)


@pytest.mark.parametrize('newline',['\n','\r\n'])
def test_standalone_national_statistics_moves_without_changing_table(newline):
    table='{| class="wikitable"\n! Матчи за сборную Чехии\n| 2018 || 1:0\n|}\n'.replace('\n',newline)
    text=('Введение\n== Достижения ==\nКубок\n== Статистика выступлений за сборную ==\n'.replace('\n',newline)
          +table+'== Примечания ==\n'.replace('\n',newline))
    op=operation(text,'Игрок','2026-10-12')
    result,_=execute(text,'Игрок',op)
    assert table in result
    assert result.index('== Статистика выступлений ==') < result.index('== Достижения ==')
    assert result.count('=== Сборная ===')==1
    assert execute(result,'Игрок',op)==(result,'already_applied')
