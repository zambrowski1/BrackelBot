from tests.test_sofascore import API, Wiki, Resolver, MINI, snapshot, match
from tests.test_season_aggregate_mode import MAPPING, NOW
from wiki_stats.sofascore_cycle import other_season_clubs, prepare_player


def history_match(**changes):
    row = match()
    row['player']['team_id'] = 99
    row.update(changes)
    return row


RECORD = {'team_id': 2600, 'tournament_id': 35, 'season_id': 26}


def test_positive_other_club_detected_even_with_partial_history():
    assert other_season_clubs({'complete': False, 'matches': [history_match()]}, RECORD, NOW) == [99]


def test_bench_future_wrong_scope_and_unbound_team_are_not_evidence():
    bench = history_match(); bench['player']['statistics']['minutes_played'] = 0
    missing = history_match(); missing['player']['statistics'] = None
    future = history_match(start_time='2026-10-11T12:00:00Z')
    wrong = history_match(season={'id': 25})
    unbound = history_match(); unbound['player']['team_id'] = 77
    assert other_season_clubs({'matches': [bench, missing, future, wrong, unbound]}, RECORD, NOW) == []


def test_aggregate_does_not_overwrite_one_club_with_combined_season():
    class Split(API):
        def matches(self, *args, **kwargs):
            return {'complete': True, 'matches': [match(), history_match(id=2)]}
        def match_statistics(self, *args):
            raise AssertionError('Aggregate guard must not spend protocol requests')
    text = MINI.replace('| имя =', '| дата рождения = 22.08.1999\n| имя =')
    _, plan, report = prepare_player(snapshot(text), MAPPING, Split(), Wiki(), Resolver(), statistics_since=0, now=NOW)
    assert any(r['code'] == 'multi_club_season_unproven' for r in report['review'])
    assert not any(c.operation['type'] == 'update_stats' and c.status == 'ready' for c in plan.changes)
