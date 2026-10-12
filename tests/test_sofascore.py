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
    with pytest.raises(UpdateError, match='Квот'):
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


@pytest.mark.parametrize('tid,league_title', [(1079,'Региональная лига «Юго-Запад»'),(1085,'Региональная лига «Бавария»'),(170,'Чемпионат Хорватии по футболу|Первая лига')])
def test_regional_season_is_collected_and_builds_its_own_league_table(tid,league_title):
    class Regional(API):
        def seasons(self,pid):
            return {'player_id':pid,'statistics_types':{str(tid):{'26':['overall']}},
                'tournament_seasons':[{'tournament':{'id':tid},'seasons':[{'id':26,'year':'26/27'}]}]}
        def statistics(self,pid,tournament,sid,part):
            assert tournament==tid and part=='overall'
            return super().statistics(pid,tournament,sid,part)
        def matches(self,pid,max_pages=12):
            return {'complete':True,'matches':[match(i,tournament=tid) for i in range(1,6)]}
    _,plan,evidence=prepare(MINI.split('{{КлСтат|')[0],Regional())
    assert not any(r['code']=='competition_unmapped' for r in evidence['review'])
    assert '[['+league_title+']]' in plan.preview
    assert '{{КлСтат/Сезон|2026/27|5|2}}' in plan.preview


def test_failed_reserve_league_does_not_hide_verified_primary_league():
    class ReserveFailure(API):
        def seasons(self,pid):
            result=super().seasons(pid)
            result['tournament_seasons'].append({'tournament':{'id':1085},'seasons':[{'id':99,'year':'26/27'}]})
            return result
        def statistics(self,pid,tid,sid,part):
            if tid==1085:raise UpdateError('club_ambiguous','Reserve team not specified')
            return super().statistics(pid,tid,sid,part)
    _,plan,evidence=prepare(api=ReserveFailure())
    assert any(r['code']=='club_ambiguous' and r['tournament_id']==1085 for r in evidence['review'])
    assert any(c.operation.get('competition',{}).get('category')=='Чемпионат' and c.status=='ready' for c in plan.changes)

def test_multiple_missing_seasons_use_newly_inserted_anchor():
    class ThreeSeasons(API):
        def seasons(self,pid):
            return {'player_id':pid,'statistics_types':{'35':{str(y):['overall','home','away'] for y in (24,25,26)}},
                'tournament_seasons':[{'tournament':{'id':35},'seasons':[{'id':y,'year':f'{y}/{y+1}'} for y in (24,25,26)]}]}
    card=MINI.split('{{КлСтат|')[0]
    text=card+'{{КлСтат|Чемпионат}}\n'+\
        '{{КлСтат/Клуб|'+AUGSBURG+'|сезоны=1}}\n{{КлСтат/Лига|[[Чемпионат Германии по футболу|Бундеслига]]|сезоны=1}}\n'+\
        '{{КлСтат/Сезон|2024/25|4|1}}\n{{КлСтат/ИтогоЗаКлуб||4|1}}\n{{КлСтат/Итого|4|1}}'
    pkg,plan,report=prepare(text,ThreeSeasons())
    assert all(c.status in ('ready','already_applied') for c in plan.changes)
    assert [r.season for r in __import__('wiki_stats.wikitext_parser',fromlist=['WikitextParser']).WikitextParser(plan.preview).club_table().seasons]==['2024/25','2025/26','2026/27']


def prepare(text=MINI, api=None):
    text = text.replace('| имя =', '| дата рождения = 22.08.1999\n| имя =').replace('2025/26', '2026/27')
    return prepare_player(snapshot(text), {'api_id': 1, 'qid': 'Q114406918', 'title': 'Игрок',
        'wiki_name': 'Кальвин Браккельман', 'birth_date': '1999-08-22'}, api or API(), Wiki(), Resolver(),
        now=datetime(2026,10,9,tzinfo=timezone.utc))


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


def test_football_career_alias_is_migrated_to_canonical_template():
    _, plan, _ = prepare(MINI.replace('спортивная карьера', 'футбольная карьера'))
    assert '{{футбольная карьера' not in plan.preview
    assert any(c.operation['type']=='migrate_career_template' for c in plan.changes)
    assert all(c.status in {'ready', 'already_applied'} for c in plan.changes)


