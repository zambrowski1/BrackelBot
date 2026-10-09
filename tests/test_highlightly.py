# SPDX-License-Identifier: MIT
"""All football figures and credentials here are synthetic."""
from copy import deepcopy
from dataclasses import replace
import json
import pytest
import requests
from wiki_stats.api_key_pool import ApiKeyPool
from wiki_stats.api_provider import ProviderStore
from wiki_stats.highlightly_client import HighlightlyClient
from wiki_stats.highlightly_scheduler import HighlightlyCycleRunner
from wiki_stats.state_store import StateStore
from wiki_stats.errors import UpdateError
from wiki_stats.statistics_engine import normalize_dataset
from wiki_stats.scheduler import publish_plan
from wiki_stats.publication_policy import PublicationPolicy
from wiki_stats.operator_review import deserialize_plan, approve_stored
from server_helpers import seed, community as legacy_community, ServerApi, ServerWiki, dataset, PLAYER, ANCHOR


def community(store):
    legacy_community(store)
    approval=store.get('policy','community_approval')
    store.put('policy','community_approval',{**approval,'providers':['highlightly']})


class Response:
    def __init__(self, data, status=200, remaining=90, headers=None):
        self.data, self.status_code = data, status
        self.headers = headers if headers is not None else {
            'x-ratelimit-requests-limit': '100', 'x-ratelimit-requests-remaining': str(remaining)}
    def json(self): return self.data


class Session:
    def __init__(self, replies): self.replies, self.calls = list(replies), []
    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception): raise reply
        return reply


def listing(rows):
    return {'data': rows, 'plan': {'message': 'All data available with current plan.', 'tier': 'BASIC'},
            'pagination': {'totalCount': len(rows), 'limit': 1000, 'offset': 0}}


@pytest.fixture
def store(tmp_path):
    base = StateStore(tmp_path/'state.sqlite')
    value = ProviderStore(base, 'highlightly')
    yield value
    base.close()


def client(store, replies, entries=None, clock=None):
    session = Session(replies)
    api = HighlightlyClient(store, entries=entries or ['SYNTHETIC-KEY'], session=session,
                            sleep=lambda _: None, clock=clock or (lambda: 1800000000))
    return api, session


def test_five_keys_same_account_do_not_multiply_quota(store):
    api, s = client(store, [Response(listing([]), remaining=0)], entries=[f'SYNTHETIC-{i}' for i in range(5)])
    api.request('/leagues')
    with pytest.raises(UpdateError, match='квоты'): api.request('/players')
    assert len(s.calls) == 1
    status = api.quota_status()
    assert len(status['keys']) == 5 and len(status['quota_groups']) == 1 and status['usable_remaining'] == 0


def test_independent_subscriptions_rotate_and_persist(store):
    entries = [{'key': f'SYNTHETIC-{i}', 'group': f'subscription-{i}', 'daily_budget': 1} for i in range(5)]
    api, s = client(store, [Response(listing([]), remaining=0) for _ in entries], entries)
    for _ in entries: api.request('/leagues')
    assert [c[1]['headers']['x-rapidapi-key'] for c in s.calls] == [e['key'] for e in entries]
    restarted, _ = client(store, [], entries)
    with pytest.raises(UpdateError) as exc: restarted.request('/leagues')
    assert exc.value.code == 'api_quota_exhausted'
    assert 'SYNTHETIC' not in json.dumps(dict(store.items('key_quotas')))


def test_shared_quota_uses_smallest_observed_remaining(store):
    api, _ = client(store, [Response(listing([]), remaining=2), Response(listing([]), remaining=99)])
    api.request('/leagues'); api.request('/players')
    assert api.quota_status()['usable_remaining'] == 1


