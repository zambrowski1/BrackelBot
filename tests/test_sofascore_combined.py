# SPDX-License-Identifier: MIT
import pytest
from wiki_stats.sofascore_combined import CombinedSofascoreClient, Sofa6Account, make_sofascore_client
from wiki_stats.sofascore_client import SofascoreClient
from wiki_stats.errors import UpdateError
from tests.test_sofascore import Store,Session,response


def six(data=None,status=200,remaining=499):
    r=response(data,status,remaining)
    r.headers['X-RateLimit-Requests-Limit']='500'
    return r


def api(responses,store=None):
    session=Session(responses)
    return CombinedSofascoreClient(store or Store(),primary_key='PRIMARY',fallback_keys=['BACKUP'],session=session,clock=lambda:100),session


def test_primary_season_and_cache_without_bulk():
    a,s=api([six({'statistics':{'appearances':2,'goals':2},'team':{'id':3,'national':False}})])
    data=a.statistics(7,328,11949,'overall')
    assert data['statistics']=={'appearances':2,'goals':2}
    assert a.statistics(7,328,11949,'overall')==data
    assert len(s.calls)==1
    assert s.calls[0][1]['params']=={'player_id':7,'unique_tournament_id':328,'season_id':11949}
    assert 'career' not in s.calls[0][0]


@pytest.mark.parametrize('status',[404,502,403])
def test_fallback_and_separate_quota(status):
    a,s=api([six(status=status),response({'statistics':{'appearances':2,'goals':2}})])
    assert a.statistics(7,328,11949,'overall')['statistics']['goals']==2
    assert 'sofascore6.' in s.calls[0][0]
    assert 'sofascore-scraper-' in s.calls[1][0]
    assert a.primary.account != a.fallback.accounts[0].account
    assert a.quota_status()['sources']['sofascore6']['remaining']==499


def test_rate_limit_wait_is_scoped_to_provider():
    a,s=api([six(status=429,remaining=450),response({'ok':True})])
    a.request('/players/details',{'player_id':7})
    assert a.store.get('api_state','sofascore6-cooldown')>100
    assert a.store.get('api_state','cooldown',0)==0


def test_missing_headers_allow_only_one_primary_probe():
    bad=six({'wrong':'shape'});bad.headers={}
    a,s=api([bad,response({'ok':True}),response({'another':True})])
    a.request('/players/details',{'player_id':7})
    a.request('/players/details',{'player_id':8})
    assert len([url for url,kw in s.calls if 'sofascore6.' in url])==1


def test_unsupported_stage_and_matches_use_backup_without_primary_request():
    a,s=api([response({'ok':True}),response({'player_id':7,'page':1,'matches':[],'has_next_page':False})])
    a.statistics(7,35,26,'regularSeason')
    assert a.matches(7)['complete'] is True
    assert len(s.calls)==2
    assert all('sofascore-scraper-' in u for u,kw in s.calls)


def test_profile_and_lists_normalize_dates_and_national_identity():
    a,s=api([six({'id':7,'name':'Footballer','position':'F','dateOfBirthTimestamp':0,'shirtNumber':14,'team':{'id':3}}),
             six([{'uniqueTournament':{'id':35},'seasons':[{'id':26,'year':'26/27'}]}]),
             six([{'team':{'id':4,'national':True},'appearances':56,'goals':26}])])
    assert a.details(7)['date_of_birth']=='1970-01-01'
    assert a.seasons(7)['tournament_seasons'][0]['tournament']['id']==35
    assert a.national_statistics(7)['statistics'][0]['team']['is_national'] is True


def test_wrong_player_is_not_silently_replaced():
    a,s=api([six({'id':8,'position':'F'})])
    with pytest.raises(UpdateError) as exc:a.details(7)
    assert exc.value.code=='identity_mismatch'
    assert len(s.calls)==1


def test_malformed_normalization_not_cached():
    a,s=api([six([{'uniqueTournament':None}]),response({'player_id':7,'tournament_seasons':[]})])
    assert a.seasons(7)['tournament_seasons']==[]
    assert len(s.calls)==2
    assert next(v for (bucket,k),v in a.store.data.items() if bucket=='cache')['delivery_provider'].startswith('sofascore-scraper-')


def test_same_key_across_subscriptions_has_distinct_state():
    store=Store();session=Session([six({'statistics':{'appearances':0,'goals':0},'team':{'id':3}}),response({'ok':True})])
    a=CombinedSofascoreClient(store,primary_key='SAME',fallback_keys=['SAME'],session=session,clock=lambda:100)
    a.statistics(7,35,26,'overall');a.request('/search',{'query':'x'})
    assert len([key for bucket,key in store.data if bucket=='quota'])==2


def test_factory_preserves_existing_configuration(monkeypatch):
    monkeypatch.setenv('SOFASCORE_API_KEY','BACKUP');monkeypatch.delenv('SOFASCORE_KEYS',raising=False)
    monkeypatch.delenv('SOFASCORE6_API_KEY',raising=False)
    assert isinstance(make_sofascore_client(Store()),SofascoreClient)
    monkeypatch.setenv('SOFASCORE6_API_KEY','PRIMARY')
    assert isinstance(make_sofascore_client(Store()),CombinedSofascoreClient)


def test_empty_season_statistics_falls_back_without_inventing_zero():
    a,s=api([six({'statistics':{},'team':{'id':3}}),response({'statistics':{'appearances':2,'goals':1}})])
    assert a.statistics(7,35,26,'overall')['statistics']=={'appearances':2,'goals':1}
    assert len(s.calls)==2
