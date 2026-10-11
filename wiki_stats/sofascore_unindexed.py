# SPDX-License-Identifier: MIT
"""Verify an unindexed competition using an independently anchored season."""
from datetime import date, datetime

from .errors import UpdateError
from .sofascore_match_stats import played_matches, excluded_league_round
from .sofascore_timeline import timeline_numbers


def recover_unindexed(history, anchor, pid, api, budget, now, tid, *, european=False, league=False, goalkeeper=False):
    def fail(code, mid=None):
        budget.setdefault('failures', []).append({'code': code, 'season': anchor['season'],
            'team_id': anchor['team_id'], 'tournament_id': tid, 'match_id': mid,
            'remaining': budget['remaining']})
        return None

    if history.get('complete') is not True or anchor['numbers']['appearances'] <= 0:
        return None
    if not all(hasattr(api, name) for name in ('match_lineups', 'match_incidents')):
        return None
    year = int(anchor['season'][:4])
    start = date(year, 6 if european else 7, 1);end = date(year+1, 7, 1)
    if now.date() < end:return None
    anchors = played_matches(history, anchor, now, allow_unknown_minutes=True,
        european=anchor.get('category') == 'Еврокубки', league=anchor.get('category') == 'Чемпионат')
    if anchors is None:return None
    known = sum(type(((m.get('player') or {}).get('statistics') or {}).get('minutes_played')) in (int, float)
        and ((m['player'].get('statistics') or {})['minutes_played'] > 0) for m in anchors)
    if known != anchor['numbers']['appearances']:return None
    ids = [m.get('id') for m in history.get('matches', [])]
    if any(type(mid) is not int or mid <= 0 for mid in ids) or len(ids) != len(set(ids)):return None
    bounds = [];candidates = []
    labels = {anchor['season'], anchor['season'][2:]}
    for row in history['matches']:
        if row.get('is_finished') is not True or (row.get('status') or {}).get('type') != 'finished':continue
        try:day = datetime.fromisoformat(row['start_time'].replace('Z', '+00:00'))
        except (KeyError, TypeError, ValueError):return None
        if day.tzinfo is None:return None
        if day > now:continue
        bounds.append(day.date())
        if ((row.get('player') or {}).get('team_id'), (row.get('tournament') or {}).get('id')) != (anchor['team_id'], tid):continue
        label = (row.get('season') or {}).get('year')
        if label not in labels:
            if start <= day.date() < end:return None
            continue
        if not start <= day.date() < end:return None
        round_ = row.get('round') or {}
        if league and excluded_league_round(tid, round_):
            continue
        if sum((row.get(k) or {}).get('id') == anchor['team_id'] for k in ('home_team', 'away_team')) != 1:return None
        candidates.append(row)
    if not candidates or not bounds or min(bounds) > start or max(bounds) < end:return None
    sids = {(m.get('season') or {}).get('id') for m in candidates}
    if len(sids) != 1 or any(type(sid) is not int or sid <= 0 for sid in sids):return None
    if 2*len(candidates) > budget['remaining']:return fail('match_request_cap')
    value = {'appearances': 0, 'goals': 0}
    for row in candidates:
        try:
            budget['remaining'] -= 1;roster = api.match_lineups(row['id'])
            budget['remaining'] -= 1;events = api.match_incidents(row['id'])
        except UpdateError as exc:
            if exc.code in {'quota_exhausted', 'api_rate_limited', 'api_authentication'}:budget['remaining'] = 0
            return fail(exc.code, row['id'])
        numbers = timeline_numbers(row, pid, anchor['team_id'], roster, events, goalkeeper=goalkeeper)
        if numbers is None:return fail('timeline_unproven', row['id'])
        for field in value:value[field] += numbers[field]
    return {'numbers': value, 'match_ids': [m['id'] for m in candidates], 'season_id': next(iter(sids))}