def test_day_rollover_and_budget_are_persistent(store):
    now = [1800000000]
    api, s = client(store, [Response(listing([]), remaining=0), Response(listing([]), remaining=99)], clock=lambda: now[0])
    api.request('/leagues')
    now[0] = (now[0]//86400 + 1)*86400
    api.request('/leagues')
    assert len(s.calls) == 2 and api.quota_status()['usable_remaining'] == 99


def test_rejected_key_rotates_within_shared_quota(store):
    api, s = client(store, [Response({}, 401), Response(listing([]), remaining=80)], ['BAD-SYNTHETIC', 'GOOD-SYNTHETIC'])
    api.request('/leagues')
    assert len(s.calls) == 2 and api.quota_status()['keys'][0]['disabled']
    restarted, _ = client(store, [], ['BAD-SYNTHETIC', 'GOOD-SYNTHETIC'])
    assert restarted.pool.select()['key'] == 'GOOD-SYNTHETIC'


def test_minute_throttle_is_global_not_key_rotation(store):
    entries = [{'key': 'A-SYNTHETIC', 'group': 'a'}, {'key': 'B-SYNTHETIC', 'group': 'b'}]
    api, s = client(store, [Response({}, 429, remaining=70, headers={'Retry-After': '120'})], entries)
    for _ in range(2):
        with pytest.raises(UpdateError) as exc: api.request('/leagues')
        assert exc.value.code == 'api_rate_limited'
    assert len(s.calls) == 1 and api.quota_status()['cooldown_until'] == 1800000120


def test_daily_429_can_select_independent_subscription(store):
    entries = [{'key': 'A-SYNTHETIC', 'group': 'a'}, {'key': 'B-SYNTHETIC', 'group': 'b'}]
    api, s = client(store, [Response({}, 429, remaining=0), Response(listing([]), remaining=95)], entries)
    api.request('/leagues')
    assert len(s.calls) == 2 and s.calls[-1][1]['headers']['x-rapidapi-key'] == 'B-SYNTHETIC'


@pytest.mark.parametrize('reply,code', [(Response({},500),'api_unavailable'),
    (requests.ConnectionError('SYNTHETIC-KEY'),'api_unavailable'),
    (Response({'error':'SYNTHETIC-KEY'}),'api_invalid_response')])
def test_failures_do_not_retry_or_leak_secret(store,reply,code):
    api,s = client(store,[reply])
    with pytest.raises(UpdateError) as exc: api.request('/players')
    assert exc.value.code == code and 'SYNTHETIC' not in str(exc.value)
    assert len(s.calls) == 1 and not store.items('api_cache')


def test_cache_does_not_spend_quota(store):
    api, s = client(store, [Response(listing([]))])
    api.request('/leagues', ttl=100); api.request('/leagues', ttl=100)
    assert len(s.calls) == 1 and api.quota_status()['usable_remaining'] == 90


def test_hidden_plan_and_partial_pages_are_rejected(store):
    data = listing([{'id': 1}]); data['plan']['message'] = 'Some results might be hidden with FREE tier.'
    api, _ = client(store, [Response(data)])
    with pytest.raises(UpdateError) as exc: api.search_players('Synthetic')
    assert exc.value.code == 'api_plan_restricted'


def test_search_rejects_repeated_ids_and_changing_total(store):
    data = listing([{'id': 1}, {'id': 1}])
    api, _ = client(store, [Response(data)])
    with pytest.raises(UpdateError) as exc: api.search_players('Synthetic')
    assert exc.value.code == 'api_duplicate_conflict'


def test_provider_ids_and_policies_are_isolated(store):
    store.base.put('players', 1, {'name': 'API-Football'})
    store.put('players', 1, {'name': 'Highlightly'})
    assert store.get('players',1)['name'] == 'Highlightly' and store.base.get('players',1)['name'] == 'API-Football'
    store.base.put('policy','kill_switch',True)
    assert store.get('policy','kill_switch') is True


def test_duplicate_credentials_and_regrouping_rejected(store):
    with pytest.raises(UpdateError): ApiKeyPool(store, ['SYNTHETIC','SYNTHETIC'], clock=lambda: 1)
    ApiKeyPool(store, [{'key':'SYNTHETIC','group':'original'}], clock=lambda: 1)
    with pytest.raises(UpdateError): ApiKeyPool(store, [{'key':'SYNTHETIC','group':'changed'}], clock=lambda: 1)


class Api(ServerApi):
    provider = 'highlightly'
    def scope(self, season): return {15755: {'id': 15755, 'name': 'Augsburg', 'national': False}}
    def quota_status(self): return {'usable_remaining': 90}
    def current_team(self, pid, season): return self.scope(season)[15755]
    def transfer_dates(self, pid, fresh=False): return ['2020-01-01']
    def profile(self, pid, fresh=False):
        return PLAYER, {'transfers': [{'type':'transfer'}]}, '2026-10-09T00:00:00+00:00'
    def player(self, pid, season, fresh=True):
        return {**dataset(self.rows), 'provider':'highlightly', 'coverage':'single_player',
                'source_url':f'https://soccer.highlightly.net/players/{pid}/statistics'}


def prepare(store):
    seed(store); api=Api(); wiki=ServerWiki()
    report=HighlightlyCycleRunner(store,api,wiki).run()
    assert report['status']=='completed' and report['prepared_count']==1 and not wiki.saved
    return report,api,wiki


def test_registered_player_cycle_prepares_atomic_diff_with_correct_source(store):
    report, _, _ = prepare(store)
    plan = deserialize_plan(store.get('plans', report['plans'][0]['hash'])['plan'])
    assert len(plan.changes) == 2 and '5 (2)' in plan.preview
    assert plan.changes[0].operation['sources'][0]['provenance'] == 'highlightly'
    assert report['coverage']=='registered_players_only'


def test_publish_rechecks_highlightly_and_detects_changed_stats(store, monkeypatch):
    report,api,wiki=prepare(store); community(store); monkeypatch.setenv('BRACKELBOT_PUBLICATION','1')
    h=report['plans'][0]['hash']; plan=deserialize_plan(store.get('plans',h)['plan'])
    for c in plan.changes:
        if c.status=='ready': approve_stored(store,h,c.id)
    api.rows[0]['statistics'][0]['goals']['total']=3
    with pytest.raises(UpdateError) as exc: publish_plan(store,h,wiki,PublicationPolicy(store,'manual'),source_api=api)
    assert exc.value.code=='source_changed' and store.get('plans',h)['status']=='stale' and not wiki.saved


def test_highlightly_mock_publish_and_quota_resume(store, monkeypatch):
    report,api,wiki=prepare(store); community(store); monkeypatch.setenv('BRACKELBOT_PUBLICATION','1')
    h=report['plans'][0]['hash']; plan=deserialize_plan(store.get('plans',h)['plan'])
    for c in plan.changes:
        if c.status=='ready': approve_stored(store,h,c.id)
    publish_plan(store,h,wiki,PublicationPolicy(store,'manual'),source_api=api)
    assert len(wiki.saved)==1
    api.player=lambda *a,**k: (_ for _ in ()).throw(UpdateError('api_quota_exhausted','Stop'))
    report=HighlightlyCycleRunner(store,api,wiki).run()
    assert report['status']=='stopped' and store.get('api_checkpoint','2026')['next_id']==990001


def test_null_is_review(store):
    ds=Api().player(990001,2026)
    ds['response'][0]['statistics'][0]['goals']['total']=None
    _,obs=normalize_dataset(ds)
    assert obs[0]['status']=='needs_review'


def profile_reply():
    return [{'id':990001,'profile':{'fullName':'Synthetic Player','birthDate':'01/01/1990',
            'club':{'current':'Augsburg','joinedAt':'01/01/2020'}},
            'transfers':[{'type':'transfer','transferDate':'Jan 1, 2020','from':'Synthetic Old Club','to':'Augsburg'}]}]


def scope_reply():
    teams=[{'id':15755,'name':'Augsburg'}]+[{'id':1000+i,'name':f'Synthetic Club {i}'} for i in range(17)]
    return {'league':{'id':67162,'season':2026},'groups':[{'standings':[{'team':t} for t in teams]}]}


def stats_reply():
    return [{'id':990001,'perCompetition':[
        {'club':'Augsburg','type':'national league','league':'Bundesliga','season':'26/27','gamesPlayed':5,'goals':2},
        {'club':'Augsburg','type':'national cup','league':'DFB Pokal','season':'26/27','gamesPlayed':20,'goals':19},
        {'club':'Augsburg','type':'national league','league':'Bundesliga','season':'25/26','gamesPlayed':40,'goals':30}]}]


def test_real_client_contract_only_selects_current_league_season(store):
    api,s=client(store,[Response(profile_reply()),Response(scope_reply()),Response(stats_reply())])
    ds=api.player(990001,2026,fresh=False)
    players,obs=normalize_dataset(ds)
    assert players[990001]['birth']['date']=='1990-01-01' and obs[0]['stats']=={'appearances':5,'goals':2}
    assert api.transfer_dates(990001)==['2020-01-01'] and len(s.calls)==3
    assert all(c[1]['allow_redirects'] is False for c in s.calls)


@pytest.mark.parametrize('kind', ['duplicate','wrong_season','null','wrong_club'])
def test_real_client_rejects_ambiguous_statistics(store,kind):
    stats=stats_reply()
    if kind=='duplicate': stats[0]['perCompetition'].append(deepcopy(stats[0]['perCompetition'][0]))
    elif kind=='wrong_season': stats[0]['perCompetition'][0]['season']='2026'
    elif kind=='null': stats[0]['perCompetition'][0]['goals']=None
    else: stats[0]['perCompetition'][0]['club']='Synthetic Other Club'
    api,_=client(store,[Response(profile_reply()),Response(scope_reply()),Response(stats)])
    if kind=='null':
        _,obs=normalize_dataset(api.player(990001,2026,fresh=False))
        assert obs[0]['status']=='needs_review'
    else:
        with pytest.raises(UpdateError) as exc: api.player(990001,2026,fresh=False)
        assert exc.value.code=='needs_review'


def test_missing_transfer_history_blocks_anchor_updates(store):
    row=profile_reply();del row[0]['transfers']
    api,_=client(store,[Response(row)])
    with pytest.raises(UpdateError) as exc: api.transfer_dates(990001)
    assert exc.value.code=='api_incomplete'


def test_partial_league_cannot_be_scope(store):
    scope=scope_reply();scope['groups'][0]['standings'].pop()
    api,_=client(store,[Response(scope)])
    with pytest.raises(UpdateError) as exc: api.scope(2026)
    assert exc.value.code=='api_incomplete'


def test_current_club_change_queues_history_without_making_transfer_edit(store):
    seed(store); wiki=ServerWiki()
    wiki.current=replace(wiki.current,text=wiki.current.text.replace('нынешний клуб = '+ANCHOR['career_wikitext'], 'нынешний клуб = [[Рома]]'))
    # Use a valid, separately identified old club reference.
    from wiki_stats import scheduler
    from unittest.mock import patch
    original=scheduler.reference_identity
    def identity(client,text):
        if text=='[[Рома]]': return {'title':'Рома (футбольный клуб)','qid':'Q2739'}
        return original(client,text)
    assert '[[Рома]]' in wiki.current.text
    with patch.object(scheduler,'reference_identity',identity):
        report=HighlightlyCycleRunner(store,Api(),wiki).run()
    assert report['prepared_count']==0 and not wiki.saved
    assert any(r['reason']=='transfer_chain_requires_mapping' for _,r in store.items('review'))


def test_legacy_approval_does_not_silently_enable_new_source(store,monkeypatch):
    report,_,_=prepare(store);legacy_community(store);monkeypatch.setenv('BRACKELBOT_PUBLICATION','1')
    plan=deserialize_plan(store.get('plans',report['plans'][0]['hash'])['plan'])
    with pytest.raises(UpdateError) as exc: PublicationPolicy(store,'manual').gate_plan(plan)
    assert exc.value.code=='provider_not_approved'


def test_publish_cannot_use_wrong_provider_for_recheck(store,monkeypatch):
    report,_,wiki=prepare(store);community(store);monkeypatch.setenv('BRACKELBOT_PUBLICATION','1')
    with pytest.raises(UpdateError) as exc:
        publish_plan(store,report['plans'][0]['hash'],wiki,PublicationPolicy(store,'manual'),source_api=ServerApi())
    assert exc.value.code=='source_unverified' and not wiki.saved


def test_disabled_credentials_have_no_usable_quota(store):
    api,_=client(store,[Response({},401)])
    with pytest.raises(UpdateError): api.request('/leagues')
    assert api.quota_status()['usable_remaining']==0


def test_missing_headers_use_local_budget_not_infinite_requests(store):
    api,s=client(store,[Response(listing([]),headers={})],entries=[{'key':'SYNTHETIC','daily_budget':1}])
    api.request('/leagues')
    assert api.quota_status()['keys'][0]['remaining'] is None
    with pytest.raises(UpdateError) as exc: api.request('/players')
    assert exc.value.code=='api_quota_exhausted' and len(s.calls)==1


def test_cli_quota_report_needs_no_network_and_has_no_secret(tmp_path,monkeypatch,capsys):
    from wiki_stats.server_cli import main
    monkeypatch.setenv('HIGHLIGHTLY_KEYS',json.dumps([{'key':f'SYNTHETIC-{i}'} for i in range(5)]))
    result=main(['--state',str(tmp_path/'cli.sqlite'),'--provider','highlightly','api-quotas'])
    output=capsys.readouterr().out
    assert result==0 and 'SYNTHETIC' not in output
    value=json.loads(output)
    assert len(value['keys'])==5 and value['usable_remaining']==100 and value['keys'][-1]['slot']==5


def test_new_source_credential_fields_are_rejected_in_state(store):
    for field in ['HIGHLIGHTLY_API_KEY','HIGHLIGHTLY_KEYS','x-rapidapi-key']:
        with pytest.raises(UpdateError): store.put('review','bad',{field:'SYNTHETIC'})
