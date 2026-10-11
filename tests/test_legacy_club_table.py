# SPDX-License-Identifier: MIT
import pytest
import mwparserfromhell as mw
from wiki_stats.legacy_club_table import migration_operation,migrate
from wiki_stats.wikitext_parser import WikitextParser
from wiki_stats.structural_editor import recompute_totals
from wiki_stats.errors import UpdateError
from wiki_stats.change_validator import references

RAW='''До<!-- до -->
{| class="wikitable"
|-
!rowspan="2"|Клуб
!rowspan="2"|Сезон
!colspan="3"|Лига
!colspan="2"|[[Кубок Германии по футболу|Кубок<br>Германии]]
!colspan="2"|Еврокубки
!colspan="2"|Прочие
!colspan="2"|Итого
|-
!Лига!!Игры!!Голы!!Игры!!Голы!!Игры!!Голы!!Игры!!Голы!!Игры!!Голы
|-
|rowspan="9"|[[РБ Лейпциг]]<!-- клуб -->
|[[Чемпионат Германии по футболу 2022/2023|2022/23]]
|Бундеслига
|33||4||6||1||8<ref>Лига чемпионов</ref>||0||1{{efn|name=s|Суперкубок}}||0||48||5
|-
!colspan="2"|Итого
!900!!90!!6!!1!!8!!0!!1!!0!!900!!90
|-
!colspan="3"|Всего за карьеру
!900!!90!!6!!1!!8!!0!!1!!0!!900!!90
|}
После<!-- после -->'''

def test_migration_preserves_cells_notes_and_article_and_replays():
 op=migration_operation(RAW,'Тест','2026-10-09')
 fixed,status=migrate(RAW,'Тест',op)
 table=WikitextParser(fixed).club_table()
 assert status=='ready' and len(table.seasons)==1
 assert table.categories==['Чемпионат','Кубок Германии','Еврокубки','Прочие']
 assert '900|90' in fixed and 'сезоны=1' in fixed
 assert references(fixed)==references(RAW)
 assert [str(c) for c in mw.parse(fixed).filter_comments()]==[str(c) for c in mw.parse(RAW).filter_comments()]
 assert '{{efn|name=s|Суперкубок}}' in fixed and fixed.startswith('До') and fixed.endswith('После<!-- после -->')
 assert migrate(fixed,'Тест',op)[1]=='already_applied'
 totals=recompute_totals(fixed)
 assert '{{КлСтат/Итого|33|4|6|1|8|0|1|0}}' in totals
 assert recompute_totals(totals)==totals
 from wiki_stats.change_validator import arithmetic_warnings
 assert any('итог сезона' in w for w in arithmetic_warnings(totals.replace('игры=48','игры=999')))

def test_migration_changed_old_value_and_unknown_headers_refuse():
 op=migration_operation(RAW,'Тест','2026-10-09')
 with pytest.raises(UpdateError,match='изменилась'):migrate(RAW.replace('33||4','34||4'),'Тест',op)
 with pytest.raises(UpdateError,match='Заголовок'):migration_operation(RAW.replace('Прочие','Неизвестный турнир'),'Тест','2026-10-09')

def test_efn_numbers_and_documented_absence_marker():
 from wiki_stats.wikitext_parser import ValueRef
 assert ValueRef(mw.parse('8{{efn|name=x|Примечание}}'),0).numeric()[0].value==8
 assert ValueRef(mw.parse('-'),0).numeric()[0].value==0
 with pytest.raises(UpdateError):ValueRef(mw.parse('8{{неизвестный|x}}'),0).numeric()
