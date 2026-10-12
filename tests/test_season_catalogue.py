from wiki_stats.season_catalogue import lookup,parse_season_title
import pytest

CAT={'aliases':{'Лига А':['Q1'],'Вторая лига А':['Q2'],'Женская лига А':['Q3']},'entries':[
 {'title':'Лига А 2020/2021','season':'2020/21','league_keys':['Q1']},
 {'title':'Вторая лига А 2020/2021','season':'2020/21','league_keys':['Q2']},
 {'title':'Женская лига А 2020/2021','season':'2020/21','league_keys':['Q3']},
 {'title':'Лига А 2020','season':'2020','league_keys':['Q1']},
]}

def test_exact_division_gender_and_season_are_not_mixed():
 assert lookup('Лига А','2020/21',CAT)==['Лига А 2020/2021']
 assert lookup('Вторая лига А','2020/21',CAT)==['Вторая лига А 2020/2021']
 assert lookup('Женская лига А','2020/21',CAT)==['Женская лига А 2020/2021']
 assert lookup('Лига А','2020',CAT)==['Лига А 2020']

def test_missing_season_and_unknown_alias_do_not_guess():
 assert lookup('Лига А','2021/22',CAT)==[]
 assert lookup('Лига Б','2020/21',CAT)==[]

def test_aliases_share_only_explicit_verified_league_keys():
 cat={**CAT,'aliases':{**CAT['aliases'],'Другое название':['Q1']}}
 assert lookup('Другое_название','2020/21',cat)==['Лига А 2020/2021']

def test_two_stages_with_the_same_parent_and_year_are_not_selected_arbitrarily():
 cat={**CAT,'entries':CAT['entries']+[{'title':'Апертурa 2020','season':'2020','league_keys':['Q1']}]}
 assert lookup('Лига А','2020',cat)==[]

@pytest.mark.parametrize('title,key,hint',[
 ('MLS в сезоне 2010','2010','MLS'),
 ('Вторая Бундеслига 2020/2021','2020/21','Вторая Бундеслига'),
 ('Лига 1 2020/21','2020/21','Лига 1'),
 ('Чемпионат страны 2020/2021 (женщины)','2020/21','Чемпионат страны (женщины)'),
 ('Суперлига (Индия) 2020/2021','2020/21','Суперлига (Индия)'),
 ('Бундеслига (2020/2021)','2020/21','Бундеслига'),
])
def test_worldwide_title_styles_keep_competition_and_season(title,key,hint):
 assert parse_season_title(title)==(key,hint)

@pytest.mark.parametrize('title',['Лига 2009/2010','Лига 2027/2028','Лига 2020/2022','Лига 2020 и 2021'])
def test_outside_range_or_ambiguous_season_is_not_registered(title):
 assert parse_season_title(title,end=2026) is None

@pytest.mark.parametrize('league,season,title,display',[
 ('Чемпионшип Английской футбольной лиги','2010/11','Чемпионшип Футбольной лиги Англии 2010/2011','2010/2011'),
 ('Второй дивизион Джей-лиги','2020','Второй дивизион Джей-лиги 2020','2020'),
 ('Чемпионат Испании по футболу среди женщин','2024/25','Чемпионат Испании по футболу среди женщин 2024/2025','2024/2025'),
])
def test_shipped_worldwide_catalogue_links_real_title_styles_and_replays(league,season,title,display):
 from types import SimpleNamespace
 from tests.stage23_helpers import MINI,LEAGUE
 from wiki_stats.season_links import prepare,execute
 from wiki_stats.errors import UpdateError

 class SavedPage:
  def get_page_identity(self,requested):
   if requested!=title:raise UpdateError('entity_not_found','Missing test page')
   return {'title':title,'qid':None}

 text=MINI.replace(LEAGUE,'[['+league+']]').replace('2025/26',season)
 wiki=SavedPage();op,issues=prepare(text,'Кальвин Браккельман','2026-10-12',wiki)
 assert op and not issues
 after,status=execute(text,'Кальвин Браккельман',op,SimpleNamespace(client=wiki))
 assert status=='ready'
 assert after==text.replace('{{КлСтат/Сезон|'+season+'|','{{КлСтат/Сезон|[['+title+'|'+display+']]|')
 assert prepare(after,'Кальвин Браккельман','2026-10-12',wiki)==(None,[])
