# SPDX-License-Identifier: MIT
import pytest
from wiki_stats.sbmatches_editor import match_date
from wiki_stats.errors import UpdateError
from tests.test_sbmatches import renderer, lupa


@pytest.mark.parametrize('day,valid',[
    ('29-02-1900',False),('29.02.2000',True),('2100-02-29',False),
    ('29-02-2096',True),('31.04.2026',False),('31-12-2026',True)])
def test_python_editor_and_lua_module_agree_on_calendar_edges(renderer,day,valid):
    args={'заголовок':'Матчи','дата1':day,'соперник1':'Франция',
          'счёт1':'0:0','статистика':'пропущенные голы','соревнование':'Кубок'}
    if valid:
        parsed=match_date(day)
        output=renderer(args)
        assert str(parsed.year) in output and '<td>—</td>' in output
    else:
        with pytest.raises(UpdateError):match_date(day)
        with pytest.raises(lupa.LuaError):renderer(args)


@pytest.mark.parametrize('metric',['голы','пропущенные голы'])
def test_mixed_date_formats_do_not_change_totals_or_sort_order(renderer,metric):
    args={'заголовок':'Матчи','статистика':metric,'соревнование':'Кубок',
          'матч1':'31-12-2025 ;; Франция ;; 0:0 ;;',
          'матч2':'01.01.2026 ;; Франция ;; 0:0 ;; 0',
          'матч3':'2026-02-01 ;; Франция ;; 0:0 ;; 0'}
    output=renderer(args)
    assert output.index('31 декабря 2025')<output.index('1 января 2026')<output.index('1 февраля 2026')
    assert output.count('<td>—</td>')==3
    assert '3 матча' in output and '3 ничьи' in output
