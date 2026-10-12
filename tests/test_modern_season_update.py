from datetime import datetime,timezone
import pytest
from tests.test_sofascore import API,Wiki,Resolver,match
from tests.stage23_helpers import MINI,AUGSBURG,snapshot
from wiki_stats.sofascore_cycle import prepare_player
from wiki_stats.server_cli import parser
from wiki_stats.errors import UpdateError

NOW=datetime(2026,10,11,tzinfo=timezone.utc)
MAPPING={'api_id':1,'qid':'Q114406918','title':'Игрок','wiki_name':'Кальвин Браккельман','birth_date':'1999-08-22'}

def article(keeper=False):
    card=MINI.split('{{КлСтат|')[0].replace('| имя =','| дата рождения = 22.08.1999\n| имя =').replace('2026—{{н.в.}}','2019—{{н.в.}}')
    if keeper:card=card.replace('| номер =','| позиция = вратарь\n| номер =').replace('4 (1)','4 (-1)').replace('10 (1)','10 (-1)')
    oldgoal=-9 if keeper else 1
    return card+'{{КлСтат|Чемпионат}}\n{{КлСтат/Клуб|'+AUGSBURG+'|сезоны=2}}\n'+\
        '{{КлСтат/Лига|[[Чемпионат Германии по футболу|Бундеслига]]|сезоны=2}}\n'+\
        f'{{{{КлСтат/Сезон|2019/20|10|{oldgoal}}}}}<!-- старую строку сохранить -->\n'+\
        '{{КлСтат/Сезон|2020/21|1|0}}\n'+f'{{{{КлСтат/ИтогоЗаКлуб|Итого|11|{oldgoal}}}}}\n{{{{КлСтат/Итого|11|{oldgoal}}}}}'

class Seasons(API):
    def __init__(self,keeper=False,missing=None):self.calls=[];self.keeper=keeper;self.missing=missing
    def details(self,pid):
        result=super().details(pid)
        if self.keeper:result['position']='G'
        return result
    def seasons(self,pid):
        return {'player_id':pid,'statistics_types':{'35':{str(y):['overall','home','away'] for y in range(19,27)}},
            'tournament_seasons':[{'tournament':{'id':35},'seasons':[{'id':y,'year':f'{y}/{y+1}'} for y in range(19,27)]}]}
    def statistics(self,pid,tid,sid,part):
        self.calls.append(sid)
        assert sid>=20,'Old season must not be requested'
        if sid==self.missing:raise UpdateError('api_not_found','Missing season')
        result=super().statistics(pid,tid,sid,part)
        result['statistics']['goals_conceded']=3
        return result
    def match_statistics(self,*args):raise AssertionError('Club protocols must not be fetched')
    def matches(self,pid,max_pages=12):return {'complete':True,'matches':[match(day='2026-10-08')]}

def run(text,api):
    return prepare_player(snapshot(text),MAPPING,api,Wiki(),Resolver(),statistics_since=2020,now=NOW)

@pytest.mark.parametrize('keeper,expected',[(False,'45 (15)'),(True,'45 (-30)')])
def test_stale_card_and_table_update_from_season_aggregates_preserving_old_base_once(keeper,expected):
    source=Seasons(keeper);text=article(keeper)
    _,plan,report=run(text,source)
    assert sorted(source.calls)==list(range(20,27))
    assert report['statistics_since']==2020 and report['old_seasons_policy']=='preserve_existing'
    assert all(c.status in {'ready','already_applied'} for c in plan.changes)
    assert expected in plan.preview
    assert '{{КлСтат/Сезон|2019/20|10|'+('-9' if keeper else '1')+'}}<!-- старую строку сохранить -->' in plan.preview
    assert '{{КлСтат/Сезон|2026/27|5|'+('-3' if keeper else '2')+'}}' in plan.preview
    assert '| обновление данных о клубе = 08.10.2026' in plan.preview
    assert 'Неприкосновенный источник' in plan.preview and '<!-- статистика -->' in plan.preview
    _,replay,_=run(plan.preview,Seasons(keeper))
    assert not replay.diff