def test_negative_career_without_goalkeeper_position_stops_collection():
    with pytest.raises(UpdateError) as exc:
        prepare(MINI.replace('4 (1)', '4 (-1)'))
    assert exc.value.code == 'goal_metric_mismatch'


def test_sandbox_birthday_allows_single_digit_day_and_month():
    from dataclasses import replace
    class BirthdayWiki(Wiki):
        def get_wikidata_entity(self, qid):
            result = super().get_wikidata_entity(qid)
            result['claims']['P569'][0]['mainsnak']['datavalue']['value']['time'] = '+1999-08-02T00:00:00Z'
            return result
    class BirthdayAPI(API):
        def details(self, pid):
            return {**super().details(pid), 'date_of_birth':'1999-08-02'}
    text=MINI.replace('| имя =','| дата рождения = 2.8.1999\n| имя =').replace('2025/26','2026/27')
    _, plan, _ = prepare_player(replace(snapshot(text),title='Участник:Zambrowski/testbot4',namespace=2),
        {'api_id':1,'qid':'Q114406918','title':'Игрок','wiki_name':'Кальвин Браккельман','birth_date':'1999-08-02'},
        BirthdayAPI(), BirthdayWiki(), Resolver(), now=datetime(2026,10,9,tzinfo=timezone.utc))
    assert plan.diff


def test_missing_season_keeps_incomplete_table_out_of_publishable_plans():
    class PartialAPI(API):
        def seasons(self,pid):
            result=super().seasons(pid)
            result['tournament_seasons'][0]['seasons'].append({'id':25,'year':'25/26'})
            return result
    pkg,plan,evidence=prepare(api=PartialAPI())
    assert plan.diff and not plan.publishable
    assert any('2025/26' in warning and 'полнота' in warning for warning in plan.warnings)
    assert any(r['code']=='new_row_needs_all_categories' for r in evidence['review'])
    missing = next(r for r in evidence['review'] if r['code']=='new_row_needs_all_categories')
    assert missing['league_status']=='confirmed'
    assert missing['missing_categories']==['Национальный кубок','Еврокубки','Стыковые матчи']
    assert all(category in ' '.join(plan.warnings) for category in missing['missing_categories'])
    assert all(c.source_verification=='existing_table_arithmetic' for c in plan.changes if c.operation['type']=='update_totals')
    from wiki_stats.change_planner import plan_article
    imported=plan_article(plan.snapshot,pkg['articles'][0],'1.1',Resolver())
    assert imported.warnings==plan.warnings and not imported.publishable
    _,migrated,_=prepare(MINI.replace('спортивная карьера','футбольная карьера'),api=PartialAPI())
    assert migrated.diff and not migrated.publishable
    assert any('2025/26' in warning for warning in migrated.warnings)


def test_missing_statistics_never_becomes_zero():
    api = API()
    api.statistics = lambda *a: {'player_id': 1, 'tournament_id': 35, 'season_id': 26,
        'type': 'regularSeason', 'team': api.details(1)['team'], 'statistics': {'goals': 0}}
    _, plan, evidence = prepare(api=api)
    assert '{{КлСтат/Сезон|2026/27|4|1|' in plan.preview
    assert any(r['code'] == 'incomplete_statistics' for r in evidence['review'])


def test_unavailable_season_does_not_stop_independent_season_collection():
    class OneFailedSeason(API):
        calls = None
        def __init__(self): self.calls = []
        def seasons(self, pid):
            result = super().seasons(pid)
            result['tournament_seasons'][0]['seasons'].append({'id':25,'year':'25/26'})
            return result
        def statistics(self, pid, tid, sid, part):
            self.calls.append(sid)
            if sid == 26:
                raise UpdateError('api_unavailable', 'One unavailable season')
            return super().statistics(pid, tid, sid, part)
    api = OneFailedSeason()
    _, _, evidence = prepare(api=api)
    assert api.calls == [26,25]
    assert evidence['covered_statistics'] == 1
    assert any(r['code']=='api_unavailable' and r['season']=='2026/27' for r in evidence['review'])


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
        'wiki_name':'Кальвин Браккельман','birth_date':'1999-08-22'}, TransferAPI(),TransferWiki(),TransferResolver(),
        now=datetime(2026,10,9,tzinfo=timezone.utc))
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


@pytest.mark.parametrize('types,accepted', [
    (['overall','home','away'],True), (['overall'],True),
    (['overall','playoffs'],False), (['home','away'],False), (None,False)])
