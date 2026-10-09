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
existingTitles={['Сборная Франции по футболу (до 18 лет)']=true,
    ['Сборная Ирландии по футболу']=true}
redirectTitles={['Сборная Ирландии по футболу (до 16 лет)']=true}
mw.title={new=function(name) return {exists=existingTitles[name] or false,
    isRedirect=redirectTitles[name] or false} end}
testFrame={expandTemplate=function(self,spec)
    if spec.title=='обновлено' then return '{{обновлено|'..spec.args[1]..'}}' end
    if spec.title=='цвета сборной/Германия' then return 'background:#fff;color:#000;' end
    if spec.title:sub(1,#'Флаг ')=='Флаг ' then return '{{'..spec.title..'|20px}}' end
    if spec.title=='нп5' then
        return '{{нп5|'..table.concat(spec.args,'|')..'}}'
    end
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
    assert 'max-width:100%;' in result and 'min-width' not in result and 'colspan="6"' in result
    assert '13 ноября 2016' in result and '17 мая 2017' in result
    for name in ('№','Дата','Соперник','Счёт','Голы','Соревнование'):
        assert f'<th scope="col">{name}</th>' in result
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


def test_compact_example_matches_verbose_example(renderer):
    template = next(t for t in mw.parse((ROOT/'Браккельман-кратко.wiki').read_text(encoding='utf-8')).filter_templates()
                    if str(t.name).strip()=='СбМатчи')
    args = {str(p.name).strip():str(p.value).strip() for p in template.params}
    assert renderer(args)==renderer(example())


def test_compact_with_nested_links_references_and_shootout(renderer):
    args={'заголовок':'Матчи','соревнование':'Кубок',
          'матч1':'2024-01-01 ;; {{Флаг Германии|20px}} [[Команда|Имя]] ;; 2:2 ;; 1',
          'источник1':'<ref>Источник</ref>','пенальти1':'4:3'}
    result=renderer(args)
    assert '[[Команда|Имя]]' in result and 'Кубок<ref>Источник</ref>' in result
    assert '2:2 (4:3 пен.)' in result and '0 побед, 1 ничья' in result


@pytest.mark.parametrize('row,extra',[
    ('2024-01-01 ;; Команда ;; 1:0',{}),
    ('2024-01-01 ;; Команда ;; 1:0 ;;',{}),
    ('2024-01-01 ;; Команда ;; 1:0 ;; 0 ;; Кубок ;; лишнее',{}),
    ('2024-01-01 ;; Команда ;; 1:0 ;; 0',{'дата1':'2024-01-01'}),
    ('2024-01-01 ;; Команда ;; 1:0 ;; 0',{'соревнование1':'Кубок'}),
])
def test_compact_malformed_or_mixed_input_fails(renderer,row,extra):
    with pytest.raises(lupa.LuaError,match='СбМатчи:'):
        renderer({'заголовок':'Матчи','соревнование':'Кубок','матч1':row,**extra})


def test_mixed_row_formats_and_competition_override(renderer):
    result=renderer({'заголовок':'Матчи','соревнование':'Общее',
        'матч1':'2024-01-01 ;; А ;; 1:0 ;; 0 ;; Другое',
        'дата2':'2024-01-02','соперник2':'Б','счёт2':'0:0','голы2':'0'})
    assert 'Другое</td>' in result and 'Общее</td>' in result
    assert '2 матча / 0 голов; 1 победа, 1 ничья' in result


def national_args(country='Ирландия',age='18'):
    return {'заголовок':'Матчи','возраст':age,'соревнование':'Товарищеский матч',
            'матч1':f'2016-11-13 ;; {country} ;; 3:2 ;; 0'}


def test_ireland_u18_keeps_correct_age_without_wrong_english_redirect(renderer):
    result=renderer(national_args())
    assert '{{Флаг Ирландии|20px}} [[Сборная Ирландии по футболу (до 18 лет)|Ирландия (до 18 лет)]]' in result
    assert 'under-19' not in result and '{{нп5' not in result


def test_existing_russian_youth_article_preferred(renderer):
    result=renderer(national_args('Франция'))
    assert '[[Сборная Франции по футболу (до 18 лет)|Франция (до 18 лет)]]' in result
    assert '{{нп5' not in result


def test_missing_russian_article_uses_verified_same_age_foreign_article(renderer):
    result=renderer(national_args('Австрия'))
    assert '{{нп5|Сборная Австрии по футболу (до 18 лет)|Австрия (до 18 лет)|en|Austria national under-18 football team}}' in result


def test_senior_default_and_explicit_overrides(renderer):
    result=renderer(national_args(age=''))
    assert '[[Сборная Ирландии по футболу|Ирландия]]' in result and '(до ' not in result
    manual='{{Флаг Ирландии|20px}} [[Особая статья|Ирландия]]'
    assert manual in renderer(national_args(manual))


@pytest.mark.parametrize('age',['0','14','24','18 лет','-1'])
def test_invalid_age_rejected(renderer,age):
    with pytest.raises(lupa.LuaError,match='возраст'): renderer(national_args(age=age))


def test_unknown_short_country_needs_explicit_link(renderer):
    with pytest.raises(lupa.LuaError,match='полную ссылку'): renderer(national_args('Ирландиия'))


def test_redirect_without_safe_foreign_article_not_followed(renderer):
    result=renderer(national_args(age='16'))
    assert '{{Флаг Ирландии|20px}} Ирландия (до 16 лет)' in result
    assert '[[Сборная Ирландии' not in result


def test_auto_example_keeps_match_totals_and_ages(renderer):
    template=next(t for t in mw.parse((ROOT/'Браккельман-авто.wiki').read_text(encoding='utf-8')).filter_templates()
                  if str(t.name).strip()=='СбМатчи')
    args={str(p.name).strip():str(p.value).strip() for p in template.params}
    result=renderer(args)
    assert 'Итого: 6 матчей / 0 голов; 5 побед, 1 ничья, 0 поражений' in result
    assert '{{Флаг Ирландии|20px}}' in result
    assert 'Ирландия (до 18 лет)' in result and 'Австрия (до 18 лет)' in result
    assert 'under-19' not in result


def test_repeated_country_uses_single_title_lookup_and_template_expansion():
    runtime=lupa.LuaRuntime(unpack_returned_tuples=True)
    runtime.execute(HOST)
    runtime.execute('''
    titleCalls=0; templateCalls=0
    local originalNew=mw.title.new
    mw.title.new=function(name) titleCalls=titleCalls+1 return originalNew(name) end
    local originalExpand=testFrame.expandTemplate
    testFrame.expandTemplate=function(self,spec)
        templateCalls=templateCalls+1 return originalExpand(self,spec)
    end
    ''')
    module=runtime.execute((ROOT/'Модуль.lua').read_text(encoding='utf-8'))
    args=national_args('Австрия')
    args['матч2']='2016-11-14 ;; Австрия ;; 0:0 ;; 0'
    result=module['_render'](runtime.table_from(args),runtime.globals().testFrame)
    assert '2 матча' in result
    assert runtime.globals().titleCalls==1
    assert runtime.globals().templateCalls==2  # One flag and one foreign link.
