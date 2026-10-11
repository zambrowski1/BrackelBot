# SPDX-License-Identifier: MIT
from copy import deepcopy
from pathlib import Path
import gzip,json
import pytest
from tests.test_dfb_club_profile import source,NOW
from tests.test_dfb_tim_youth import MAPPING
from tests.stage23_helpers import MINI,TABLE_AUGSBURG,snapshot
from wiki_stats.dfb_club_profile import parse_closed_clubs
from wiki_stats.dfb_club_cycle import club_secondary_scopes
from wiki_stats.dfb_reserve_identity import freiburg_reserve_identity
from wiki_stats.dfb_reserve_cup import reserve_cup_zero
from wiki_stats.wikitext_parser import WikitextParser
from wiki_stats.errors import UpdateError

REF='{{Флаг Германии|20px}} «[[Фрайбург II]]»'


class Wiki:
    def get_page_identity(self,title):
        return {'title':title,'qid':'Q20631356' if title=='Фрайбург II' else 'Q106394'}
    def get_foreign_page_identity(self,title,lang):
        return {'title':title,'qid':'Q20631356' if title=='SC Freiburg II' else 'Q106394'}


def inputs(season='2015/16'):
    bundle=source()
    s=next(s for s in parse_closed_clubs(MAPPING,bundle,NOW)
           if s['team_slug']=='sc-freiburg-ii' and s['season']==season)
    return s,bundle


@pytest.mark.parametrize('season',['2015/16','2018/19','2019/20'])
def test_real_parent_cup_entries_prove_three_reserve_seasons_without_counting_penalties(season):
    s,b=inputs(season);identity=freiburg_reserve_identity(s,b,Wiki())
    value=reserve_cup_zero(s,identity,b,NOW)
    assert identity['wikidata_id']=='Q20631356' and identity['parent_wikidata_id']=='Q106394'
    assert value['numbers']=={'appearances':0,'goals':0} and value['matches']==[]
    assert len(value['parent_entry_match_urls'])==2
    assert value['statistics_as_of']==f'{int(season[:4])+1}-06-30'
    assert value['category']=='Национальный кубок'
    assert value['competition_slug']=='dfb-pokal'


@pytest.mark.parametrize('fault',['parent_id','bool_id','wrong_slug','wrong_name','missing','duplicate',
    'women','youth','wrong_parent','wrong_team','backlink','foreign_backlink','ru_reserve_redirect',
    'en_reserve_redirect','parent_qid'])
def test_parent_women_youth_and_unproven_reserve_identity_cannot_certify_cup(fault):
    s,b=inputs();wiki=Wiki();p=b['freiburg_reserve_identity_profiles']
    if fault=='parent_id':s['source_team_id']=1640
    elif fault=='bool_id':s['source_team_id']=True
    elif fault=='wrong_slug':s['team_slug']='sc-freiburg'
    elif fault=='wrong_name':s['team']='SC Freiburg'
    elif fault=='missing':p.pop()
    elif fault=='duplicate':p[1]=deepcopy(p[0])
    elif fault=='women':p[0]['html']=p[0]['html'].replace('SC Freiburg II (Männer)','SC Freiburg II (Frauen)')
    elif fault=='youth':p[0]['html']=p[0]['html'].replace('SC Freiburg II (Männer)','SC Freiburg U 19 (Junioren)')
    elif fault=='wrong_parent':p[0]['html']=p[0]['html'].replace('SC Freiburg','Energie Cottbus')
    elif fault=='wrong_team':p[1]['html']=p[1]['html'].replace('SC Freiburg II','SC Freiburg')
    elif fault=='backlink':p[1]['html']=p[1]['html'].replace('/clubs/sc-freiburg-e-v','/clubs/fc-energie-cottbus-e-v')
    elif fault=='foreign_backlink':p[1]['html']=p[1]['html'].replace('/clubs/sc-freiburg-e-v','https://example.org/clubs/sc-freiburg-e-v')
    elif fault=='ru_reserve_redirect':wiki.get_page_identity=lambda title:{'title':'Фрайбург (футбольный клуб)','qid':'Q106394'}
    elif fault=='en_reserve_redirect':wiki.get_foreign_page_identity=lambda title,lang:{'title':'SC Freiburg','qid':'Q106394'}
    else:wiki.get_page_identity=lambda title:{'title':title,'qid':'Q20631356'}
    with pytest.raises(UpdateError):freiburg_reserve_identity(s,b,wiki)


@pytest.mark.parametrize('fault',['empty_200','foreign_source','wrong_parent','wrong_season','duplicate',
    'unplayed','unknown_result','no_entry','future_date','wrong_competition','wrong_rule'])
def test_empty_200_and_foreign_or_unplayed_entries_do_not_become_zero(fault):
    s,b=inputs();identity=freiburg_reserve_identity(s,b,Wiki())
    proofs=b['freiburg_reserve_cup_eligibility'];cup=proofs[0]['parent_cup']
    if fault=='empty_200':cup['html']=''
    elif fault=='foreign_source':cup['url']=cup['url'].replace('datencenter.dfb.de','example.org')
    elif fault=='wrong_parent':cup['html']=cup['html'].replace('SC Freiburg','Energie Cottbus')
    elif fault=='wrong_season':cup['html']=cup['html'].replace('2015/2016','2014/2015')
    elif fault=='duplicate':proofs.append(deepcopy(proofs[0]))
    elif fault=='unplayed':cup['html']=cup['html'].replace('0 : 5','- : -').replace('0 : 3','- : -')
    elif fault=='unknown_result':cup['html']=cup['html'].replace('0 : 5','0 : 5 abgebrochen')
    elif fault=='no_entry':cup['html']=cup['html'].replace('c-MatchTable-row','ignored-row')
    elif fault=='future_date':cup['html']=cup['html'].replace('09.08.2015','09.08.2016')
    elif fault=='wrong_competition':cup['url']=cup['url'].replace('dfb-pokal','dfb-pokal-der-frauen')
    else:proofs[0]['rule']['pages']=[6,7]
    with pytest.raises(UpdateError):reserve_cup_zero(s,identity,b,NOW)