def test_advertised_football_scope_does_not_need_complete_career_history(types, accepted):
    class AdvertisedAPI(API):
        def seasons(self,pid):
            result=super().seasons(pid)
            result['statistics_types']={'35':{'26':types}}
            return result
        def statistics(self,pid,tid,sid,part):
            if part=='regularSeason':raise UpdateError('api_not_found','404')
            return super().statistics(pid,tid,sid,part)
        def matches(self,*args,**kwargs):return {'complete':False,'matches':[match()]}
    _,plan,report=prepare(api=AdvertisedAPI())
    assert ('{{КлСтат/Сезон|2026/27|5|2|' in plan.preview) == accepted
    assert report['covered_statistics'] == (1 if accepted else 0)


def test_declared_scope_cannot_override_observed_playoff():
    class PlayoffAPI(API):
        def seasons(self,pid):
            return {**super().seasons(pid),'statistics_types':{'35':{'26':['overall','home','away']}}}
        def matches(self,*args,**kwargs):
            row=match();row['round']={'cup_round_type':1}
            return {'complete':False,'matches':[row]}
    _,plan,report=prepare(api=PlayoffAPI())
    assert '{{КлСтат/Сезон|2026/27|4|1|' in plan.preview
    assert any(r['code']=='overall_league_unproven' for r in report['review'])


def test_pipeline_can_create_missing_partial_section_without_inventing_cup_zeros():
    text = MINI[:MINI.index('{{КлСтат|')]
    _,plan,report = prepare(text)
    assert all(c.status in {'ready','already_applied'} for c in plan.changes)
    assert '== Статистика выступлений ==' in plan.preview
    assert '{{КлСтат|Чемпионат}}' in plan.preview
    assert 'полный итог карьеры не заявляется' not in plan.preview
    assert '{{КлСтат/Сезон|2026/27|5|2}}' in plan.preview


def test_missing_current_season_cannot_lower_current_career_total():
    api=API()
    api.seasons=lambda pid: {'player_id':pid,'tournament_seasons':[
        {'tournament':{'id':35},'seasons':[{'id':25,'year':'25/26'}]}]}
    _,plan,evidence=prepare(api=api)
    assert '|2026—{{н.в.}}|'+AUGSBURG+'| 4 (1)' in plan.preview
    assert any(r['code']=='career_incomplete' for r in evidence['review'])


def test_null_player_details_in_match_history_do_not_confirm_participation():
    history={'complete':True,'matches':[{**match(),'player':None}]}
    assert confirmed_match_date(history,{(2600,35,26)},datetime(2026,10,9,tzinfo=timezone.utc)) is None


def test_three_subscriptions_sum_budgets_and_rotate_after_exhaustion():
    store=Store();session=Session([response(remaining=0),response(remaining=998),response(remaining=997),response(remaining=999)])
    api=SofascoreClient(store,keys=['KEY_A','KEY_B','KEY_C'],session=session)
    api.request('/search',{'query':'first'})
    api.request('/search',{'query':'second'})
    assert [c[1]['headers']['x-rapidapi-key'] for c in session.calls]==['KEY_A','KEY_B']
    # Checking all keys keeps an exhausted subscription untouched.
    api.check_keys(1)
    q=api.quota_status()
    assert q['limit']==3000 and len(q['keys'])==3
    assert all(k not in str(store.data) for k in ['KEY_A','KEY_B','KEY_C'])


def test_existing_single_key_balance_survives_pool_migration():
    store=Store();session=Session([response(remaining=940)])
    SofascoreClient(store,key='KEY_A',session=session).request('/search',{'query':'x'})
    pool=SofascoreClient(store,keys=['KEY_A','KEY_B','KEY_C'],session=Session([]))
    assert pool.quota_status()['keys'][0]['remaining']==940
    assert pool.request('/search',{'query':'x'})=={'ok':True}


def test_exhausted_429_rotates_but_rate_429_stops_every_key():
    exhausted=Session([response(status=429,remaining=0),response(remaining=999)])
    api=SofascoreClient(Store(),keys=['KEY_A','KEY_B'],session=exhausted)
    assert api.request('/search',{'query':'x'})=={'ok':True}
    assert len(exhausted.calls)==2
    rate=Session([response(status=429,remaining=900),response()])
    api=SofascoreClient(Store(),keys=['KEY_A','KEY_B'],session=rate)
    with pytest.raises(UpdateError) as exc: api.request('/search',{'query':'x'})
    assert exc.value.code=='api_rate_limited' and len(rate.calls)==1
    with pytest.raises(UpdateError): api.request('/search',{'query':'x'})
    assert len(rate.calls)==1


