# SPDX-License-Identifier: MIT
"""Recover a club's closed competition scope from the full tournament calendar."""
from datetime import datetime, date

from .errors import UpdateError
from .sofascore_timeline import timeline_numbers


def confirmed_match_sheet_absence(row, pid, team_id, lineup, timeline):
    """A player omitted by a complete confirmed match sheet cannot play.

    This does not reconstruct scoring or substitutions. IFAB Law 3 permits
    only the named players and substitutes to take part. Contradictory
    target events still invalidate the roster evidence.
    """
    if (lineup.get('match_id') != row.get('id') or lineup.get('is_confirmed') is not True
            or timeline.get('match_id') != row.get('id') or row.get('is_finished') is not True
            or (row.get('status') or {}).get('type') != 'finished'):
        return False
    if sum((row.get(k) or {}).get('id') == team_id for k in ('home_team','away_team')) != 1:
        return False
    ids=[]
    for side in ('home','away'):
        squad=lineup.get(side) or {}
        if not isinstance(squad,dict):return False
        players=squad.get('players')
        if not isinstance(players,list) or not 11<=len(players)<=40:return False
        if any(not isinstance(p,dict) or not isinstance(p.get('player'),dict) for p in players):return False
        if sum(p.get('is_substitute') is False for p in players)!=11:return False
        if any(type(p.get('is_substitute')) is not bool for p in players):return False
        ids.extend(p['player'].get('id') for p in players)
    if any(type(i) is not int or i<=0 for i in ids) or len(ids)!=len(set(ids)) or pid in ids:return False
    events=timeline.get('incidents')
    if (not isinstance(events,list) or type(timeline.get('count')) is not int
            or timeline['count']!=len(events) or any(not isinstance(e,dict) for e in events)):
        return False
    for event in events:
        for field in ('player','player_in','player_out','assist1','assist2'):
            actor=event.get(field) or {}
            if not isinstance(actor,dict) or actor.get('id')==pid:return False
    return True


def recover_european_calendar(anchor, pid, api, budget, now, *, goalkeeper=False):
    # The Conference League first ran in 2021/22; before 2009/10 there
    # were additional historical competitions not covered by these IDs.
    if not 2009 <= int(anchor['season'][:4]) < 2021:return None
    calendars=[]
    for tid in (7,679):
        value=recover_calendar(anchor,pid,api,budget,now,tid,goalkeeper=goalkeeper)
        if value is None:return None
        calendars.append({'tournament_id':tid,**value})
    return {'numbers':{field:sum(c['numbers'][field] for c in calendars)
                       for field in ('appearances','goals')},'calendars':calendars}


def recover_calendar(anchor, pid, api, budget, now, tid, *, goalkeeper=False):
    if not all(hasattr(api, name) for name in ('tournament_seasons', 'tournament_matches',
                                             'match_lineups', 'match_incidents')):
        return None
    # The 2019/20 cup final was played on 4 July 2020; European finals
    # were postponed into August. An explicit season ID is the scope,
    # and the date window must include this summer extension.
    year = int(anchor['season'][:4]);end = date(year+1, 9, 1)
    if now.date() < end or anchor['numbers']['appearances'] <= 0:
        return None
    def fail(code, mid=None):
        budget.setdefault('failures', []).append({'code': code, 'season': anchor['season'],
            'team_id': anchor['team_id'], 'tournament_id': tid, 'match_id': mid,
            'remaining': budget['remaining']})
        return None
    try:
        if budget['remaining'] <= 0:return fail('match_request_cap')
        budget['remaining'] -= 1
        index = api.tournament_seasons(tid)
        seasons = index.get('seasons')
        if (type(index.get('tournament_id')) is not int or index['tournament_id'] != tid
                or not isinstance(seasons, list) or type(index.get('count')) is not int
                or index['count'] != len(seasons)):
            return fail('calendar_season_unproven')
        labels = {anchor['season'], anchor['season'][2:]}
        scopes = [s for s in seasons if s.get('year') in labels]
        if len(scopes) != 1 or type(scopes[0].get('id')) is not int or scopes[0]['id'] <= 0:
            return fail('calendar_season_unproven')
        sid = scopes[0]['id']
        calendar = api.tournament_matches(tid, sid, request_budget=budget)
        rows = calendar.get('matches')
        if (calendar.get('complete') is not True or calendar.get('tournament_id') != tid
                or calendar.get('season_id') != sid or not isinstance(rows, list) or not rows):
            return fail('calendar_incomplete')
        ids = [r.get('id') for r in rows]
        if any(type(mid) is not int or mid <= 0 for mid in ids) or len(ids) != len(set(ids)):
            return fail('calendar_incomplete')
        candidates = []
        for row in rows:
            s = row.get('season') or {};t = row.get('tournament') or {}
            if s.get('id') != sid or s.get('year') not in labels or t.get('id') != tid:
                return fail('calendar_scope_unproven', row['id'])
            day = datetime.fromisoformat(row['start_time'].replace('Z', '+00:00'))
            if day.tzinfo is None or not date(year, 6, 1) <= day.date() < end or day > now:
                return fail('calendar_scope_unproven', row['id'])
            teams = [row.get(side) or {} for side in ('home_team', 'away_team')]
            if (any(type(t.get('id')) is not int or t['id'] <= 0
                    or (t.get('sport') or {}).get('slug') != 'football' for t in teams)
                    or teams[0]['id'] == teams[1]['id']):
                return fail('calendar_scope_unproven', row['id'])
            if not any(t['id'] == anchor['team_id'] for t in teams):continue
            if row.get('is_finished') is not True or (row.get('status') or {}).get('type') != 'finished':
                return fail('calendar_match_unfinished', row['id'])
            candidates.append(row)
        if 2*len(candidates) > budget['remaining']:return fail('match_request_cap')
        total = {'appearances': 0, 'goals': 0}
        roster_absences=[]
        secondary_proofs=[]
        for row in candidates:
            budget['remaining'] -= 1;lineup = api.match_lineups(row['id'])
            budget['remaining'] -= 1;timeline = api.match_incidents(row['id'])
            value = timeline_numbers(row, pid, anchor['team_id'], lineup, timeline,
                                     goalkeeper=goalkeeper, allow_absent=True)
            if value is None and confirmed_match_sheet_absence(row,pid,anchor['team_id'],lineup,timeline):
                value={'appearances':0,'goals':0};roster_absences.append(row['id'])
            if value is None and hasattr(api,'match_secondary_source'):
                if budget['remaining']<=0:return fail('match_request_cap',row['id'])
                budget['remaining']-=1
                secondary=api.match_secondary_source(row['id'])
                if isinstance(secondary,dict):
                    from .dfb_evidence import dfb_unused_substitute
                    proof=dfb_unused_substitute(row,pid,anchor['team_id'],lineup,
                                               secondary.get('html'),secondary.get('url'))
                    if proof is not None:
                        value={k:proof[k] for k in ('appearances','goals')}
                        secondary_proofs.append(proof)
            if value is None:return fail('calendar_timeline_unproven', row['id'])
            for field in total:total[field] += value[field]
        return {'numbers': total, 'season_id': sid, 'match_ids': [r['id'] for r in candidates],
                'calendar_match_ids': ids, 'roster_proven_absences':roster_absences,
                'secondary_proofs':secondary_proofs}
    except UpdateError as exc:
        if exc.code in {'quota_exhausted', 'api_rate_limited', 'api_authentication'}:budget['remaining'] = 0
        return fail(exc.code)
    except (KeyError, TypeError, ValueError):
        return fail('calendar_invalid')
