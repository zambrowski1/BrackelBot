# SPDX-License-Identifier: MIT
"""Invented API IDs and numbers; no live provider or Wikipedia writes."""
from copy import deepcopy
from dataclasses import replace
import pytest
from server_helpers import ServerApi, ServerWiki, MAPPING, TITLE, player_row, community
from wiki_stats.api_football_client import utcnow
from wiki_stats.state_store import StateStore
from wiki_stats.scheduler import CycleRunner, publish_plan
from wiki_stats.operator_review import deserialize_plan, approve_stored
from wiki_stats.publication_policy import PublicationPolicy
from wiki_stats.transfer_recovery import missing_stints
from wiki_stats.errors import UpdateError

CLUBS = {
    100: ('Roma', 'Q2739', 'Рома (футбольный клуб)', 135),
    200: ('Sporting CP', 'Q75729', 'Спортинг (футбольный клуб, Лиссабон)', 94),
    300: ('Bayern Munich', 'Q15789', 'Бавария (футбольный клуб)', 78),
}
ROMA = '{{Флаг Италии|20px}} [[Рома (футбольный клуб)|Рома]]'
SPORTING = '{{Флаг Португалии|20px}} [[Спортинг (футбольный клуб, Лиссабон)|Спортинг (Лиссабон)]]'
BAYERN = '{{Флаг Германии|20px}} [[Бавария (футбольный клуб)|Бавария]]'
TEXT = '''{{Футболист
| имя = Кальвин Браккельман
| нынешний клуб = ''' + ROMA + '''
| клубы = {{Спортивная карьера
 |2024—{{н.в.}}|''' + ROMA + '''|10 (1)<ref name="old"/><!-- сохранить -->
 }}
}}
Текст и <ref name="old">Старый источник</ref>.
'''


def event(day, origin, target, kind='Free'):
    return {'date': day, 'type': kind, 'teams': {'out': {'id': origin}, 'in': {'id': target}}}


class TransferApi(ServerApi):
    def __init__(self):
        super().__init__([player_row(4, 1, tid=300)])
        self.history = [event('2024-07-01', 999, 100), event('2025-07-01', 100, 200), event('2026-07-01', 200, 300)]
        self.numbers = {2024: (100, 20, 3), 2025: (200, 30, 6), 2026: (300, 4, 1)}
        self.fresh_calls = []

    def teams(self, season):
        return {'data': {'response': [{'team': {'id': 300, 'name': 'Bayern Munich', 'national': False}}]}}

    def transfers(self, player_id, fresh=False):
        self.fresh_calls.append(('transfers', fresh))
        return {'data': {'response': [{'player': {'id': player_id}, 'transfers': deepcopy(self.history)}]}, 'fetched_at': utcnow()}

    def team_leagues(self, team_id, season, fresh=False):
        self.fresh_calls.append(('leagues', fresh))
        return {'fetched_at': utcnow(), 'data': {'response': [{'league': {'id': CLUBS[team_id][3], 'type': 'League'},
                'seasons': [{'year': season, 'start': f'{season}-08-01', 'end': f'{season+1}-05-31', 'coverage': {'players': True}}]}]}}

    def player_statistics(self, player_id, season, fresh=False):
        self.fresh_calls.append(('statistics', fresh))
        tid, games, goals = self.numbers[season]
        row = player_row(games, goals, pid=player_id, tid=tid)
        row['statistics'][0]['league'] = {'id': CLUBS[tid][3], 'season': season}
        cup = deepcopy(row['statistics'][0]); cup['league']['id'] = 9999; cup['goals']['total'] = 999
        row['statistics'].append(cup)
        return {'data': {'response': [row]}, 'fetched_at': utcnow()}


class TransferWiki(ServerWiki):
    def __init__(self):
        super().__init__()
        self.current = replace(self.current, text=TEXT)

    def get_wikidata_entity(self, qid):
        club = next((c for c in CLUBS.values() if c[1] == qid), None)
        if club:
            return {'sitelinks': {'ruwiki': {'title': club[2]}}, 'claims': {}}
        return super().get_wikidata_entity(qid)

    def get_page_identity(self, title, namespace=0):
        club = next((c for c in CLUBS.values() if c[2] == title), None)
        if club:
            return {'title': title, 'qid': club[1]}
        return super().get_page_identity(title, namespace)


