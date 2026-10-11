from datetime import datetime,timezone
from tests.test_sofascore import API,Wiki,Resolver,match
from tests.stage23_helpers import MINI,snapshot
from wiki_stats.sofascore_cycle import prepare_player
from wiki_stats.errors import UpdateError


class AllCompetitions(API):
    missing_cup=False
    def seasons(self,pid):
        return {'player_id':pid,'tournament_seasons':[
            {'tournament':{'id':tid},'seasons':[{'id':26,'year':'26/27'}]}
            for tid in (35,217,21,7,799)]}
    def matches(self,*args,**kwargs):
        return {'complete':True,'matches':[match()]}
    def statistics(self,pid,tid,sid,part):
        if self.missing_cup and tid==217:
            raise UpdateError('source_cache_missing','Missing cup evidence')
        return super().statistics(pid,tid,sid,part)


def prepare(api):
    text=MINI[:MINI.index('{{КлСтат|')].replace('| имя =','| дата рождения = 22.08.1999\n| имя =')
    text+='== Достижения ==\nКубок\n== Примечания ==\n'
    mapping={'api_id':1,'qid':'Q114406918','title':'Игрок','wiki_name':'Кальвин Браккельман','birth_date':'1999-08-22'}
    return prepare_player(snapshot(text),mapping,api,Wiki(),Resolver(),statistics_since=2020,
        now=datetime(2026,10,12,tzinfo=timezone.utc))


def test_new_table_has_all_competitions_and_combines_domestic_cups_once():
    _,plan,_=prepare(AllCompetitions())
    assert '{{КлСтат|Чемпионат|Кубки|Еврокубки|Прочие}}' in plan.preview
    assert '{{КлСтат/Сезон|2026/27|5|2|10|4|5|2|5|2}}' in plan.preview
    assert plan.preview.index('== Статистика выступлений ==') < plan.preview.index('== Достижения ==')
    assert '{{КлСтат/Итого|' not in plan.preview


def test_missing_cup_does_not_become_zero_or_a_league_only_table():
    api=AllCompetitions();api.missing_cup=True
    _,plan,report=prepare(api)
    assert '{{КлСтат|' not in plan.preview
    assert any(i['code']=='new_row_needs_all_categories' and 'Кубки' in i['missing_categories'] for i in report['review'])
