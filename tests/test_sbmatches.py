# SPDX-License-Identifier: MIT
"""Run the production module in Lua 5.1 with a small Scribunto host stub.

The stub covers argument access and HTML construction, not MediaWiki parsing
or ResourceLoader's collapse control; those need an on-wiki sandbox preview.
"""
from pathlib import Path
import mwparserfromhell as mw
import pytest

lupa = pytest.importorskip('lupa.lua51', reason='Install requirements-lua-tests.txt for Lua module tests')
ROOT = Path(__file__).resolve().parents[1] / 'wikipedia' / 'СбМатчи'
HOST = r'''
local Node = {}
Node.__index = Node
local function escape(s)
    return tostring(s):gsub('&','&amp;'):gsub('"','&quot;'):gsub('<','&lt;'):gsub('>','&gt;')
end
function Node:attr(key,value) self.attrs[key]=value return self end
function Node:addClass(s)
    self.attrs.class=(self.attrs.class and self.attrs.class..' ' or '')..s return self
end
function Node:cssText(s) self.attrs.style=s return self end
function Node:wikitext(s) table.insert(self.children,s) return self end
function Node:tag(name)
    local child=setmetatable({name=name,attrs={},children={}},Node)
    table.insert(self.children,child) return child
end
function Node:__tostring()
    local result='<'..self.name
    for k,v in pairs(self.attrs) do result=result..' '..k..'="'..escape(v)..'"' end
    result=result..'>'
    for _,child in ipairs(self.children) do result=result..tostring(child) end
    return result..'</'..self.name..'>'
end
mw={text={trim=function(s) return s:match('^%s*(.-)%s*$') end},html={}}
mw.html.create=function(name) return setmetatable({name=name,attrs={},children={}},Node) end
testFrame={expandTemplate=function(self,spec)
    if spec.title=='обновлено' then return '{{обновлено|'..spec.args[1]..'}}' end
    if spec.title=='цвета сборной/Германия' then return 'background:#fff;color:#000;' end
    error('Unexpected template dependency: '..spec.title)
end}
'''


@pytest.fixture
def renderer():
    runtime = lupa.LuaRuntime(unpack_returned_tuples=True)
    runtime.execute(HOST)
    module = runtime.execute((ROOT/'Модуль.lua').read_text(encoding='utf-8'))
    return lambda args: module['_render'](runtime.table_from(args),runtime.globals().testFrame)


def example():
    template = next(t for t in mw.parse((ROOT/'Браккельман.wiki').read_text(encoding='utf-8')).filter_templates()
                    if str(t.name).strip()=='СбМатчи')
    return {str(p.name).strip():str(p.value).strip() for p in template.params}


def test_user_example_reproduces_columns_dates_collapse_and_totals(renderer):
    result=renderer(example())
    assert 'wikitable mw-collapsible mw-collapsed' in result
    assert 'min-width:600px;' in result and 'colspan="6"' in result
    assert '13 ноября 2016' in result and '17 мая 2017' in result
    assert '<th>№</th><th>Дата</th><th>Соперник</th><th>Счёт</th><th>Голы</th><th>Соревнование</th>' in result
    assert 'Итого: 6 матчей / 0 голов; 5 побед, 1 ничья, 0 поражений' in result
    assert result.count('<tr>')==8
    assert '{{обновлено|2026-10-04}}' in result
    assert 'Сборная Ирландии по футболу (до 18 лет)' in result


def test_shootout_is_displayed_but_result_stays_draw(renderer):
    args={'игрок':'Игрока','сборная':'сборную','дата1':'2026-01-01','соперник1':'Соперник',
          'счёт1':'2:2','голы1':'1','соревнование1':'Кубок','пенальти1':'4:3','источник1':'<ref>Источник</ref>',
          'свёрнуто':'нет','источники':'<ref name="all"/>'}
    result=renderer(args)
    assert '2:2 (4:3 пен.)' in result
    assert 'Итого: 1 матч / 1 гол; 0 побед, 1 ничья, 0 поражений' in result
    assert '<ref>Источник</ref>' in result and '<ref name="all"/>' in result
    assert 'mw-collapsed' not in result


@pytest.mark.parametrize('change',[
    {'дата1':'2017-02-29'}, {'голы1':'-1'}, {'голы1':'4'}, {'счёт1':'3:2 (4:3)'},
    {'дата2':''}, {'дата2':'2016-11-12'}, {'обновлено':'2016-01-01'},
    {'голы7':'0'}, {'счёт01':'3:2'}, {'пенальти1':'4:3'}, {'лишнее':'1'}, {'свёрнуто':'ошибка'}])
def test_bad_dates_missing_rows_and_inconsistent_scores_fail_visibly(renderer,change):
    with pytest.raises(lupa.LuaError,match='СбМатчи:'): renderer({**example(),**change})


def test_leap_date_and_multiple_goals(renderer):
    args={'заголовок':'Матчи игрока','дата1':'2024-02-29','соперник1':'Команда',
          'счёт1':'3:1','голы1':'2','соревнование1':'Товарищеский матч'}
    result=renderer(args)
    assert '29 февраля 2024' in result and '1 матч / 2 гола; 1 победа' in result
