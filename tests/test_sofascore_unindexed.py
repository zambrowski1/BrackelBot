# SPDX-License-Identifier: MIT
from copy import deepcopy
from datetime import datetime, timezone

import pytest

from wiki_stats.errors import UpdateError
from wiki_stats.sofascore_unindexed import recover_unindexed
from wiki_stats.sofascore_cycle import absent_category_zero
from tests.test_sofascore import API, match, prepare

NOW = datetime(2026, 10, 10, tzinfo=timezone.utc)


class BenchAPI:
    def __init__(self):self.calls = []
    def match_lineups(self, mid):
        self.calls.append(('roster', mid))
        return {'match_id': mid, 'is_confirmed': True, 'home': {'players': [
            {'player': {'id': 1}, 'position': 'G', 'is_substitute': True}]}, 'away': {'players': []}}
    def match_incidents(self, mid):
        self.calls.append(('events', mid))
        return {'match_id': mid, 'count': 1, 'incidents': [
            {'type': 'period', 'text': 'FT', 'time': 90, 'is_live': False, 'home_score': 0, 'away_score': 0}]}


def setup():
    old = match(1, day='2024-01-01');old['season'] = {'id': 24, 'year': '23/24'}
    cup = match(2, day='2025-10-01', tournament=217);cup['season'] = {'id': 50, 'year': '25/26'}
    bench = match(3, day='2025-10-02', minutes=None);bench['season'] = {'id': 25, 'year': '25/26'}
    bench.update(home_score={'current': 0}, away_score={'current': 0})
    latest = match(4, day='2026-10-01');latest['season'] = {'id': 26, 'year': '26/27'}
    anchor = {'season': '2025/26', 'team_id': 2600, 'tournament_id': 217, 'season_id': 50,
              'category': 'Национальный кубок', 'numbers': {'appearances': 1, 'goals': 0}}
    return {'complete': True, 'matches': [old, cup, bench, latest]}, anchor


def evaluate(history, anchor, api=None, budget=None):
    return recover_unindexed(history, anchor, 1, api or BenchAPI(),
        budget if budget is not None else {'remaining': 2}, NOW, 35, league=True, goalkeeper=True)


def test_unindexed_league_zero_requires_roster_and_complete_events():
    history, anchor = setup();api = BenchAPI();budget = {'remaining': 2}
    assert evaluate(history, anchor, api, budget) == {'numbers': {'appearances': 0, 'goals': 0}, 'match_ids': [3], 'season_id': 25}
    assert api.calls == [('roster', 3), ('events', 3)] and budget['remaining'] == 0


@pytest.mark.parametrize('problem', ['incomplete', 'open', 'no_anchor', 'anchor_count', 'missing_before',
    'missing_after', 'duplicate', 'wrong_team', 'wrong_year', 'bad_date', 'boolean_id', 'mixed_seasons', 'budget'])
def test_unsafe_unindexed_scope_is_rejected_before_requests(problem):
    history, anchor = setup();budget = {'remaining': 2}
    if problem == 'incomplete':history['complete'] = False
    elif problem == 'open':anchor['season'] = '2026/27'
    elif problem == 'no_anchor':anchor['numbers']['appearances'] = 0
    elif problem == 'anchor_count':anchor['numbers']['appearances'] = 2
    elif problem == 'missing_before':history['matches'].pop(0)
    elif problem == 'missing_after':history['matches'].pop()
    elif problem == 'duplicate':history['matches'].append(deepcopy(history['matches'][2]))
    elif problem == 'wrong_team':history['matches'][2]['home_team']['id'] = 999
    elif problem == 'wrong_year':history['matches'][2]['season']['year'] = '24/25'
    elif problem == 'bad_date':history['matches'][2]['start_time'] = 'unknown'
    elif problem == 'boolean_id':history['matches'][2]['id'] = True
    elif problem == 'mixed_seasons':
        other = deepcopy(history['matches'][2]);other['id'] = 5;other['season']['id'] = 99;history['matches'].append(other)
    else:budget['remaining'] = 1
    api = BenchAPI()
    assert evaluate(history, anchor, api, budget) is None
    assert not api.calls


@pytest.mark.parametrize('problem', ['unconfirmed', 'incomplete_goals', 'quota'])
def test_unindexed_scope_never_turns_a_failed_response_into_zero(problem):
    class Broken(BenchAPI):
        def match_lineups(self, mid):
            if problem == 'quota':raise UpdateError('quota_exhausted', 'Quota')
            data = super().match_lineups(mid)
            if problem == 'unconfirmed':data['is_confirmed'] = False
            return data
        def match_incidents(self, mid):
            data = super().match_incidents(mid)
            if problem == 'incomplete_goals':data['incidents'][0]['home_score'] = 1
            return data
    history, anchor = setup();budget = {'remaining': 2}
    assert evaluate(history, anchor, Broken(), budget) is None
    assert budget['failures'][-1]['code'] == ('quota_exhausted' if problem == 'quota' else 'timeline_unproven')


