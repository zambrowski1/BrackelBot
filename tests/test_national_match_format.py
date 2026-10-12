# SPDX-License-Identifier: MIT
from copy import deepcopy
import json
from pathlib import Path
import pytest
from wiki_stats.national_match_format import competition_label,format_scope
from wiki_stats.national_team_identity import NationalTeamIdentity
from wiki_stats.errors import UpdateError


def row(tid,name,year):return {'tournament':{'id':tid,'name':name},'season':{'year':year}}


@pytest.mark.parametrize('tid,name,year,label',[
    (1,'EURO','2021','Финальные матчи ЧЕ-2020'),
    (27,'EURO, Qualification','2021','Отборочные матчи ЧЕ-2020'),
    (10783,'UEFA Nations League','24/25','Лига наций УЕФА 2024/2025'),
    (11,'World Cup Qual. UEFA','2026','Отборочные матчи ЧМ-2026'),
    (16,'FIFA World Cup','2022','Финальные матчи ЧМ-2022'),
    (851,'International Friendly Games','2026','Товарищеский матч')])
def test_verified_competition_names_and_postponed_euro(tid,name,year,label):
    assert competition_label(row(tid,name,year))==label


@pytest.mark.parametrize('bad',[row(1,'EURO','2022'),row(11,'World Cup Qual. UEFA','2025'),
    row(10783,'UEFA Nations League','24/26'),row(10783,'UEFA Nations League','2016/2017'),
    row(1,'Other tournament','2024'),row(999,'EURO','2024')])
def test_unknown_or_conflicting_tournaments_are_not_silently_named(bad):
    with pytest.raises(UpdateError):competition_label(bad)


class Wiki:
    def __init__(self):self.calls=[]
    def get_page_identity(self,title):self.calls.append(title);return {'title':title,'qid':'Q1'}
    def get_foreign_page_identity(self,title,language):self.calls.append(title);return {'title':title,'qid':'Q1'}


def team():return {'id':4709,'name':'Hungary','code':'HUN','gender':'M','is_national':True,'sport':{'slug':'football'}}


def test_team_identity_uses_two_wikipedia_languages_and_caches_verified_country():
    wiki=Wiki();resolver=NationalTeamIdentity(wiki)
    assert resolver.resolve(team())['wikitext']=='Венгрия'
    assert resolver.resolve(team())['page_title']=='Сборная Венгрии по футболу'
    assert len(wiki.calls)==2


@pytest.mark.parametrize('problem',['youth','women','code','different_qid'])
def test_country_aliases_do_not_turn_youth_women_or_another_country_into_the_senior_team(problem):
    source=team();wiki=Wiki()
    if problem=='youth':source['name']='Hungary U21'
    elif problem=='women':source['gender']='F'
    elif problem=='code':source['code']='GER'
    else:wiki.get_foreign_page_identity=lambda *args:{'title':args[0],'qid':'Q2'}
    with pytest.raises(UpdateError):NationalTeamIdentity(wiki).resolve(source)


def test_actual_full_orban_scope_formats_72_distinct_games_with_own_score_orientation():
    import gzip,hashlib
    from datetime import datetime,timezone
    from wiki_stats.sofascore_client import _SofascoreAccount
    from wiki_stats.sofascore_national import collect_national_scope
    data=json.loads(gzip.decompress((Path(__file__).parent/'source-fixtures/orban-national-source.json.gz').read_bytes()))
    lookup={r['cache_key']:r['data'] for r in data['responses']}
    class Replay(_SofascoreAccount):
        def __init__(self):pass
        def national_statistics(self,pid):return data['national']
        def request(self,path,params,**kwargs):
            key=hashlib.sha256(json.dumps([path,params],sort_keys=True).encode()).hexdigest()
            if key not in lookup:raise UpdateError('source_cache_missing','Missing personal response; roster/events may still prove the game')
            return lookup[key]
    scope=collect_national_scope(136924,4709,data['history'],Replay(),{'remaining':500},datetime(2026,10,11,tzinfo=timezone.utc))
    rows,sources=format_scope(scope,NationalTeamIdentity(Wiki()).resolve)
    assert len(rows)==len(sources)==72 and sum(int(r['голы']) for r in rows)==6
    san_marino=next(r for r in rows if r['дата']=='28-03-2021')
    assert san_marino['соперник']=='Сан-Марино' and san_marino['счёт']=='3:0'
    assert san_marino['голы']=='0'
    assert any(r['соревнование']=='Финальные матчи ЧЕ-2020' for r in rows)


def test_sofascore_nigeria_code_does_not_confuse_it_with_niger():
    source={**team(),'id':4785,'name':'Nigeria','code':'NIG'}
    assert NationalTeamIdentity(Wiki()).resolve(source)['name']=='Нигерия'
    source['name']='Niger'
    with pytest.raises(UpdateError):NationalTeamIdentity(Wiki()).resolve(source)
