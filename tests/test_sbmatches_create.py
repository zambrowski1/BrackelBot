from copy import deepcopy
import pytest
from tests.stage23_helpers import MINI
from wiki_stats.sbmatches_create import create
from wiki_stats.errors import UpdateError
from wiki_stats.change_validator import references
from wiki_stats.wikitext_parser import WikitextParser
from wiki_stats.sbmatches_editor import read_matches

PLAYER='Кальвин Браккельман'
def operation():
    return {'id':'new-sb','type':'create_sbmatches','as_of':'2026-10-02',
        'entity':{'name':'Германия','kind':'national_team','age':None,'wikitext':'[[Сборная Германии по футболу|Германия]]'},
        'target':{'structure':'sbmatches','age':None},
        'sources':[{'url':'https://www.sofascore.com/football/match/test#id:1'}],
        'payload':{'complete':True,'metric':'пропущенные голы','label':'Матчи за сборную Германии',
            'totals':{'appearances':1,'goals':0},'matches':[{'дата':'02-10-2026','соперник':'Франция',
                'счёт':'1:0','пропущенные голы':'0','соревнование':'Товарищеский матч'}]}}

@pytest.mark.parametrize('national_section',[False,True])
def test_full_table_is_inserted_before_notes_without_changing_references_or_categories(national_section):
    text=MINI+('\n== Сборная ==\nСуществующий текст.\n' if national_section else '\n')+'== Примечания ==\n<references/>\n[[Категория:Футболисты Германии]]'
    result,status=create(text,PLAYER,operation())
    assert status=='ready' and references(result)==references(text)
    assert result.index('{{СбМатчи')<result.index('== Примечания ==')
    assert result.endswith('[[Категория:Футболисты Германии]]')
    assert result.count('=== Сборная ===')==(0 if national_section else 1)
    assert '|статистика=пропущенные голы' in result
    template=next(t for t in WikitextParser(result).templates if t.name=='сбматчи')
    assert len(read_matches(template)[1])==1
    assert create(result,PLAYER,operation())==(result,'already_applied')

@pytest.mark.parametrize('fault',['partial','wrong_metric','count','wrong_goals','future_match','duplicate_match',
    'missing_source','bad_source','invalid_markup','wrong_concessions'])
def test_incomplete_or_inconsistent_new_table_is_refused(fault):
    op=operation();p=op['payload']
    if fault=='partial':p['complete']=False
    elif fault=='wrong_metric':p['metric']='minutes'
    elif fault=='count':p['totals']['appearances']=2
    elif fault=='wrong_goals':p['totals']['goals']=-1
    elif fault=='future_match':p['matches'][0]['дата']='03-10-2026'
    elif fault=='duplicate_match':p['matches'].append(deepcopy(p['matches'][0]));op['sources']*=2;p['totals']['appearances']=2
    elif fault=='missing_source':op['sources']=[]
    elif fault=='bad_source':op['sources'][0]['url']='https://example.com/|{{x}}'
    elif fault=='invalid_markup':p['matches'][0]['соперник']='Франция\n{{x}}'
    else:p['matches'][0]['пропущенные голы']='1'
    with pytest.raises(UpdateError):create(MINI,PLAYER,op)

def test_existing_different_table_is_never_overwritten_by_creation():
    result,_=create(MINI,PLAYER,operation())
    result=result.replace('|заголовок=Матчи за сборную Германии','|заголовок=Другой заголовок')
    with pytest.raises(UpdateError) as exc:create(result,PLAYER,operation())
    assert exc.value.code=='old_value_mismatch'


@pytest.mark.parametrize('existing',[
    '{{НеизвестнаяСтатистика|сборная=Германия|матчи=9}}',
    '{{МатчиСборной|игры=9}}',
    '{| class="wikitable"\n! Сборная !! Игры !! Голы\n| Германия || 9 || 0\n|}',
    '{{Автонумерация|Название=Матчи за сборную Германии}}',
    '=== Статистика сборной ===\n{{Неизвестный шаблон}}',
    '=== Сборная ===\n{| class="wikitable"\n! Дата !! Соперник !! Голы\n| 2018 || Чехия || 0\n|}',
    '=== Сборная ===\n{{Неизвестный формат|1=2018|2=Чехия}}',
])
def test_suspected_national_statistics_blocks_creation(existing):
    with pytest.raises(UpdateError) as exc:
        create(MINI+'\n'+existing,PLAYER,operation())
    assert exc.value.code=='possible_national_statistics'


def test_nationals_are_inside_statistics_before_achievements():
    text=MINI+'\n== Статистика выступлений ==\n=== Клубная статистика ===\nТекст\n== Достижения ==\nКубок\n== Примечания ==\n'
    result,_=create(text,PLAYER,operation())
    assert result.count('== Статистика выступлений ==')==1
    assert result.index('=== Сборная ===') < result.index('== Достижения ==')