@pytest.mark.parametrize('proof,expected', [([], False), ([99], False), ([3, 3], False), ([3], True)])
def test_zero_league_anchor_must_cover_every_candidate(proof, expected):
    history, anchor = setup()
    league = {**anchor, 'tournament_id': 35, 'season_id': 25, 'numbers': {'appearances': 0, 'goals': 0},
              'match_reconstructed': True, 'confirmed_match_ids': proof}
    assert (absent_category_zero(history, league, 'Стыковые матчи', NOW) == {'appearances': 0, 'goals': 0}) == expected


def test_pipeline_restores_zero_league_season_and_is_idempotent():
    class CupOnly(API, BenchAPI):
        def seasons(self, pid):
            result = super().seasons(pid)
            result['tournament_seasons'].append({'tournament': {'id': 217}, 'seasons': [{'id': 50, 'year': '25/26'}]})
            return result
        def statistics(self, pid, tid, sid, part):
            data = super().statistics(pid, tid, sid, part)
            if tid == 217:data['statistics'] = {'appearances': 1, 'goals': 0}
            return data
        def matches(self, *args, **kwargs):return setup()[0]
    api = CupOnly();api.calls = []
    package, plan, evidence = prepare(api=api)
    assert '{{КлСтат/Сезон|2025/26|0|0|1|0|0|0|0|0}}' in plan.preview
    assert any(i['code'] == 'unindexed_scope_reconstructed' for i in evidence['review'])
    assert all(c.status in ('ready', 'already_applied') for c in plan.changes)
    from wiki_stats.change_planner import plan_article
    from tests.test_sofascore import Resolver, snapshot
    replay = plan_article(snapshot(plan.preview), package['articles'][0], '1.1', Resolver())
    assert not replay.diff and all(c.status == 'already_applied' for c in replay.changes)


@pytest.mark.parametrize('keeper', [False, True])
def test_unindexed_played_match_preserves_goal_metric_and_anchors_absent_category(keeper):
    class Played(BenchAPI):
        def match_lineups(self, mid):
            data = super().match_lineups(mid)
            data['home']['players'][0]['is_substitute'] = False
            return data
        def match_incidents(self, mid):
            data = super().match_incidents(mid)
            home = not keeper
            data['incidents'][0].update(home_score=int(home), away_score=int(not home))
            data['incidents'].append({'type': 'goal', 'class': 'regular', 'time': 20,
                'is_home': home, 'player': {'id': 99 if keeper else 1},
                'home_score': int(home), 'away_score': int(not home)})
            data['count'] = 2
            return data
    history, anchor = setup()
    history['matches'][2].update(home_score={'current': int(not keeper)}, away_score={'current': int(keeper)})
    result = recover_unindexed(history, anchor, 1, Played(), {'remaining': 2}, NOW, 35, league=True, goalkeeper=keeper)
    assert result['numbers'] == {'appearances': 1, 'goals': -1 if keeper else 1}
    league = {**anchor, **result, 'tournament_id': 35, 'match_reconstructed': True,
              'confirmed_match_ids': result['match_ids']}
    assert absent_category_zero(history, league, 'Стыковые матчи', NOW) == {'appearances': 0, 'goals': 0}


@pytest.mark.parametrize('european_column', [False, True])
def test_european_recovery_obeys_requested_columns_and_supports_foreign_clubs(european_column):
    from dataclasses import replace
    from tests.test_sofascore import MINI, Wiki, Resolver, snapshot
    from wiki_stats.sofascore_cycle import prepare_player
    class CupAndEurope(API, BenchAPI):
        def seasons(self, pid):
            result = super().seasons(pid)
            result['tournament_seasons'].append({'tournament': {'id': 217}, 'seasons': [{'id': 50, 'year': '25/26'}]})
            return result
        def statistics(self, pid, tid, sid, part):
            data = super().statistics(pid, tid, sid, part)
            if tid == 217:data['statistics'] = {'appearances': 1, 'goals': 0}
            return data
        def matches(self, *args, **kwargs):
            history, _ = setup()
            history['matches'][2]['tournament']['id'] = 7
            return history
    class ForeignResolver(Resolver):
        def resolve(self, entity):return replace(super().resolve(entity), flag='Бельгии')
    text = MINI if european_column else MINI.split('{{КлСтат|')[0]
    text = text.replace('2025/26', '2026/27').replace('Флаг Германии', 'Флаг Бельгии')
    text = text.replace('| имя =', '| дата рождения = 22.08.1999\n| имя =')
    api = CupAndEurope();api.calls = []
    _, _, evidence = prepare_player(snapshot(text), {'api_id': 1, 'qid': 'Q114406918', 'title': 'Игрок',
        'wiki_name': 'Кальвин Браккельман', 'birth_date': '1999-08-22'}, api, Wiki(), ForeignResolver(), now=NOW)
    recovered = [i for i in evidence['review'] if i['code'] == 'unindexed_scope_reconstructed']
    if european_column:
        assert len(recovered) == 1 and recovered[0]['tournament_id'] == 7
        assert recovered[0]['numbers'] == {'appearances': 0, 'goals': 0}
        assert api.calls == [('roster', 3), ('events', 3)]
    else:
        # With no existing table the bot now collects European scopes too.
        assert len(recovered) == 1 and recovered[0]['tournament_id'] == 7
        assert recovered[0]['numbers'] == {'appearances': 0, 'goals': 0}
        assert api.calls == [('roster', 3), ('events', 3)]
