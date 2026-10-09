# SPDX-License-Identifier: MIT
import json
from copy import deepcopy
import pytest
import requests
from wiki_stats.api_football_client import ApiFootballClient
from wiki_stats.state_store import StateStore
from wiki_stats.errors import UpdateError
from wiki_stats.statistics_engine import normalize_dataset,career_from_anchor
from server_helpers import player_row,dataset,ANCHOR


class Response:
    def __init__(self,data,status=200,headers=None): self.data,self.status_code,self.headers=data,status,headers or {}
    def json(self): return self.data


class Session:
    def __init__(self,replies): self.replies,self.headers,self.calls=list(replies),{},[]
    def get(self,url,**kwargs):
        self.calls.append((url,kwargs))
        reply=self.replies.pop(0)
        if isinstance(reply,Exception): raise reply
        return reply


def envelope(page=1,total=1,rows=None,path='players'):
    rows=rows if rows is not None else [player_row(pid=990000+page)]
    return {'get':path,'errors':[],'results':len(rows),'paging':{'current':page,'total':total},'response':rows}


def client(tmp_path,replies):
    store=StateStore(tmp_path/'api.sqlite')
    session=Session(replies)
    api=ApiFootballClient(store,key='SECRET-NEVER-LOG',session=session,clock=lambda:1800000000,sleep=lambda _:None)
    return api,store,session


def test_api_all_pages_and_cache(tmp_path):
    api,store,session=client(tmp_path,[Response(envelope(1,2)),Response(envelope(2,2))])
    ds=api.players(2026)
    assert ds['complete'] and ds['pages']==2 and len(ds['response'])==2
    assert session.headers['x-apisports-key']=='SECRET-NEVER-LOG'
    assert session.calls[0][1]['params']=={'league':78,'season':2026,'page':1}
    assert session.calls[0][1]['allow_redirects'] is False
    api.players(2026);assert len(session.calls)==2
    assert 'SECRET-NEVER-LOG' not in json.dumps(dict(store.items('api_cache')))


@pytest.mark.parametrize('status,code',[(401,'api_authentication'),(403,'api_authentication'),(429,'api_rate_limited'),(500,'api_unavailable'),(302,'api_unavailable')])
def test_api_failures_stop_without_retry(tmp_path,status,code):
    api,_,s=client(tmp_path,[Response({},status)])
    with pytest.raises(UpdateError) as exc: api.players(2026)
    assert exc.value.code==code and len(s.calls)==1


def test_api_network_exception_redacts_key(tmp_path):
    api,_,s=client(tmp_path,[requests.ConnectionError('SECRET-NEVER-LOG')])
    with pytest.raises(UpdateError) as exc: api.players(2026)
    assert exc.value.code=='api_unavailable' and 'SECRET' not in str(exc.value)


def test_api_error_in_http200_redacts_key(tmp_path):
    api,_,_=client(tmp_path,[Response({'errors':{'token':'SECRET-NEVER-LOG'}})])
    with pytest.raises(UpdateError) as exc: api.status()
    assert exc.value.code=='api_authentication' and 'SECRET' not in str(exc.value)


def test_quota_stops_partial_dataset_and_resumes(tmp_path):
    api,store,s=client(tmp_path,[Response(envelope(1,2),headers={'x-ratelimit-requests-remaining':'0'})])
    with pytest.raises(UpdateError) as exc: api.players(2026)
    assert exc.value.code=='api_quota_exhausted'
    assert store.get('datasets','2026') is None
    assert store.get('api_checkpoint','2026')['next_page']==2
    store.put('api_state','quota',{'remaining':100})
    s.replies.append(Response(envelope(2,2)))
    assert api.players(2026)['complete'] and len(s.calls)==2


@pytest.mark.parametrize('body',[envelope(rows=[]),envelope(page=2),{**envelope(),'results':200}])
def test_incomplete_and_invalid_pages_never_publish_dataset(tmp_path,body):
    api,store,_=client(tmp_path,[Response(body)])
    with pytest.raises(UpdateError): api.players(2026)
    assert store.get('datasets','2026') is None


def test_pagination_changes_stop(tmp_path):
    api,store,_=client(tmp_path,[Response(envelope(1,2)),Response(envelope(2,3))])
    with pytest.raises(UpdateError) as exc: api.players(2026)
    assert exc.value.code=='api_incomplete' and not store.get('datasets','2026')


