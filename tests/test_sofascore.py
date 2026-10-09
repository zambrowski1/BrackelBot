# SPDX-License-Identifier: MIT
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
import re

import pytest

from wiki_stats.api_provider import ProviderStore
from wiki_stats.errors import UpdateError
from wiki_stats.entity_resolver import ResolvedEntity
from wiki_stats.sofascore_client import SofascoreClient
from wiki_stats.sofascore_cycle import confirmed_match_date, prepare_player
from wiki_stats.structural_editor import execute_operation
from tests.stage23_helpers import MINI, AUGSBURG, FakeResolver, snapshot, operation


class Store:
    def __init__(self): self.data = {}
    def get(self, bucket, key, default=None): return self.data.get((bucket, key), default)
    def put(self, bucket, key, value): self.data[bucket, key] = deepcopy(value)


class Session:
    def __init__(self, responses): self.responses, self.calls = responses, []
    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


def response(data=None, status=200, remaining=999, reset=3600):
    return SimpleNamespace(status_code=status,
        headers={'X-RateLimit-Requests-Limit': '1000', 'X-RateLimit-Requests-Remaining': str(remaining),
                 'X-RateLimit-Requests-Reset': str(reset)}, json=lambda: data or {'ok': True})


def test_quota_cache_reservation_and_reset():
    store = Store();clock = [100]
    session = Session([response(remaining=0), response(remaining=999)])
    api = SofascoreClient(store, key='SYNTHETIC', session=session, clock=lambda: clock[0])
    api.request('/search', {'query': 'x'})
    assert api.quota_status()['remaining'] == 0
    assert api.request('/search', {'query': 'x'}) == {'ok': True}
    with pytest.raises(UpdateError, match='Квота'):
        api.request('/search', {'query': 'y'})
    clock[0] = 4000
    api.request('/search', {'query': 'y'})
    assert api.quota_status()['remaining'] == 999
    assert len(session.calls) == 2
    assert session.calls[0][1]['allow_redirects'] is False


def test_http_failure_is_not_retried_or_cached():
    store = Store();session = Session([response(status=502)])
    api = SofascoreClient(store, key='SYNTHETIC', session=session)
    with pytest.raises(UpdateError) as exc: api.request('/search', {'query': 'x'})
    assert exc.value.code == 'api_unavailable'
    assert len(session.calls) == 1
    assert not any(b == 'cache' for b, _ in store.data)


def test_provider_isolates_all_state_except_switch():
    base = Store();ss = ProviderStore(base, 'sofascore');hl = ProviderStore(base, 'highlightly')
    ss.put('players', 1, 'sofa');hl.put('players', 1, 'highlight')
    ss.put('policy', 'kill_switch', True)
    assert ss.get('players', 1) == 'sofa'
    assert hl.get('players', 1) == 'highlight'
    assert hl.get('policy', 'kill_switch') is True


def match(mid=1, day='2026-10-08', tournament=35, minutes=90, finished=True):
    return {'id': mid, 'start_time': day+'T12:00:00Z', 'tournament': {'id': tournament},
        'season': {'id': 26}, 'player': {'team_id': 2600, 'statistics': {'minutes_played': minutes}},
        'is_finished': finished, 'status': {'type': 'finished' if finished else 'notstarted'},
        'home_team': {'id': 2600, 'sport': {'slug': 'football'}},
        'away_team': {'id': 99, 'sport': {'slug': 'football'}}}


def test_dates_exclude_friendlies_future_bench_and_incomplete_pages():
    rows = [match(day='2026-10-02'), match(2, tournament=853), match(3, minutes=0),
            match(4, day='2026-10-10', finished=False), match(5, day='2026-10-11')]
    history = {'complete': True, 'matches': rows}
    now = datetime(2026, 10, 9, tzinfo=timezone.utc)
    assert confirmed_match_date(history, {(2600, 35, 26)}, now) == '2026-10-02'
    assert confirmed_match_date({**history, 'complete': False}, {(2600, 35, 26)}, now) is None
    rows[0]['round'] = {'name': 'Relegation playoffs'}
    assert confirmed_match_date(history, {(2600, 35, 26)}, now) is None


def test_pagination_repeated_page_is_not_complete():
    data = {'player_id': 1, 'page': 1, 'matches': [match()], 'has_next_page': True}
    session = Session([response(data), response({**data, 'page': 2, 'has_next_page': False})])
    api = SofascoreClient(Store(), key='SYNTHETIC', session=session)
    with pytest.raises(UpdateError) as exc: api.matches(1)
    assert exc.value.code == 'incomplete_matches'


def test_shirt_number_preserves_comment_and_checks_club():
    op = operation('shirt', 'update_shirt_number',
        entity={'name': 'Аугсбург', 'kind': 'club', 'wikidata_id': 'Q15755', 'wikitext': AUGSBURG},
        payload={'expected_number': 2, 'number': 3})
    result, status = execute_operation(MINI, 'Кальвин Браккельман', op, FakeResolver())
    assert status == 'ready' and '| номер = 3<!-- сохранить -->' in result
    op['entity']['wikidata_id'] = 'Q41420'
    with pytest.raises(UpdateError) as exc: execute_operation(MINI, 'Кальвин Браккельман', op, FakeResolver())
    assert exc.value.code == 'current_club_inconsistent'


