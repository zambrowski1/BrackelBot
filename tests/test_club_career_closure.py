import gzip,json
from pathlib import Path
from types import SimpleNamespace
from copy import deepcopy
import pytest
from tests.test_sofascore_belgium import fixture,NOW
from wiki_stats.club_career_closure import gent_closed_spell

def setup():
    x,r=fixture();r['resolved']=SimpleNamespace(qid='Q18232')
    records=[r]
    for tid,sid,apps in [(7,29267,3),(679,29343,5)]:
        records.append({**r,'tournament_id':tid,'season_id':sid,'numbers':{'appearances':apps,'goals':1}})
    with gzip.open(Path(__file__).parent/'source-fixtures/gent-closed-spell.json.gz','rt',encoding='utf-8') as f:proof=json.load(f)
    api=SimpleNamespace(club_career_secondary_source=lambda pid:proof)
    mapping={'api_id':228981,'qid':'Q15627096'}
    return mapping,records,x['history'],api,proof

def test_official_ended_spell_matches_all_23_competitive_appearances():
    m,r,h,a,p=setup();v=gent_closed_spell(m,r,h,a,NOW)
    assert v['numbers']=={'appearances':0,'goals':0}
    assert v['official_total']=={'appearances':23,'goals':3}
    assert len(v['confirmed_match_ids'])==23
    assert v['statistics_as_of']=='2021-06-30'

@pytest.mark.parametrize('fault',['wrong_player','wrong_qid','foreign_url','wrong_heading','wrong_date','missing_loan','loan_end','unfinished_spell','more_matches','fewer_goals','incomplete_history','missing_played','duplicate','extra_cup','unknown_cup','extra_club_record','wrong_club','receiving_missing','receiving_subtotal','receiving_foreign','after_loan'])
def test_closed_spell_rejects_damaged_identity_totals_sources_and_coverage(fault):
    m,r,h,a,p=setup()
    if fault=='wrong_player':m['api_id']=999
    elif fault=='wrong_qid':m['qid']='Q1'
    elif fault=='foreign_url':p['total']['url']='https://example.com'
    elif fault=='wrong_heading':p['total']['html']=p['total']['html'].replace('Tim Kleindienst keert terug','Other player keert terug')
    elif fault=='wrong_date':p['total']['html']=p['total']['html'].replace('18-06-2021','18-06-2020')
    elif fault=='missing_loan':p.pop('loan')
    elif fault=='loan_end':p['loan']['html']=p['loan']['html'].replace('tot 30 juni','tot 30 maart')
    elif fault=='unfinished_spell':p['total']['html']=p['total']['html'].replace('definitief','mogelijk')
    elif fault=='more_matches':p['total']['html']=p['total']['html'].replace('23 wedstrijden','24 wedstrijden')
    elif fault=='fewer_goals':p['total']['html']=p['total']['html'].replace('3 keer de weg','2 keer de weg')
    elif fault=='incomplete_history':h['complete']=False
    elif fault=='missing_played':h['matches']=[v for v in h['matches'] if v['id']!=8833001]
    elif fault=='duplicate':h['matches'].append(deepcopy(h['matches'][0]))
    elif fault in {'extra_cup','unknown_cup'}:
        v=deepcopy(next(v for v in h['matches'] if v['id']==8833001));v['id']=999999;v['tournament']['id']=999
        if fault=='unknown_cup':v['player']['statistics']=None
        h['matches'].append(v)
    elif fault=='extra_club_record':r.append(deepcopy(r[0]))
    elif fault=='wrong_club':r[0]['resolved']=SimpleNamespace(qid='Q1')
    elif fault=='receiving_missing':p.pop('receiving_club')
    elif fault=='receiving_subtotal':p['receiving_club']['html']=p['receiving_club']['html'].replace('23 Einsätze','22 Einsätze')
    elif fault=='receiving_foreign':p['receiving_club']['url']='https://example.com'
    else:next(v for v in h['matches'] if v['id']==8833001)['start_time']='2021-03-01T12:00:00Z'
    assert gent_closed_spell(m,r,h,a,NOW) is None

def test_friendly_goals_do_not_change_competitive_categories():
    m,r,h,a,p=setup()
    friendly=next(v for v in h['matches'] if v['id']==8972333)
    friendly['player']['statistics']={'minutes_played':90,'goals':3}
    assert gent_closed_spell(m,r,h,a,NOW)['official_total']=={'appearances':23,'goals':3}

def test_pipeline_uses_closed_spell_provenance_for_stale_category(monkeypatch):
    # Source verification is tested above with real HTML and all 23 matches.
    # Here the operator transaction must update the stale cell and its totals.
    from tests.test_sofascore import prepare
    from tests.stage23_helpers import MINI
    import wiki_stats.club_career_closure as closure
    _,r,h,a,_=setup()
    proof=gent_closed_spell({'api_id':228981,'qid':'Q15627096'},r,h,a,NOW)
    proof.update(qid='Q15755',season='2026/27')
    monkeypatch.setattr(closure,'gent_closed_spell',lambda *args:proof)
    text=MINI.replace('{{КлСтат/Сезон|2025/26|4|1|0|0','{{КлСтат/Сезон|2025/26|4|1|9|3')
    # Keep the league outside the stale domestic cup values.
    _,plan,evidence=prepare(text)
    assert any(v['code']=='remaining_categories_from_closed_club_spell' for v in evidence['review'])
    assert all(c.status in {'ready','already_applied'} for c in plan.changes)
    ops=[c.operation for c in plan.changes if c.operation['type']=='update_stats'
         and c.operation.get('competition',{}).get('category')=='Национальный кубок']
    assert ops
    assert any(s['provenance']=='club_official' for op in ops for s in op['sources'])
    assert all(op['as_of']=='2026-10-09' for op in ops)
    assert all(s['coverage_as_of']==op['as_of'] and '2021-06-30' in s['note'] for op in ops for s in op['sources'])

def test_ambiguous_source_metadata_cannot_supply_zero():
    m,r,h,a,p=setup()
    p['total']['html']='<meta property="og:site_name" content="Other club">'+p['total']['html']
    assert gent_closed_spell(m,r,h,a,NOW) is None

def test_source_not_published_at_requested_date_cannot_supply_zero():
    from datetime import datetime,timezone
    m,r,h,a,p=setup()
    assert gent_closed_spell(m,r,h,a,datetime(2022,8,1,tzinfo=timezone.utc)) is None
