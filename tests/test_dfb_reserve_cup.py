# SPDX-License-Identifier: MIT
from pathlib import Path
import gzip, json
import pytest
from tests.test_dfb_reserve_identity import scope, Wiki, REF
from tests.test_dfb_club_profile import source, NOW
from tests.test_dfb_tim_youth import MAPPING
from tests.stage23_helpers import MINI, TABLE_AUGSBURG, snapshot
from wiki_stats.dfb_reserve_identity import energie_reserve_identity
from wiki_stats.dfb_reserve_cup import reserve_cup_zero
from wiki_stats.dfb_club_cycle import club_secondary_scopes
from wiki_stats.wikitext_parser import WikitextParser
from wiki_stats.errors import UpdateError


def proof():
    with gzip.open(Path(__file__).parent/'source-fixtures/dfb-energie-reserve-cup.json.gz','rt',encoding='utf-8') as f:
        return json.load(f)


def inputs():
    s=scope();bundle=source();identity=energie_reserve_identity(s,bundle,Wiki())
    bundle['reserve_cup_eligibility']=proof()
    return s,identity,bundle


def test_real_rule_and_parent_cup_entry_prove_only_reserve_national_cup_zero():
    s,i,b=inputs();value=reserve_cup_zero(s,i,b,NOW)
    assert value['numbers']=={'appearances':0,'goals':0}
    assert value['category']=='Национальный кубок' and value['statistics_as_of']=='2013-06-30'
    assert value['competition_slug']=='dfb-pokal'
    assert value['complete'] and value['matches']==[]
    assert len(value['parent_entry_match_urls'])==1
    assert value['parent_entry_match_urls'][0].endswith('1005995')
    assert 'Еврокубки' not in value['category']


@pytest.mark.parametrize('fault',['parent_id','reserve_id','reserve_slug','old_season','wrong_season',
    'rule_url','pdf_hash','rule_text','rule_pages','cup_url','parent_heading','cup_heading',
    'backlink','foreign_backlink','reserve_entered','no_entry','duplicate_entry','future_date','foreign_protocol'])
def test_unproven_eligibility_and_foreign_or_missing_parent_entry_never_mean_zero(fault):
    s,i,b=inputs();p=b['reserve_cup_eligibility'];rule=p['rule'];cup=p['parent_cup']
    if fault=='parent_id':i['parent_wikidata_id']='Q106394'
    elif fault=='reserve_id':i['source_entity_id']='dfb:545'
    elif fault=='reserve_slug':s['team_slug']='energie-cottbus'
    elif fault=='old_season':s['season']='2008/09'
    elif fault=='wrong_season':s['season']='2011/12'
    elif fault=='rule_url':rule['url']=rule['url'].replace('www.dfb.de','example.org')
    elif fault=='pdf_hash':rule['pdf_sha256']='0'*64
    elif fault=='rule_text':rule['text']=rule['text'].replace('2009/2010','2008/2009')
    elif fault=='rule_pages':rule['pages']=[6,7]
    elif fault=='cup_url':cup['url']=cup['url'].replace('dfb-pokal','dfb-pokal-der-junioren')
    elif fault=='parent_heading':cup['html']=cup['html'].replace('Energie Cottbus','SC Freiburg')
    elif fault=='cup_heading':cup['html']=cup['html'].replace('DFB-Pokal 2012/2013','DFB-Pokal 2013/2014')
    elif fault=='backlink':cup['html']=cup['html'].replace('/clubs/fc-energie-cottbus-e-v','/clubs/sc-freiburg')
    elif fault=='foreign_backlink':cup['html']=cup['html'].replace('/clubs/fc-energie-cottbus-e-v','https://example.org/clubs/fc-energie-cottbus-e-v')
    elif fault=='reserve_entered':cup['html']=cup['html'].replace('>Energie Cottbus<','>Energie Cottbus II<')
    elif fault=='no_entry':cup['html']=cup['html'].replace('c-MatchTable-row','ignored-row')
    elif fault=='duplicate_entry':
        # A second copy of the complete source duplicates the actual entry and headings.
        cup['html']*=2
    elif fault=='future_date':cup['html']=cup['html'].replace('20.08.2012','20.08.2013')
    else:cup['html']=cup['html'].replace('https://datencenter.dfb.de/datencenter/dfb-pokal/','https://example.org/datencenter/dfb-pokal/')
    with pytest.raises(UpdateError):reserve_cup_zero(s,i,b,NOW)


def test_missing_rule_does_not_invent_zero_or_block_verified_league():
    s,i,b=inputs();b.pop('reserve_cup_eligibility')
    assert reserve_cup_zero(s,i,b,NOW) is None


def test_source_binding_preserves_other_unknown_reserve_categories():
    s,i,b=inputs()
    class API:
        def club_secondary_source(self,pid):return b
    text=MINI.replace(TABLE_AUGSBURG,REF).replace('2025/26','2012/13')
    values,entities,issues,scopes=club_secondary_scopes(snapshot(text),MAPPING,API(),Wiki(),
        WikitextParser(text).club_table(),NOW)
    assert values[('dfb-546','2012/13','Чемпионат')]['numbers']=={'appearances':4,'goals':0}
    assert values[('dfb-546','2012/13','Национальный кубок')]['numbers']=={'appearances':0,'goals':0}
    assert ('dfb-546','2012/13','Еврокубки') not in values
    assert ('dfb-546','2012/13','Стыковые матчи') not in values


def test_corrupted_cup_proof_keeps_league_available_and_reports_failure():
    s,i,b=inputs();b['reserve_cup_eligibility']['rule']['pages']=[6,7]
    class API:
        def club_secondary_source(self,pid):return b
    text=MINI.replace(TABLE_AUGSBURG,REF).replace('2025/26','2012/13')
    values,_,issues,_=club_secondary_scopes(snapshot(text),MAPPING,API(),Wiki(),WikitextParser(text).club_table(),NOW)
    assert ('dfb-546','2012/13','Чемпионат') in values
    assert ('dfb-546','2012/13','Национальный кубок') not in values
    assert any(x['code']=='dfb_reserve_cup_unproven' for x in issues)


def test_pipeline_corrects_stale_reserve_cup_without_erasing_other_columns(monkeypatch):
    from tests.test_sofascore import prepare
    s,i,b=inputs();cup=reserve_cup_zero(s,i,b,NOW)
    entity={**i,'wikitext':REF}
    monkeypatch.setattr('wiki_stats.dfb_club_cycle.club_secondary_scopes',lambda *a,**k:(
        {('dfb-546','2012/13','Чемпионат'):s,('dfb-546','2012/13','Национальный кубок'):cup},
        {'dfb-546':entity},[],[s,cup]))
    text=MINI.replace(TABLE_AUGSBURG,REF).replace('2025/26','2012/13')
    text=text.replace('|4|1|0|0|0|0|0|0}}','|4|1|9|3|2|1|0|0}}')
    _,plan,report=prepare(text)
    changes=[c for c in plan.changes if c.operation.get('season')=='2012/13']
    assert len(changes)==2 and all(c.status=='ready' for c in changes)
    assert '{{КлСтат/Сезон|2012/13|4|0|0|0|2|1|0|0}}' in plan.preview
    change=next(c for c in changes if c.operation['competition']['category']=='Национальный кубок')
    assert change.operation['as_of']=='2013-06-30'
    assert {src['provenance'] for src in change.operation['sources']}=={'dfb'}
    assert '<!-- статистика -->' in plan.preview and 'Неприкосновенный источник' in plan.preview
