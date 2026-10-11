# SPDX-License-Identifier: MIT
from copy import deepcopy
from types import SimpleNamespace
import pytest
from tests.test_dfb_club_profile import source, NOW
from tests.test_dfb_tim_youth import MAPPING
from tests.stage23_helpers import MINI, snapshot, TABLE_AUGSBURG
from wiki_stats.dfb_club_profile import parse_closed_clubs
from wiki_stats.dfb_club_cycle import club_secondary_scopes
from wiki_stats.dfb_reserve_identity import energie_reserve_identity, reserve_reference, PARENT, TEAM
from wiki_stats.errors import UpdateError
from wiki_stats.wikitext_parser import WikitextParser

REF='{{Флаг Германии|20px}} «{{Нп5|Энерги II|Энерги II|en|FC Energie Cottbus II}}»'


class Wiki:
    def get_page_identity(self,title):return {'title':title,'qid':'Q107818'}
    def get_foreign_page_identity(self,title,lang):
        return {'title':'FC Energie Cottbus' if title=='FC Energie Cottbus II' else title,'qid':'Q107818'}


def scope():
    return next(s for s in parse_closed_clubs(MAPPING,source(),NOW) if s['team_slug']=='energie-cottbus-ii')


def test_real_federation_team_identity_has_no_fabricated_reserve_wikidata_id():
    identity=energie_reserve_identity(scope(),source(),Wiki())
    assert identity['identity_key']=='dfb-546' and identity['source_entity_id']=='dfb:546'
    assert identity['parent_wikidata_id']=='Q107818' and 'wikidata_id' not in identity
    assert reserve_reference(REF)


@pytest.mark.parametrize('fault',['team_id','bool_id','slug','name','missing','duplicate','foreign_url',
                                 'wrong_parent','wrong_reserve','wrong_team','wrong_backlink','foreign_backlink','qid'])
def test_foreign_or_partial_identity_cannot_bind_a_reserve_team(fault):
    s=scope();bundle=source();wiki=Wiki();p=bundle['reserve_identity_profiles']
    if fault=='team_id':s['source_team_id']=545
    elif fault=='bool_id':s['source_team_id']=True
    elif fault=='slug':s['team_slug']='energie-cottbus'
    elif fault=='name':s['team']='Energie Cottbus'
    elif fault=='missing':p.pop()
    elif fault=='duplicate':p[1]=deepcopy(p[0])
    elif fault=='foreign_url':p[1]['url']=TEAM.replace('datencenter.dfb.de','example.org')
    elif fault=='wrong_parent':p[0]['html']=p[0]['html'].replace('FC Energie Cottbus','SC Freiburg')
    elif fault=='wrong_reserve':p[0]['html']=p[0]['html'].replace('Energie Cottbus II (Männer)','Energie Cottbus U 19 (Junioren)')
    elif fault=='wrong_team':p[1]['html']=p[1]['html'].replace('Energie Cottbus II','Energie Cottbus')
    elif fault=='wrong_backlink':p[1]['html']=p[1]['html'].replace('/clubs/fc-energie-cottbus-e-v','/clubs/sc-freiburg')
    elif fault=='foreign_backlink':p[1]['html']=p[1]['html'].replace('/clubs/fc-energie-cottbus-e-v','https://example.org/clubs/fc-energie-cottbus-e-v')
    else:wiki.get_page_identity=lambda title:{'title':title,'qid':'Q106394'}
    with pytest.raises(UpdateError):energie_reserve_identity(s,bundle,wiki)


@pytest.mark.parametrize('text',[REF.replace('Энерги II','Энерги'),REF.replace('FC Energie Cottbus II','FC Energie Cottbus'),
    REF.replace('Флаг Германии','Флаг Франции'),REF+' [[Энерги]]',REF+' другая команда',
    REF.replace('|en|','|de|'),REF.replace('Нп5','Нп'),
    '{{Флаг Германии|20px}} '+REF, '<span>'+REF+'</span>',REF+' [https://example.org ]'])
def test_parent_youth_foreign_or_ambiguous_wikitext_does_not_select_reserve(text):
    assert not reserve_reference(text)


def test_complete_binding_uses_a_source_identity_and_separate_literal_row():
    class API:
        def club_secondary_source(self,pid):return source()
    text=MINI.replace(TABLE_AUGSBURG,REF).replace('2025/26','2012/13')
    values,entities,issues,scopes=club_secondary_scopes(snapshot(text),MAPPING,API(),Wiki(),
                                                      WikitextParser(text).club_table(),NOW)
    assert values[('dfb-546','2012/13','Чемпионат')]['numbers']=={'appearances':4,'goals':0}
    assert entities['dfb-546']['wikitext']==REF and 'wikidata_id' not in entities['dfb-546']
    matched=next(s for s in scopes if s.get('identity_key')=='dfb-546')
    assert matched['qid'] is None
