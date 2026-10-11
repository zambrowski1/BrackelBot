# SPDX-License-Identifier: MIT
from tests.test_sofascore import API, prepare
from tests.stage23_helpers import MINI
from wiki_stats.errors import UpdateError


def secondary(monkeypatch, season, appearances, goals):
    scope={'numbers':{'appearances':appearances,'goals':goals},'statistics_as_of':'2025-05-17',
           'sources':['https://datencenter.dfb.de/personen/test-player/spieler']}
    monkeypatch.setattr('wiki_stats.dfb_club_cycle.club_secondary_scopes', lambda *a,**k: (
        {('Q15755',season,'Чемпионат'):scope}, {'Q15755':{'name':'Аугсбург'}}, [], [scope]))


def test_missing_primary_season_uses_verified_secondary_with_correct_provenance(monkeypatch):
    secondary(monkeypatch,'2024/25',7,0)
    class NoStats(API):
        def statistics(self,*a):raise UpdateError('api_unavailable','No primary aggregate')
    package,plan,report=prepare(MINI.replace('2025/26','2024/25'),NoStats())
    changes=[c for c in plan.changes if c.operation.get('season')=='2024/25'
             and c.operation.get('competition',{}).get('category')=='Чемпионат']
    assert len(changes)==1 and changes[0].status=='ready'
    assert changes[0].operation['new']=={'appearances':7,'goals':0}
    assert {s['provenance'] for s in changes[0].operation['sources']}=={'dfb'}
    assert changes[0].source_verification=='dfb_collected_scope_checked'
    assert '{{КлСтат/Сезон|2024/25|7|0|' in plan.preview
    assert report['secondary_club_scopes']


def test_disagreeing_sources_preserve_cell_and_make_conflict_visible(monkeypatch):
    secondary(monkeypatch,'2026/27',8,3)
    _,plan,report=prepare()
    assert any(i['code']=='club_sources_disagree' for i in report['review'])
    assert '{{КлСтат/Сезон|2026/27|4|1|' in plan.preview
    assert any('расходятся' in w for w in plan.warnings)
    assert not plan.publishable


def test_uncovered_existing_cells_and_global_dates_are_not_silent():
    class NoStats(API):
        def statistics(self,*a):raise UpdateError('api_unavailable','No primary aggregate')
    _,plan,report=prepare(api=NoStats())
    assert any(i['code']=='category_not_covered' for i in report['review'])
    assert any('графы существующих' in w for w in plan.warnings)
    assert any('Общая дата' in w for w in plan.warnings)
    assert not plan.publishable


def test_same_season_league_and_cup_keep_their_independent_source_dates(monkeypatch):
    league={'numbers':{'appearances':7,'goals':0},'statistics_as_of':'2025-05-17',
            'sources':['https://datencenter.dfb.de/personen/test-player/spieler']}
    cup={'numbers':{'appearances':2,'goals':1},'statistics_as_of':'2024-08-17',
         'sources':['https://datencenter.dfb.de/personen/test-player/spieler']}
    monkeypatch.setattr('wiki_stats.dfb_club_cycle.club_secondary_scopes', lambda *a,**k: (
        {('Q15755','2024/25','Чемпионат'):league,('Q15755','2024/25','Национальный кубок'):cup},
        {'Q15755':{'name':'Аугсбург'}}, [], [league,cup]))
    class NoStats(API):
        def statistics(self,*a):raise UpdateError('api_unavailable','No primary aggregate')
    _,plan,_=prepare(MINI.replace('2025/26','2024/25'),NoStats())
    changes=[c for c in plan.changes if c.operation.get('season')=='2024/25'
             and c.operation['type']=='update_stats']
    assert len(changes)==2 and all(c.status=='ready' for c in changes)
    assert len({c.group_id for c in changes})==2
    assert '{{КлСтат/Сезон|2024/25|7|0|2|1|' in plan.preview


def test_reserve_source_identity_updates_only_its_literal_row(monkeypatch):
    from tests.test_dfb_reserve_identity import REF
    from tests.stage23_helpers import TABLE_AUGSBURG
    scope={'numbers':{'appearances':4,'goals':0},'statistics_as_of':'2013-05-19',
           'sources':['https://datencenter.dfb.de/personen/tim-kleindienst/spieler']}
    entity={'name':'Энерги II','wikitext':REF,'source_entity_id':'dfb:546',
            'parent_wikidata_id':'Q107818'}
    monkeypatch.setattr('wiki_stats.dfb_club_cycle.club_secondary_scopes',lambda *a,**k:(
        {('dfb-546','2012/13','Чемпионат'):scope},{'dfb-546':entity},[],[scope]))
    text=MINI.replace(TABLE_AUGSBURG,REF).replace('2025/26','2012/13')
    _,plan,_=prepare(text)
    changes=[c for c in plan.changes if c.operation.get('season')=='2012/13']
    assert len(changes)==1 and changes[0].status=='ready'
    assert changes[0].operation['entity']['wikitext']==REF
    assert 'wikidata_id' not in changes[0].operation['entity']
    assert '{{КлСтат/Сезон|2012/13|4|0|0|0|0|0|0|0}}' in plan.preview
    assert '<!-- статистика -->' in plan.preview and 'Неприкосновенный источник' in plan.preview
