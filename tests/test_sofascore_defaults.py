# SPDX-License-Identifier: MIT
from datetime import date, timedelta

import pytest

from tests.test_sofascore import API, MINI, Wiki, Resolver, snapshot, match
from wiki_stats.sofascore_cycle import prepare_player
from wiki_stats.server_cli import parser

MAPPING = {'api_id': 1, 'qid': 'Q114406918', 'title': 'Игрок',
           'wiki_name': 'Кальвин Браккельман', 'birth_date': '1999-08-22'}


def run(api, **limits):
    from tests.test_sofascore import datetime, timezone
    text = MINI.split('{{КлСтат|')[0].replace('| имя =', '| дата рождения = 22.08.1999\n| имя =')
    return prepare_player(snapshot(text), MAPPING, api, Wiki(), Resolver(),
                          now=datetime(2026, 10, 9, tzinfo=timezone.utc), **limits)


class Pressure(API):
    def __init__(self):self.calls = 0;self.page_limit = None
    def seasons(self, pid):
        return {'player_id': pid, 'tournament_seasons': [{'tournament': {'id': 35},
            'seasons': [{'id': y, 'year': f'{y}/{y+1}'} for y in (24, 25, 26)]}]}
    def statistics(self, pid, tid, sid, part):
        data = super().statistics(pid, tid, sid, part)
        if sid != 26:data['statistics'] = {'appearances': 34, 'goals': 0}
        return data
    def transfers(self, pid):
        return {'player_id': pid, 'transfers': [{'transfer_date': f'20{y}-08-01',
            'transfer_from': {'id': 99}, 'transfer_to': {'id': 2600}} for y in (24, 25)]}
    def matches(self, pid, max_pages=12):
        self.page_limit = max_pages;rows = [match()]
        for year in (24, 25):
            for i in range(34):
                day = date(2000+year, 8, 1)+timedelta(days=7*i)
                row = match(year*100+i, day=day.isoformat(), minutes=None)
                row['season'] = {'id': year, 'year': f'{year}/{year+1}'}
                row.update(home_score={'current': 0}, away_score={'current': 0});rows.append(row)
        return {'complete': max_pages >= 20, 'matches': rows}
    def match_statistics(self, pid, mid):
        self.calls += 1
        return {'match_id': mid, 'player': {'id': pid}, 'team': {'id': 2600}, 'statistics': {'minutes_played': 90}}
    def match_lineups(self, mid):
        self.calls += 1
        return {'match_id': mid, 'is_confirmed': True, 'home': {'players': [
            {'player': {'id': 1}, 'position': 'F', 'is_substitute': False}]}, 'away': {'players': []}}
    def match_incidents(self, mid):
        self.calls += 1
        return {'match_id': mid, 'count': 1, 'incidents': [{'type': 'period', 'text': 'FT', 'time': 90,
            'is_live': False, 'home_score': 0, 'away_score': 0}]}


@pytest.mark.parametrize('limits,complete', [({}, True), ({'max_match_requests': 160}, False), ({'max_match_pages': 2}, False)])
def test_default_collection_finishes_long_history_and_explicit_caps_still_apply(limits, complete):
    api = Pressure();_, plan, evidence = run(api, **limits)
    recovered = [i for i in evidence['review'] if i['code'] == 'match_statistics_reconstructed']
    assert (len(recovered) == 2) == complete
    if complete:
        assert api.calls == 204 and api.page_limit >= 20
        assert not evidence['match_recovery_failures']
        assert '{{КлСтат/Сезон|2024/25|34|0}}' in plan.preview
        assert '{{КлСтат/Сезон|2025/26|34|0}}' in plan.preview
    else:
        assert evidence['match_recovery_failures']
        assert api.calls <= limits.get('max_match_requests', 512)


@pytest.mark.parametrize('limit', [None, 3])
def test_default_statistics_scope_includes_more_than_twenty_four_advertised_seasons(limit):
    class Many(API):
        def __init__(self):self.queries = []
        def seasons(self, pid):
            return {'player_id': pid, 'tournament_seasons': [{'tournament': {'id': tid},
                'seasons': [{'id': y, 'year': f'{y}/{y+1}'} for y in range(16, 26)]} for tid in (35, 44, 217)]}
        def statistics(self, pid, tid, sid, part):
            self.queries.append((tid, sid));return super().statistics(pid, tid, sid, part)
    api = Many();_, _, evidence = run(api, **({} if limit is None else {'max_requests': limit}))
    assert len(api.queries) == (30 if limit is None else limit)
    assert any(i['code'] == 'collection_capped' for i in evidence['review']) == (limit is not None)


def test_headless_cli_uses_collection_defaults_and_preserves_operator_overrides():
    argv = ['prepare-player', '1', '--title', 'Игрок', '--identity-title', 'Игрок', '--qid', 'Q1', '--wiki-name', 'Игрок']
    args = parser().parse_args(argv)
    from inspect import signature
    defaults = signature(prepare_player).parameters
    assert (args.max_statistics, args.max_match_pages, args.max_match_requests) == tuple(
        defaults[name].default for name in ('max_requests', 'max_match_pages', 'max_match_requests'))
    custom = parser().parse_args(argv+['--max-statistics', '3', '--max-match-pages', '2', '--max-match-requests', '0'])
    assert (custom.max_statistics, custom.max_match_pages, custom.max_match_requests) == (3, 2, 0)