@pytest.fixture
def setup(tmp_path):
    store = StateStore(tmp_path/'state.sqlite')
    mapping = deepcopy(MAPPING); mapping['club_ids'] = [100]
    store.put('players', 990001, mapping)
    for tid, (name, qid, title, _) in CLUBS.items():
        store.put('clubs', tid, {'api_id': tid, 'name': name, 'qid': qid, 'title': title, 'verified_at': utcnow()})
    yield store, TransferApi(), TransferWiki()
    store.close()


def prepare(setup):
    store, api, wiki = setup
    report = CycleRunner(store, api, wiki).run()
    assert report['prepared_count'] == 1, report
    key = report['plans'][0]['hash']
    return report, key, deserialize_plan(store.get('plans', key)['plan'])


def allow_transfer(store, key):
    community(store)
    approval = store.get('policy', 'community_approval')
    approval['operations'] += ['update_career_period', 'add_club', 'update_current_club']
    store.put('policy', 'community_approval', approval)
    for change in store.get('plans', key)['plan']['changes']:
        approve_stored(store, key, change['id'])


def test_stale_card_gets_entire_chain_with_links_and_league_totals(setup):
    store, api, wiki = setup
    report, key, plan = prepare(setup)
    assert len(plan.groups) == 1 and len(plan.changes) == 5
    assert '2024—2025|' + ROMA + '|20 (3)' in plan.preview
    assert '2025—2026|' + SPORTING + '|30 (6)' in plan.preview
    assert '2026—{{н.в.}}|' + BAYERN + '|4 (1)' in plan.preview
    assert '| нынешний клуб = ' + BAYERN in plan.preview
    assert '<ref name="old"/><!-- сохранить -->' in plan.preview
    assert '999' not in plan.preview and report['plans'][0]['manual_only'] and not wiki.saved
    repeated = CycleRunner(store, api, wiki).run()
    assert repeated['plans'][0]['hash'] == key and len(store.items('plans')) == 1


def test_approved_chain_rechecks_api_and_publishes_once(setup):
    store, api, wiki = setup
    _, key, plan = prepare(setup); allow_transfer(store, key)
    publish_plan(store, key, wiki, PublicationPolicy(store, 'manual', enabled=lambda: True), source_api=api)
    assert len(wiki.saved) == 1 and wiki.current.text == plan.preview
    assert ('statistics', True) in api.fresh_calls and ('leagues', True) in api.fresh_calls
    repeated = CycleRunner(store, api, wiki).run()
    assert not repeated['plans'] and len(wiki.saved) == 1
    assert store.get('anchors','990001:300:2026')['career']=={'appearances':4,'goals':1}
    api.rows=[player_row(5,2,tid=300)]
    following=CycleRunner(store,api,wiki).run()
    assert following['prepared_count']==1
    next_plan=deserialize_plan(store.get('plans',following['plans'][0]['hash'])['plan'])
    assert len(next_plan.changes)==1 and '|5 (2)' in next_plan.preview


def test_automatic_mode_keeps_transfer_in_review(setup, monkeypatch):
    store, api, wiki = setup
    monkeypatch.setenv('BRACKELBOT_PUBLICATION', '1'); community(store)
    report = CycleRunner(store, api, wiki, mode='automatic').run()
    assert report['prepared_count'] == 1 and report['published_count'] == 0 and not wiki.saved


@pytest.mark.parametrize('problem', ['missing_join', 'broken', 'loan', 'unknown_type', 'missing_club', 'null', 'table'])
def test_uncertain_chain_leaves_whole_card_unchanged(setup, problem):
    store, api, wiki = setup
    if problem == 'missing_join': api.history.pop(0)
    elif problem == 'broken': api.history[1]['teams']['out']['id'] = 999
    elif problem == 'loan': api.history[1]['type'] = 'Loan'
    elif problem == 'unknown_type': api.history[1]['type'] = 'N/A'
    elif problem == 'missing_club':
        club = store.get('clubs', 200); club['qid'] = 'Q999'; store.put('clubs', 200, club)
    elif problem == 'null': api.numbers[2025] = (200, None, 6)
    elif problem == 'table': wiki.current = replace(wiki.current, text=TEXT + '{{КлСтат|Чемпионат}}')
    report = CycleRunner(store, api, wiki).run()
    assert not report['plans'] and report['review_count'] and not wiki.saved