def test_bad_key_rotates_but_502_does_not():
    session=Session([response(status=403),response()])
    api=SofascoreClient(Store(),keys=['KEY_A','KEY_B'],session=session)
    api.request('/search',{'query':'x'})
    assert api.quota_status()['keys'][0]['disabled']
    session=Session([response(status=502),response()])
    api=SofascoreClient(Store(),keys=['KEY_A','KEY_B'],session=session)
    with pytest.raises(UpdateError): api.request('/search',{'query':'x'})
    assert len(session.calls)==1


def test_pool_rejects_duplicate_keys_and_reflected_secrets():
    with pytest.raises(UpdateError): SofascoreClient(Store(),keys=['KEY_A','KEY_A'])
    store=Store();session=Session([response({'reflected':'KEY_B'})])
    api=SofascoreClient(store,keys=['KEY_A','KEY_B'],session=session)
    with pytest.raises(UpdateError): api.request('/search',{'query':'x'})
    assert not any(bucket=='cache' for bucket,_ in store.data)

@pytest.mark.parametrize('period', ['2023—{{н.в.}}','2023—{{н. в.}}','2023—{{ н. в. }}','2023—{{НВ}}'])
def test_current_period_variants_are_recognized_without_rewriting(period):
    from wiki_stats.structural_editor import is_active_period, validate_period
    assert is_active_period(period)
    assert validate_period(period)==2023

@pytest.mark.parametrize('fail_second', [False,True])
def test_european_competitions_are_summed_only_when_all_queries_succeed(fail_second):
    class EuropeAPI(API):
        def seasons(self,pid):
            result=super().seasons(pid)
            for tid in [7,679]:
                result['tournament_seasons'].append({'tournament':{'id':tid},'seasons':[{'id':26,'year':'26/27'}]})
            return result
        def statistics(self,pid,tid,sid,part):
            if tid==7 and fail_second:raise UpdateError('api_unavailable','One European competition failed')
            result=super().statistics(pid,tid,sid,part)
            result['statistics']={'appearances':2 if tid==7 else 3,'goals':1}
            return result
        def matches(self,*args,**kwargs):
            return {'complete':True,'matches':[match()]+[match(10+i,tournament=tid) for tid,count in [(7,2),(679,3)] for i in range(count)]}
    _,plan,report=prepare(api=EuropeAPI())
    changes=[c for c in plan.changes if c.operation.get('competition',{}).get('category')=='Еврокубки']
    assert bool(changes)==(not fail_second)
    if changes:assert changes[0].operation['new']=={'appearances':5,'goals':2}

def test_european_main_round_statistics_do_not_erase_qualification():
    class QualifyingAPI(API):
        def seasons(self,pid):
            result=super().seasons(pid)
            result['tournament_seasons'].append({'tournament':{'id':679},'seasons':[{'id':26,'year':'26/27'}]})
            return result
        def statistics(self,pid,tid,sid,part):
            result=super().statistics(pid,tid,sid,part)
            if tid==679:result['statistics']={'appearances':5,'goals':0}
            return result
        def matches(self,*args,**kwargs):
            rows=[match()]+[match(i,tournament=679) for i in range(10,21)]
            for row in rows[1:]:row['season']['year']='26/27'
            for row in rows[1:7]:row['season']['id']=99
            return {'complete':True,'matches':rows}
    _,plan,report=prepare(api=QualifyingAPI())
    assert not any(c.operation.get('competition',{}).get('category')=='Еврокубки' for c in plan.changes)
    assert any(r['code']=='european_scope_unproven' and r['history_appearances']==11 for r in report['review'])


def test_european_total_includes_qualifiers_of_an_unindexed_competition():
    class MissingChampionsIndex(API):
        def seasons(self,pid):
            result=super().seasons(pid)
            result['tournament_seasons'].append({'tournament':{'id':679},'seasons':[{'id':26,'year':'26/27'}]})
            return result
        def statistics(self,pid,tid,sid,part):
            result=super().statistics(pid,tid,sid,part)
            if tid==679:result['statistics']={'appearances':5,'goals':1}
            return result
        def matches(self,*args,**kwargs):
            rows=[match()]+[match(i,tournament=679) for i in range(10,15)]+[match(i,tournament=7) for i in range(20,23)]
            for row in rows[1:]:row['season']['year']='26/27'
            return {'complete':True,'matches':rows}
    _,plan,report=prepare(api=MissingChampionsIndex())
    assert not any(c.operation.get('competition',{}).get('category')=='Еврокубки' for c in plan.changes)
    assert any(r['code']=='european_category_incomplete' for r in report['review'])

