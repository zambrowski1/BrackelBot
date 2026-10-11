# SPDX-License-Identifier: MIT
import json
from pathlib import Path
import pytest
from wiki_stats.dfb_evidence import dfb_unused_substitute


def evidence():
    base=Path(__file__).parent/'source-fixtures'
    data=json.loads((base/'dfb-unused-orban.json').read_text(encoding='utf-8'))
    return data,(base/'dfb-match-358803.html').read_text(encoding='utf-8')


def test_independent_actual_match_sheet_exhausts_three_substitutions_without_orban():
    data,html=evidence()
    result=dfb_unused_substitute(data['row'],136924,2675,data['lineup'],html,data['url'])
    assert result['appearances']==0 and result['goals']==0
    assert result['entered_players']==['dorge kouemaha','oliver kirch','richard sukuta-pasu']
    assert result['source_url']==data['url'] and result['match_id']==1763180


def test_calendar_uses_primary_proof_and_retains_its_distinct_provenance():
    from datetime import datetime,timezone
    from wiki_stats.sofascore_calendar import recover_calendar
    data,html=evidence();row=data['row'];sid=row['season']['id']
    class SourceAPI:
        def tournament_seasons(self,tid):
            return {'tournament_id':217,'count':1,'seasons':[{'id':sid,'year':row['season']['year']}]}
        def tournament_matches(self,*args,**kwargs):
            return {'complete':True,'tournament_id':217,'season_id':sid,'matches':[row]}
        def match_lineups(self,mid):return data['lineup']
        def match_incidents(self,mid):
            return {'match_id':mid,'count':0,'incidents':[]}
        def match_secondary_source(self,mid):return {'url':data['url'],'html':html}
    result=recover_calendar({'season':'2011/12','team_id':2675,'numbers':{'appearances':1}},
        136924,SourceAPI(),{'remaining':10},datetime(2026,10,11,tzinfo=timezone.utc),217)
    assert result['numbers']=={'appearances':0,'goals':0}
    assert result['secondary_proofs'][0]['source_url']==data['url']
    assert not result['roster_proven_absences']


def test_review_package_can_retain_dfb_as_a_separate_source():
    from tests.test_sofascore import prepare
    from wiki_stats.schema_validator import validate_package
    package,_,_=prepare()
    data,_=evidence()
    operation=package['articles'][0]['operations'][0]
    operation['sources'].append({'url':data['url'],'provenance':'dfb',
        'coverage_as_of':operation['as_of'],'note':'Independent historical match sheet'})
    validate_package(package)


@pytest.mark.parametrize('problem',['missing_change','target_entered','incorrect_roster','score',
    'date','canonical','untrusted_url','modern_rules','wrong_match','unconfirmed',
    'partial_roster','starter','duplicate_id','wrong_team','wrong_tournament'])
def test_source_disagreement_or_incomplete_substitution_proof_never_supplies_zero(problem):
    data,html=evidence();row=data['row'];lineup=data['lineup'];url=data['url']
    if problem in {'missing_change','target_entered'}:
        marker='m-MatchDetails-history-title m-MatchDetails-title">Wechsel'
        first,last=html.split(marker)
        last=last.replace('Dorge Kouemaha','' if problem=='missing_change' else 'Willi Orban',1)
        html=first+marker+last
    elif problem=='incorrect_roster':html=html.replace('Clemens Walch','Another Player')
    elif problem=='score':row['away_score']['current']=4
    elif problem=='date':row['start_time']='2011-07-31T13:30:00Z'
    elif problem=='canonical':html=html.replace('property="og:url"','property="different:url"')
    elif problem=='untrusted_url':url=url.replace('datencenter.dfb.de','example.org')
    elif problem=='modern_rules':
        html=html.replace('2011','2016').replace('2012','2017')
        url=url.replace('2011','2016').replace('2012','2017')
        row['start_time']='2016-07-30T13:30:00Z';row['season']['year']='16/17'
    elif problem=='wrong_match':lineup['match_id']=999
    elif problem=='unconfirmed':lineup['is_confirmed']=False
    elif problem=='partial_roster':lineup['away']['players'].pop()
    elif problem=='starter':
        target=next(p for p in lineup['away']['players'] if p['player']['id']==136924)
        target['is_substitute']=False
    elif problem=='duplicate_id':lineup['away']['players'][-1]['player']['id']=136924
    elif problem=='wrong_team':row['away_team']['id']=999
    else:row['tournament']['id']=35
    assert dfb_unused_substitute(row,136924,2675,lineup,html,url) is None