class Wiki:
    def get_page_identity(self, title):
        qid = 'Q114406918' if title == 'Игрок' else ('Q15755' if 'Аугсбург' in title else 'Q13174')
        return {'qid': qid, 'title': title}
    def get_wikidata_entity(self, qid):
        return {'sitelinks': {'ruwiki': {'title': 'Игрок'}}, 'claims': {
            'P31': [{'mainsnak': {'snaktype': 'value', 'datavalue': {'value': {'id': 'Q5'}}}}],
            'P569': [{'mainsnak': {'snaktype': 'value', 'datavalue': {
                'value': {'time': '+1999-08-22T00:00:00Z', 'precision': 11}}}}]}}


class Resolver(FakeResolver):
    client = Wiki()
    def resolve(self, entity):
        return super().resolve({"wikidata_id": "Q15755", **entity})


class API:
    def details(self, pid):
        return {'id': pid, 'date_of_birth': '1999-08-22', 'jersey_number': 3,
            'team': {'id': 2600, 'name': 'Аугсбург', 'sport': {'slug': 'football'}, 'is_national': False}}
    def seasons(self, pid):
        return {'player_id': pid, 'tournament_seasons': [{'tournament': {'id': 35},
            'seasons': [{'id': 26, 'year': '26/27'}]}]}
    def statistics(self, pid, tid, sid, part):
        return {'player_id': pid, 'tournament_id': tid, 'season_id': sid, 'type': part,
            'team': self.details(pid)['team'], 'statistics': {'appearances': 5, 'goals': 2}}
    def transfers(self, pid): return {'player_id': pid, 'transfers': []}
    def matches(self, pid, max_pages=12): return {'complete': True, 'matches': [match()]}
    def quota_status(self): return {'remaining': 999}


def prepare(text=MINI, api=None):
    text = text.replace('| имя =', '| дата рождения = 22.08.1999\n| имя =').replace('2025/26', '2026/27')
    return prepare_player(snapshot(text), {'api_id': 1, 'qid': 'Q114406918', 'title': 'Игрок',
        'wiki_name': 'Кальвин Браккельман', 'birth_date': '1999-08-22'}, api or API(), Wiki(), Resolver())


def test_full_pipeline_updates_card_table_totals_number_leaves_national():
    pkg, plan, evidence = prepare()
    assert all(c.status in {'ready', 'already_applied'} for c in plan.changes)
    assert '|2026—{{н.в.}}|'+AUGSBURG+'| 5 (2)<ref name="x"/>' in plan.preview
    assert '{{КлСтат/Сезон|2026/27|5|2|0|0|0|0|0|0}}' in plan.preview
    assert '{{КлСтат/Итого|5|2|0|0|0|0|0|0}}' in plan.preview
    assert '| номер = 3<!-- сохранить -->' in plan.preview
    assert '| обновление данных о сборной = 04.10.2026' in plan.preview
    assert evidence['confirmed_match_date'] == '2026-10-08'
    assert any(r['code'] == 'global_date_incomplete_coverage' for r in evidence['review'])


def test_missing_statistics_never_becomes_zero():
    api = API()
    api.statistics = lambda *a: {'player_id': 1, 'tournament_id': 35, 'season_id': 26,
        'type': 'regularSeason', 'team': api.details(1)['team'], 'statistics': {'goals': 0}}
    _, plan, evidence = prepare(api=api)
    assert '{{КлСтат/Сезон|2026/27|4|1|' in plan.preview
    assert any(r['code'] == 'incomplete_statistics' for r in evidence['review'])


def test_older_match_date_never_regresses_card_date():
    api = API();api.matches = lambda *a, **k: {'complete': True, 'matches': [match(day='2026-09-19')]}
    _, plan, evidence = prepare(api=api)
    assert '| обновление данных о клубе = 04.10.2026' in plan.preview
    assert any(r['code'] == 'date_evidence_older' for r in evidence['review'])


def test_birth_mismatch_blocks_the_entire_cycle():
    api = API();profile = api.details(1);profile['date_of_birth'] = '2000-01-01'
    api.details = lambda _: profile
    with pytest.raises(UpdateError) as exc: prepare(api=api)
    assert exc.value.code == 'identity_mismatch'


def test_confirmed_date_updates_card_only_after_complete_career_coverage():
    text = MINI.replace('  |2010—2012|{{Флаг Германии|20px}} [[Вердер (футбольный клуб)|Вердер]]|10 (1)\n', '')
    _, plan, evidence = prepare(text)
    assert '| обновление данных о клубе = 08.10.2026' in plan.preview
    assert '| обновление данных о сборной = 04.10.2026' in plan.preview
    assert not any(r['code']=='global_date_incomplete_coverage' for r in evidence['review'])