def test_binding_keeps_reserve_qid_and_other_unproven_columns_separate():
    s,b=inputs()
    class API:
        def club_secondary_source(self,pid):return b
    text=MINI.replace(TABLE_AUGSBURG,REF).replace('2025/26','2015/16')
    values,_,issues,scopes=club_secondary_scopes(snapshot(text),MAPPING,API(),Wiki(),WikitextParser(text).club_table(),NOW)
    assert values[('Q20631356','2015/16','Чемпионат')]['numbers']=={'appearances':8,'goals':2}
    assert values[('Q20631356','2015/16','Национальный кубок')]['numbers']=={'appearances':0,'goals':0}
    assert ('Q106394','2015/16','Национальный кубок') not in values
    assert ('Q20631356','2015/16','Еврокубки') not in values
    assert ('Q20631356','2015/16','Стыковые матчи') not in values


def test_missing_reserve_cup_source_keeps_verified_league_and_unknown_cup():
    s,b=inputs();b.pop('freiburg_reserve_cup_eligibility')
    assert reserve_cup_zero(s,freiburg_reserve_identity(s,b,Wiki()),b,NOW) is None


def test_full_pipeline_uses_reserve_qid_and_preserves_uncovered_european_goals(monkeypatch):
    from datetime import datetime,timezone
    from tests.test_sofascore import API,Wiki as PlayerWiki,Resolver
    from wiki_stats.sofascore_cycle import prepare_player
    s,b=inputs();identity=freiburg_reserve_identity(s,b,Wiki());cup=reserve_cup_zero(s,identity,b,NOW)
    monkeypatch.setattr('wiki_stats.dfb_club_cycle.club_secondary_scopes',lambda *a,**k:(
        {('Q20631356','2015/16','Чемпионат'):s,('Q20631356','2015/16','Национальный кубок'):cup},
        {'Q20631356':{'name':'Фрайбург II','wikidata_id':'Q20631356','wikitext':REF}},[],[s,cup]))
    class CombinedWiki(PlayerWiki):
        def get_page_identity(self,title):
            if title=='Фрайбург II':return {'title':title,'qid':'Q20631356'}
            return super().get_page_identity(title)
    text=MINI.replace(TABLE_AUGSBURG,REF).replace('2025/26','2015/16')
    text=text.replace('|4|1|0|0|0|0|0|0}}','|4|1|9|3|2|1|0|0}}')
    text=text.replace('| имя =','| дата рождения = 22.08.1999\n| имя =')
    _,plan,_=prepare_player(snapshot(text),{'api_id':1,'qid':'Q114406918','title':'Игрок',
        'wiki_name':'Кальвин Браккельман','birth_date':'1999-08-22'},API(),CombinedWiki(),Resolver(),
        now=datetime(2026,10,9,tzinfo=timezone.utc))
    changes=[c for c in plan.changes if c.operation.get('season')=='2015/16']
    assert len(changes)==2 and all(c.status=='ready' for c in changes)
    assert '{{КлСтат/Сезон|2015/16|8|2|0|0|2|1|0|0}}' in plan.preview
    assert all(c.operation['entity']['wikitext']==REF for c in changes)
    assert 'Неприкосновенный источник' in plan.preview


def test_three_season_binding_keeps_each_scope_and_checks_parent_once():
    _,b=inputs()
    class API:
        def club_secondary_source(self,pid):return b
    class CountingWiki(Wiki):
        def __init__(self):self.parents=[]
        def get_page_identity(self,title):
            if title=='Фрайбург (футбольный клуб)':self.parents.append(('ru',title))
            return super().get_page_identity(title)
        def get_foreign_page_identity(self,title,lang):
            if title=='SC Freiburg':self.parents.append(('en',title))
            return super().get_foreign_page_identity(title,lang)
    text=MINI.replace(TABLE_AUGSBURG,REF).replace('сезоны=1','сезоны=3')
    text=text.replace('{{КлСтат/Сезон|2025/26|4|1|0|0|0|0|0|0}}',
        '\n'.join('{{КлСтат/Сезон|'+season+'|4|1|0|0|0|0|0|0}}'
                  for season in ['2015/16','2018/19','2019/20']))
    wiki=CountingWiki()
    values,_,issues,scopes=club_secondary_scopes(snapshot(text),MAPPING,API(),wiki,WikitextParser(text).club_table(),NOW)
    for season,numbers in [('2015/16',{'appearances':8,'goals':2}),
                           ('2018/19',{'appearances':2,'goals':1}),
                           ('2019/20',{'appearances':1,'goals':0})]:
        assert values[('Q20631356',season,'Чемпионат')]['numbers']==numbers
        assert values[('Q20631356',season,'Национальный кубок')]['numbers']=={'appearances':0,'goals':0}
    assert wiki.parents.count(('ru','Фрайбург (футбольный клуб)'))==1
    # The existing league identity lookup and the independent parent check are separate.
    assert wiki.parents.count(('en','SC Freiburg'))==2