def test_missing_recent_season_preserves_whole_card_total_and_does_not_invent_zero():
    text=article();_,plan,report=run(text,Seasons(missing=22))
    assert '| 4 (1)<ref name="x"/>' in plan.preview
    assert '{{КлСтат/Сезон|2022/23|' not in plan.preview
    assert '{{КлСтат/Сезон|2026/27|5|2}}' in plan.preview
    assert any(i['code']=='career_incomplete' for i in report['review'])

def test_missing_pre_boundary_base_does_not_replace_career_with_recent_subtotal():
    text=article().replace('{{КлСтат/Сезон|2019/20|10|1}}<!-- старую строку сохранить -->\n','')
    text=text.replace('сезоны=2','сезоны=1')
    _,plan,_=run(text,Seasons())
    assert '| 4 (1)<ref name="x"/>' in plan.preview
    assert not any(c.operation.get('target',{}).get('structure')=='career' and c.operation['type']=='update_stats' for c in plan.changes)

def test_cli_uses_all_available_seasons_by_default():
    args=parser().parse_args(['--provider','sofascore','prepare-player','1','--title','T','--identity-title','I','--qid','Q1','--wiki-name','N'])
    assert args.statistics_since==0

def test_one_run_updates_keeper_card_and_klstat_and_adds_national_matches(monkeypatch):
    from tests.test_sofascore_national import NationalAPI
    from tests.stage23_helpers import U18
    from wiki_stats.sbmatches_editor import read_matches
    from wiki_stats.wikitext_parser import WikitextParser
    source=NationalAPI()
    for row in source.rows:
        row['home_team'].update(id=4709,name='Germany',code='GER',gender='M')
        row['away_team'].update(name='France',code='FRA',gender='M')
        row['player']['team_id']=4709
        row['season']={'id':26,'year':'2026'}
        row['tournament']={'id':851,'name':'International Friendly Games'}
        row['start_time']='2026-10-0'+str(row['id'])+'T12:00:00Z'
        row['link']='https://www.sofascore.com/football/match/test#id:'+str(row['id'])
    source.expected['statistics'][0]['goals']=0
    identity=Wiki.get_page_identity
    def page(self,title):
        if title=='Сборная Германии по футболу':return {'title':title,'qid':'Q9000'}
        if title=='Сборная Франции по футболу':return {'title':title,'qid':'Q9001'}
        return identity(self,title)
    monkeypatch.setattr(Wiki,'get_page_identity',page)
    monkeypatch.setattr(Wiki,'get_foreign_page_identity',lambda self,title,language:{'title':title,'qid':'Q9000' if 'Germany' in title else 'Q9001'},raising=False)
    class Combined(Seasons):
        def national_statistics(self,pid):return source.national_statistics(pid)
        def match_statistics(self,pid,mid):
            result=source.match_statistics(pid,mid);result['team']['id']=4709;return result
        def matches(self,pid,max_pages=12):return {'complete':True,'matches':super().matches(pid,max_pages)['matches']+source.rows}
    text=article(True).replace(U18,'[[Сборная Германии по футболу|Германия]]').replace('2016—2017','2020—{{н.в.}}')
    _,plan,report=run(text,Combined(True))
    assert all(c.status in {'ready','already_applied'} for c in plan.changes)
    assert '45 (-30)' in plan.preview and '|2 (-6)' in plan.preview
    templates=[t for t in WikitextParser(plan.preview).templates if t.name=='сбматчи']
    assert len(templates)==1 and len(read_matches(templates[0])[1])==2,report['review']
    assert report['national_scopes'][0]['numbers']=={'appearances':2,'goals':-6}
    _,replay,_=run(plan.preview,Combined(True))
    assert not replay.diff

@pytest.mark.parametrize('boundary',[True,2020.0,1899,2101,'2020'])
def test_invalid_boundary_is_rejected_before_source_access(boundary):
    with pytest.raises(UpdateError) as exc:prepare_player(snapshot(),{},None,None,None,statistics_since=boundary,now=NOW)
    assert exc.value.code=='season_boundary_invalid'