@pytest.mark.parametrize('api_apps,accepted',[(5,True),(6,False)])
def test_mid_season_transfer_does_not_attribute_other_club_games(api_apps,accepted):
    class SplitAPI(API):
        def seasons(self,pid):
            return {**super().seasons(pid),'statistics_types':{'35':{'26':['overall','home','away']}}}
        def statistics(self,pid,tid,sid,part):
            result=super().statistics(pid,tid,sid,part);result['statistics']['appearances']=api_apps;return result
        def transfers(self,pid):
            return {'player_id':pid,'transfers':[{'transfer_date':'2026-08-01','transfer_from':{'id':99},'transfer_to':{'id':2600}}]}
        def matches(self,*args,**kwargs):
            rows=[match(i) for i in range(1,6)]
            other=match(10);other['player']['team_id']=99;other['away_team']['id']=2600
            return {'complete':True,'matches':rows+[other]}
    _,plan,report=prepare(api=SplitAPI())
    assert any(c.operation['type']=='update_stats' for c in plan.changes)==accepted
    assert any(r['code']=='multi_club_season_unproven' for r in report['review'])== (not accepted)

def test_european_sum_does_not_ignore_unqueried_competition_at_request_cap():
    class CappedAPI(API):
        def seasons(self,pid):
            return {'player_id':pid,'tournament_seasons':[
                {'tournament':{'id':tid},'seasons':[{'id':26,'year':'26/27'}]} for tid in [35,7,679]]}
    text=MINI.replace('| имя =','| дата рождения = 22.08.1999\n| имя =').replace('2025/26','2026/27')
    _,plan,report=prepare_player(snapshot(text),{'api_id':1,'qid':'Q114406918','title':'Игрок',
        'wiki_name':'Кальвин Браккельман','birth_date':'1999-08-22'},CappedAPI(),Wiki(),Resolver(),
        now=datetime(2026,10,9,tzinfo=timezone.utc),max_requests=2)
    assert not any(c.operation.get('competition',{}).get('category')=='Еврокубки' for c in plan.changes)
    assert any(r['code']=='collection_capped' for r in report['review'])

@pytest.mark.parametrize('arrival,expected', [('2026-07-01',True),('2025-07-01',False),('2027-07-01',False)])
def test_unknown_active_period_requires_matching_past_transfer(arrival,expected):
    class PeriodAPI(API):
        def transfers(self,pid):
            return {'player_id':pid,'transfers':[{'transfer_date':arrival,'transfer_to':self.details(pid)['team'],
                'transfer_from':{'id':99,'name':'Аугсбург'}} ]}
    raw=MINI.replace('2026—{{н.в.}}','2026—?')
    _,plan,_=prepare(raw,api=PeriodAPI())
    repaired=any(c.id=='restore-active-career-period' and c.status in ('ready','already_applied') for c in plan.changes)
    assert repaired==expected
    if expected:
        assert '2026—?' not in plan.preview
        assert '2026—{{н.в.}}' in plan.preview


def test_unknown_period_operation_checks_current_club_and_preserves_notes():
    op=operation('period','update_career_period',entity={'name':'Аугсбург','kind':'club','wikidata_id':'Q15755','wikitext':AUGSBURG},
        target={'structure':'career','field':'клубы','period':'2026—?'},
        payload={'period':'2026—{{н.в.}}','restore_unknown_end':True,'arrival_date':'2026-07-01'})
    raw=MINI.replace('2026—{{н.в.}}','2026—?')
    fixed,status=execute_operation(raw,'Кальвин Браккельман',op,Resolver())
    assert status=='ready' and '<ref name="x"/>' in fixed
    assert execute_operation(fixed,'Кальвин Браккельман',op,Resolver())[1]=='already_applied'
    op['payload']['arrival_date']='2025-07-01'
    with pytest.raises(UpdateError):execute_operation(raw,'Кальвин Браккельман',op,Resolver())

