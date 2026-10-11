# SPDX-License-Identifier: MIT
"""Reconstruct a competition total from explicitly verified match statistics."""
from datetime import datetime, date
import math
import re

from .errors import UpdateError


def excluded_league_round(tournament_id, round_):
    # Belgian championship and European qualification playoffs belong to the
    # Pro League season. German promotion/relegation ties are separate totals.
    if tournament_id == 38:
        return False
    return bool(round_.get('cup_round_type') or any(
        'play' in str(round_.get(k, '')).lower() for k in ('name', 'slug')))


def played_matches(history, record, now, *, european=False, league=True, allow_unknown_minutes=False):
    if history.get('complete') is not True:
        return None
    rows = []
    unplayed_bench = []
    year = int(record['season'][:4])
    for row in history.get('matches', []):
        player = row.get('player') or {}
        if (player.get('team_id') != record['team_id'] or
                (row.get('tournament') or {}).get('id') != record['tournament_id']):
            continue
        if row.get('is_finished') is not True or (row.get('status') or {}).get('type') != 'finished':
            continue
        season = row.get('season') or {}
        label = str(season.get('year', ''))
        labels = {record['season'], record['season'][2:]}
        parts = re.fullmatch(r'(20\d{2}|\d{2})/(\d{2})', label)
        known_label = parts is not None and (int(parts[1])+1)%100 == int(parts[2])
        matches_scope = season.get('id') == record['season_id'] or european and label in labels
        if not matches_scope and known_label:
            continue
        try:
            day = datetime.fromisoformat(row['start_time'].replace('Z', '+00:00'))
        except (KeyError, TypeError, ValueError):
            return None
        if day.tzinfo is None:
            return None
        if day > now:
            continue
        if not matches_scope:
            if not known_label and date(year, 6 if european else 7, 1) <= day.date() < date(year+1, 7, 1):
                return None
            continue
        if sum((row.get(k) or {}).get('id') == record['team_id'] for k in ('home_team', 'away_team')) != 1:
            return None
        round_ = row.get('round') or {}
        if league and not european and excluded_league_round(record['tournament_id'], round_):
            continue
        minutes = (player.get('statistics') or {}).get('minutes_played')
        if (minutes is None and player.get('is_on_bench') is True and not allow_unknown_minutes):
            unplayed_bench.append(row)
            continue
        if minutes is None and allow_unknown_minutes:
            rows.append(row)
            continue
        if type(minutes) not in (int, float) or not math.isfinite(minutes) or not 0 <= minutes <= 130:
            return None
        if minutes > 0:
            rows.append(row)
    # A missing bench metric is not independently a zero. Accept it only if
    # the explicitly played matches exhaust the separately verified aggregate.
    if unplayed_bench and len(rows) != record['numbers']['appearances']:
        return None
    ids = [r.get('id') for r in rows + unplayed_bench]
    if any(type(i) is not int or i <= 0 for i in ids) or len(ids) != len(set(ids)):
        return None
    return rows


def reconstruct(api, player_id, record, rows, budget, *, goalkeeper=False, allow_absent=False):
    """Never infer zero from an omitted metric or allocate a season aggregate."""
    def failed(code,mid=None):
        budget.setdefault('failures',[]).append({'code':code,'season':record.get('season'),
            'team_id':record.get('team_id'),'tournament_id':record.get('tournament_id'),
            'match_id':mid,'remaining':budget['remaining']})
        return None
    if rows is None or not rows:return failed('match_history_unproven')
    if not hasattr(api,'match_statistics'):return failed('match_adapter_unsupported')
    if len(rows)>budget['remaining']:return failed('match_request_cap')
    from .sofascore_timeline import timeline_numbers
    goals = appearances = 0
    for row in rows:
        budget['remaining'] -= 1
        try:
            data = api.match_statistics(player_id, row['id'])
        except UpdateError as exc:
            if exc.code in {'quota_exhausted', 'api_rate_limited', 'api_authentication'}:
                budget['remaining'] = 0
                return failed(exc.code,row['id'])
            # Offline replays may lack the personal-statistics response while
            # retaining independently verifiable roster and timeline data.
            if exc.code not in {'api_not_found','source_cache_missing'}:return failed(exc.code,row['id'])
            data=None
        if data is not None and (data.get('match_id') != row['id'] or (data.get('player') or {}).get('id') != player_id
                or (data.get('team') or {}).get('id') != record['team_id']):
            return failed('match_identity_mismatch',row['id'])
        stats = (data or {}).get('statistics') or {}
        minutes = stats.get('minutes_played')
        metric = stats.get('goals_conceded' if goalkeeper else 'goals')
        maximum=100
        if 'home_score' in row and 'away_score' in row:
            from .sofascore_score import match_score
            score=match_score(row)
            if score is None:return failed('match_score_unproven',row['id'])
            home=(row.get('home_team') or {}).get('id')==record['team_id']
            maximum=score[(1 if home else 0) if goalkeeper else (0 if home else 1)]
        if (type(minutes) in (int,float) and math.isfinite(minutes) and 0<minutes<=130
                and type(metric) is int and 0<=metric<=maximum):
            appearances+=1
            goals += -metric if goalkeeper else metric
            continue
        if not hasattr(api,'match_lineups') or not hasattr(api,'match_incidents'):
            return failed('timeline_adapter_unsupported',row['id'])
        if budget['remaining']<2:return failed('match_request_cap',row['id'])
        budget['remaining']-=2
        try:
            lineups=api.match_lineups(row['id']);incidents=api.match_incidents(row['id'])
        except UpdateError as exc:
            if exc.code in {'quota_exhausted','api_rate_limited','api_authentication'}:budget['remaining']=0
            return failed(exc.code,row['id'])
        recovered=timeline_numbers(row,player_id,record['team_id'],lineups,incidents,
                                   goalkeeper=goalkeeper,allow_absent=allow_absent)
        if recovered is None and allow_absent:
            from .sofascore_calendar import confirmed_match_sheet_absence
            if confirmed_match_sheet_absence(row,player_id,record['team_id'],lineups,incidents):
                recovered={'appearances':0,'goals':0}
                budget.setdefault('roster_proven_absences',[]).append(row['id'])
        if recovered is None:return failed('timeline_unproven',row['id'])
        appearances+=recovered['appearances'];goals+=recovered['goals']
    return {'appearances': appearances, 'goals': goals}