def test_status_uses_reported_limits_no_account_email(tmp_path):
    api,store,_=client(tmp_path,[Response({'errors':[],'response':{'account':{'email':'PRIVATE'},'requests':{'current':20,'limit_day':100},'subscription':{'active':True}}})])
    assert api.status()['requests']['limit_day']==100
    assert store.get('api_state','quota')['remaining']==80
    assert 'PRIVATE' not in json.dumps(dict(store.items('api_cache')))


def test_normalize_null_and_appearences_not_lineups():
    _,observations=normalize_dataset(dataset([player_row(None,0)]))
    assert observations[0]['stats']['appearances'] is None and observations[0]['status']=='needs_review'
    with pytest.raises(UpdateError): career_from_anchor(ANCHOR,observations[0])


def test_two_teams_same_player_not_summed_and_duplicate_conflicts():
    row=player_row();row['statistics'].append(player_row(tid=200)['statistics'][0])
    players,observations=normalize_dataset(dataset([row,row]))
    assert len(players)==1 and len(observations)==2
    conflicting=deepcopy(row);conflicting['statistics'][0]['games']['appearences']=99
    with pytest.raises(UpdateError) as exc: normalize_dataset(dataset([row,conflicting]))
    assert exc.value.code=='api_duplicate_conflict'


def test_career_54_never_replaced_by_season8():
    anchor={**ANCHOR,'career':{'appearances':54,'goals':4},'season_stats':{'appearances':8,'goals':1}}
    _,observations=normalize_dataset(dataset([player_row(10,2)]))
    assert career_from_anchor(anchor,observations[0])=={'appearances':56,'goals':5}
    observations[0]['stats']['appearances']=7
    with pytest.raises(UpdateError) as exc: career_from_anchor(anchor,observations[0])
    assert exc.value.code=='needs_review'


def test_same_name_different_ids_and_incomplete_scope():
    players,observations=normalize_dataset(dataset([player_row(pid=1),player_row(pid=2)]))
    assert len(players)==2 and len(observations)==2
    with pytest.raises(UpdateError): normalize_dataset({**dataset(),'complete':False})


def test_partial_team_list_is_not_complete_league(tmp_path):
    api,_,_=client(tmp_path,[Response(envelope(total=2,path='teams'))])
    with pytest.raises(UpdateError) as exc: api.teams(2026)
    assert exc.value.code=='api_incomplete'


def test_missing_account_quota_is_rejected(tmp_path):
    api,_,_=client(tmp_path,[Response({'errors':[],'response':{'requests':{}}})])
    with pytest.raises(UpdateError) as exc: api.status()
    assert exc.value.code=='api_invalid_response'


@pytest.mark.parametrize('changes', [{'season': 2025}, {'season': True}, {'response': {}},
                                    {'fetched_at': 'bad'}, {'fetched_at': '2026-10-08T00:00:00'},
                                    {'fetched_at': '2099-01-01T00:00:00Z'}])
def test_dataset_season_and_timestamp_are_verified(changes):
    with pytest.raises(UpdateError) as exc:
        normalize_dataset({**dataset(), **changes}, expected_season=2026)
    assert exc.value.code == 'api_invalid_response'


def test_invalid_games_object_returns_domain_error():
    row = player_row(); row['statistics'][0]['games'] = []
    with pytest.raises(UpdateError) as exc:
        normalize_dataset(dataset([row]))
    assert exc.value.code == 'api_invalid_response'


def test_repeated_page_is_not_a_complete_league(tmp_path):
    rows = [player_row()]
    api, store, session = client(tmp_path, [Response(envelope(1, 2, rows)), Response(envelope(2, 2, rows))])
    with pytest.raises(UpdateError) as exc:
        api.players(2026)
    assert exc.value.code == 'api_incomplete' and store.get('datasets', '2026') is None
    assert len(session.calls) == 2
    assert not store.get('api_checkpoint', '2026')['complete']


def test_plan_restriction_is_distinct_from_invalid_key(tmp_path):
    api, _, _ = client(tmp_path, [Response({'errors': {'plan': 'SECRET-NEVER-LOG'}})])
    with pytest.raises(UpdateError) as exc:
        api.leagues(2026)
    assert exc.value.code == 'api_plan_restricted' and 'SECRET' not in str(exc.value)


@pytest.mark.parametrize('changes', [{'period': None}, {'period': '2027—2026'}, {'period': '2099—{{н.в.}}'},
                                    {'season': True}, {'season': 1}, {'career_wikitext': []}, {'evidence_url': None}])
def test_invalid_anchor_is_a_domain_error(changes):
    from wiki_stats.statistics_engine import validate_anchor
    with pytest.raises(UpdateError) as exc:
        validate_anchor({**ANCHOR, **changes})
    assert exc.value.code == 'anchor_invalid'