def test_loan_season_label_matches_source_without_creating_another_season():
 raw=MINI.replace('{{КлСтат/Сезон|2025/26|','{{КлСтат/Сезон|2025/26 (аренда)|')
 _,plan,report=prepare(raw)
 assert '{{КлСтат/Сезон|2026/27 (аренда)|5|2|' in plan.preview
 assert not any(c.operation['type']=='add_season' for c in plan.changes)
 assert not any(r['code']=='new_row_needs_all_categories' for r in report['review'])

@pytest.mark.parametrize('change,zero', [('none',True),('incomplete',False),('count',False),('cup',False),('bench',True),('unknown_minutes',False)])
def test_absent_category_zero_requires_complete_anchored_closed_season(change,zero):
    from wiki_stats.sofascore_cycle import absent_category_zero
    league={'season':'2025/26','team_id':2600,'tournament_id':35,'season_id':25,'numbers':{'appearances':1,'goals':0}}
    old=match(1,day='2024-01-01');old['season']['id']=24
    played=match(2,day='2025-09-01');played['season']={'id':25,'year':'25/26'}
    latest=match(3,day='2026-09-01')
    history={'complete':True,'matches':[old,played,latest]}
    if change=='incomplete':history['complete']=False
    if change=='count':league['numbers']['appearances']=2
    if change in ('cup','bench','unknown_minutes'):
        cup=match(4,day='2025-10-01',tournament=217,minutes=0 if change=='bench' else 90)
        cup['season']={'id':52,'year':'25/26'}
        if change=='unknown_minutes':cup['player']['statistics']={}
        history['matches'].append(cup)
    result=absent_category_zero(history,league,'Национальный кубок',datetime(2026,10,9,tzinfo=timezone.utc))
    assert (result=={'appearances':0,'goals':0})==zero


@pytest.mark.parametrize('extra,expected', [('bench',True),('missing_played',False),('playoff',False)])
def test_unknown_league_bench_does_not_hide_proven_absent_category(extra,expected):
    from wiki_stats.sofascore_cycle import absent_category_zero
    league={'season':'2025/26','team_id':2600,'tournament_id':35,'season_id':25,'numbers':{'appearances':1,'goals':0}}
    old=match(1,day='2024-01-01');old['season']['id']=24
    played=match(2,day='2025-09-01');played['season']={'id':25,'year':'25/26'}
    latest=match(3,day='2026-09-01')
    bench=match(4,day='2025-10-01');bench['season']={'id':25,'year':'25/26'}
    bench['player']['statistics']=None
    if extra=='missing_played':league['numbers']['appearances']=2
    if extra=='playoff':bench['round']={'name':'Relegation playoffs'}
    result=absent_category_zero({'complete':True,'matches':[old,played,bench,latest]},league,'Прочие',datetime(2026,10,9,tzinfo=timezone.utc))
    assert (result=={'appearances':0,'goals':0})==expected


def test_supported_plain_league_label_allows_restoring_a_missing_season():
    from wiki_stats.klstat_editor import same_league_name
    assert same_league_name('Бундеслига','[[Чемпионат Германии по футболу|Бундеслига]]')
    assert not same_league_name('Бундеслига','[[Чемпионат Испании|Бундеслига]]')
    class ClosedAPI(API):
        def seasons(self,pid):
            result=super().seasons(pid)
            result['tournament_seasons'][0]['seasons'].append({'id':25,'year':'25/26'})
            return result
        def matches(self,*args,**kwargs):
            rows=[match(1,day='2024-01-01')]+[match(i,day='2025-10-01') for i in range(2,7)]+[match(7,day='2026-09-01')]
            for row in rows[1:6]:row['season']={'id':25,'year':'25/26'}
            return {'complete':True,'matches':rows}
    text=MINI.replace('[[Чемпионат Германии по футболу|Бундеслига]]','Бундеслига')
    pkg,plan,report=prepare(text,api=ClosedAPI())
    assert any(c.operation['type']=='add_season' and c.status=='ready' for c in plan.changes)
    assert '{{КлСтат/Сезон|2025/26|5|2|0|0|0|0|0|0}}' in plan.preview
    assert any(r['code']=='zero_from_complete_match_history' for r in report['review'])
    replay=__import__('wiki_stats.change_planner',fromlist=['plan_article']).plan_article(snapshot(plan.preview),pkg['articles'][0],'1.1',Resolver())
    assert not replay.diff and all(c.status=='already_applied' for c in replay.changes)