@pytest.mark.parametrize('problem', ['history', 'statistics', 'roster'])
def test_changed_source_clears_transfer_approval(setup, problem):
    store, api, wiki = setup
    _, key, _ = prepare(setup); allow_transfer(store, key)
    if problem == 'history': api.history[1]['date'] = '2025-07-02'
    elif problem == 'statistics': api.numbers[2025] = (200, 31, 6)
    else: api.squads = lambda *args, **kwargs: {'data': {'response': []}}
    with pytest.raises(UpdateError):
        publish_plan(store, key, wiki, PublicationPolicy(store, 'manual', enabled=lambda: True), source_api=api)
    item = store.get('plans', key)
    assert item['status'] == 'stale' and not wiki.saved
    assert all(c['decision'] == 'pending' for c in item['plan']['changes'])


def test_return_to_previous_club_cannot_reuse_season_aggregates():
    history = [event('2024-07-01', 999, 100), event('2025-07-01', 100, 200), event('2026-07-01', 200, 100)]
    with pytest.raises(UpdateError): missing_stints(history, 100, 2024, 100, '2026-10-09')


@pytest.mark.parametrize('problem', ['no_calendar','coverage','season','birth','timestamp','unknown_kind','same_day','current_mismatch'])
def test_bad_history_or_season_evidence_prevents_entire_proposal(setup,problem):
    store,api,wiki=setup
    if problem=='no_calendar':
        api.team_leagues=lambda *args,**kwargs: {'fetched_at':utcnow(),'data':{'response':[]}}
    elif problem in {'coverage','season'}:
        original=api.team_leagues
        def calendar(*args,**kwargs):
            value=original(*args,**kwargs)
            season=value['data']['response'][0]['seasons'][0]
            if problem=='coverage': season['coverage']['players']=False
            else: season['year']-=1
            return value
        api.team_leagues=calendar
    elif problem in {'birth','timestamp'}:
        original=api.player_statistics
        def statistics(*args,**kwargs):
            value=original(*args,**kwargs)
            if problem=='birth': value['data']['response'][0]['player']['birth']={'date':'1991-01-01'}
            else: value['fetched_at']='2099-01-01T00:00:00Z'
            return value
        api.player_statistics=statistics
    elif problem=='unknown_kind': api.history[1]['type']='Unknown'
    elif problem=='same_day': api.history[2]['date']=api.history[1]['date']
    else: api.rows=[player_row(9,1,tid=300)]
    report=CycleRunner(store,api,wiki).run()
    assert not report['plans'] and report['review_count'] and not wiki.saved


def test_winter_transfer_counts_both_overlapping_seasons(setup):
    store,api,wiki=setup
    api.history[1]['date']='2025-01-01'
    original=api.player_statistics
    def statistics(player_id,season,**kwargs):
        value=original(player_id,season,**kwargs)
        if season==2024:
            stint=player_row(10,2,tid=200)['statistics'][0]
            stint['league']={'id':94,'season':2024}
            value['data']['response'][0]['statistics'].append(stint)
        return value
    api.player_statistics=statistics
    _,_,plan=prepare(setup)
    assert '2025—2026|'+SPORTING+'|40 (8)' in plan.preview


@pytest.mark.parametrize('national', [False,True])
def test_historical_club_cli_verifies_api_id_outside_current_scope(tmp_path,monkeypatch,national):
    from wiki_stats import server_cli
    path=tmp_path/'cli.sqlite'
    class Api:
        def team(self,tid):
            assert tid==100
            return {'data':{'response':[{'team':{'id':100,'name':'Roma','national':national}}]}}
    monkeypatch.setattr(server_cli,'ApiFootballClient',lambda store:Api())
    monkeypatch.setattr(server_cli,'WikiClient',TransferWiki)
    result=server_cli.main(['--state',str(path),'club-confirm','100','--historical','--qid','Q2739',
                           '--title',CLUBS[100][2],'--confirm'])
    store=StateStore(path)
    try:
        assert result==(2 if national else 0)
        assert bool(store.get('clubs',100)) is not national
    finally: store.close()