def test_table_date_updates_marker_and_footer_but_no_other_date():
    text = MINI.replace('{{КлСтат|', '{{обновлено|2026-10-04}}\n{{КлСтат|')
    text += ": ''Данные в таблице актуальны по состоянию на 4 октября 2026\u00a0года.''\n"
    op = operation('table-date', 'update_table_date', entity={'name':'КлСтат','wikitext':'{{КлСтат}}'},
                   target={'structure':'club_table'}, payload={'expected_value':'2026-10-04'})
    result, _ = execute_operation(text, 'Кальвин Браккельман', op)
    assert '{{обновлено|2026-10-08}}' in result
    assert 'на 8 октября 2026\u00a0года' in result
    assert '| обновление данных о клубе = 04.10.2026' in result


def test_table_date_cannot_regress():
    text = MINI.replace('{{КлСтат|', '{{обновлено|2026-10-09}}\n{{КлСтат|')
    op = operation('table-date', 'update_table_date', entity={'name':'КлСтат','wikitext':'{{КлСтат}}'},
                   target={'structure':'club_table'}, payload={'expected_value':'2026-10-09'})
    with pytest.raises(UpdateError): execute_operation(text, 'Кальвин Браккельман', op)


def test_transfer_card_and_shirt_number_share_one_atomic_diff():
    old_club = '{{Флаг Германии|20px}} [[Падерборн 07|Падерборн]]'
    text = MINI.replace('| нынешний клуб = '+AUGSBURG, '| нынешний клуб = '+old_club)
    text = re.sub(r'  \|2010—2012\|.*?\n', '', text)
    text = text.replace('|2026—{{н.в.}}|'+AUGSBURG+'| 4 (1)', '|2023—{{н.в.}}|'+old_club+'| 19 (1)')
    text = text.replace('| имя =', '| дата рождения = 22.08.1999\n| имя =').replace('2025/26', '2026/27')
    class TransferAPI(API):
        def seasons(self, pid):
            return {'player_id':pid,'tournament_seasons':[
                {'tournament':{'id':35},'seasons':[{'id':26,'year':'26/27'}]},
                {'tournament':{'id':44},'seasons':[{'id':i,'year':f'{i}/{i+1}'} for i in (23,24,25)]}]}
        def statistics(self, pid, tid, sid, part):
            r = super().statistics(pid,tid,sid,part)
            if tid==44:
                r['team']={**r['team'],'id':2561,'name':'Падерборн'}
                games,goals={23:(13,0),24:(22,3),25:(24,2)}[sid]
                r['statistics']={'appearances':games,'goals':goals}
            return r
        def transfers(self,pid):
            return {'player_id':pid,'transfers':[{'transfer_date':'2026-07-01',
                'transfer_from':{'id':2561,'name':'Падерборн'},'transfer_to':{'id':2600,'name':'Аугсбург'}}]}
    class TransferResolver(Resolver):
        def resolve(self, ent):
            if ent['name']=='Падерборн': return ResolvedEntity('Падерборн 07','Q500','Германии','Падерборн','club')
            return super().resolve(ent)
    class TransferWiki(Wiki):
        def get_page_identity(self,title):
            if title=='Падерборн 07': return {'qid':'Q500','title':title}
            return super().get_page_identity(title)
    _,plan,_ = prepare_player(snapshot(text), {'api_id':1,'qid':'Q114406918','title':'Игрок',
        'wiki_name':'Кальвин Браккельман','birth_date':'1999-08-22'}, TransferAPI(),TransferWiki(),TransferResolver())
    assert all(c.status in {'ready','already_applied'} for c in plan.changes)
    assert '|2023—2026|'+old_club+'| 59 (5)' in plan.preview
    assert '|2026—{{н.в.}}|'+AUGSBURG+'|5 (2)' in plan.preview
    assert '| нынешний клуб = '+AUGSBURG in plan.preview
    assert '| номер = 3<!-- сохранить -->' in plan.preview
    assert len([g for g in plan.groups if g.status=='ready'])==1


def test_overall_fallback_requires_complete_non_playoff_match_count():
    api = API()
    original = api.statistics
    def stats(pid,tid,sid,part):
        if part=='regularSeason': raise UpdateError('api_not_found','404')
        return original(pid,tid,sid,part)
    api.statistics = stats
    rows = [match(i) for i in range(1,6)]
    api.matches = lambda *a, **k: {'complete':True,'matches':rows}
    _, plan, report = prepare(api=api)
    assert '{{КлСтат/Сезон|2026/27|5|2|' in plan.preview
    rows[0]['round']={'cup_round_type':1}
    _, plan, report = prepare(api=api)
    assert '{{КлСтат/Сезон|2026/27|4|1|' in plan.preview
    assert any(r['code']=='overall_league_unproven' for r in report['review'])


def test_pipeline_can_create_missing_partial_section_without_inventing_cup_zeros():
    text = MINI[:MINI.index('{{КлСтат|')]
    _,plan,report = prepare(text)
    assert all(c.status in {'ready','already_applied'} for c in plan.changes)
    assert '== Статистика выступлений ==' in plan.preview
    assert '{{КлСтат|Чемпионат}}' in plan.preview
    assert 'полный итог карьеры не заявляется' in plan.preview
    assert '{{КлСтат/Сезон|2026/27|5|2}}' in plan.preview
