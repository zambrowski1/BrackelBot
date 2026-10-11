# SPDX-License-Identifier: MIT
"""Verify participation and goals from a confirmed roster and score-complete timeline."""
import math
from .sofascore_score import match_score


def clock(event):
    minute=event.get('time');added=event.get('added_time')
    if added is None:added=0
    if type(minute) is not int or not 0 <= minute <= 120 or type(added) is not int or not 0 <= added <= 30:
        return None
    return minute,added


def timeline_numbers(row, pid, team_id, lineup, timeline, *, goalkeeper=False, allow_absent=False):
    mid=row.get('id')
    if (lineup.get('match_id')!=mid or lineup.get('is_confirmed') is not True
            or timeline.get('match_id')!=mid or row.get('is_finished') is not True
            or (row.get('status') or {}).get('type')!='finished'):
        return None
    home=(row.get('home_team') or {}).get('id')==team_id
    if sum((row.get(k) or {}).get('id')==team_id for k in ('home_team','away_team'))!=1:
        return None
    entries=[(side,p) for side in ('home','away') for p in (lineup.get(side) or {}).get('players',[])
             if (p.get('player') or {}).get('id')==pid]
    absent = not entries and allow_absent
    if absent:
        # Absence from a partial roster proves nothing. Require all eleven
        # starters on both sides and unambiguous, disjoint roster identities.
        all_ids=[]
        for side in ('home','away'):
            players=(lineup.get(side) or {}).get('players')
            if not isinstance(players,list) or not 11<=len(players)<=40:return None
            if sum(p.get('is_substitute') is False for p in players)!=11:return None
            if any(type(p.get('is_substitute')) is not bool for p in players):return None
            all_ids.extend((p.get('player') or {}).get('id') for p in players)
        if any(type(i) is not int or i<=0 for i in all_ids) or len(all_ids)!=len(set(all_ids)):return None
    elif len(entries)!=1 or entries[0][0]!=('home' if home else 'away'):
        return None
    # Historical roster entries may expose the player's CURRENT team_id.
    # Membership here is bound to the match id and the home/away roster.
    entry=entries[0][1] if entries else None
    if entry is not None and (type(entry.get('is_substitute')) is not bool or goalkeeper and entry.get('position') not in {'G','GK'}):
        return None
    events=timeline.get('incidents')
    if not isinstance(events,list) or type(timeline.get('count')) is not int or timeline['count']!=len(events):
        return None
    periods=[e for e in events if e.get('type')=='period' and e.get('is_live') is False]
    overtime=[e for e in periods if e.get('text') in {'ET','FT'} and e.get('time')==120]
    normal=[e for e in periods if e.get('text')=='FT' and e.get('time')==90]
    ends=overtime or normal
    if len(ends)!=1 or len(normal)>1:
        return None
    end=ends[0];score=(end.get('home_score'),end.get('away_score'))
    if any(type(n) is not int or not 0<=n<=100 for n in score):return None
    if match_score(row)!=score:return None
    goals=[e for e in events if e.get('type')=='goal']
    if len(goals)!=sum(score):return None
    def scored(e):return e.get('home_score'),e.get('away_score')
    if any(any(type(n) is not int for n in scored(e)) for e in goals):return None
    goals.sort(key=lambda e:sum(scored(e)))
    previous=(0,0);verified=[]
    for goal in goals:
        result=scored(goal);delta=(result[0]-previous[0],result[1]-previous[1])
        if (delta not in ((1,0),(0,1)) or goal.get('class') not in {'regular','penalty','ownGoal'}
                or type((goal.get('player') or {}).get('id')) is not int or clock(goal) is None):return None
        if clock(goal)>(end['time'],999):return None
        if goal['class']!='ownGoal' and (type(goal.get('is_home')) is not bool or goal['is_home']!=(delta==(1,0))):return None
        if goal['class']!='ownGoal' and goal['player']['id']==pid and goal['is_home']!=home:return None
        if verified and clock(goal)<clock(verified[-1][0]):return None
        verified.append((goal,delta==(1,0)));previous=result
    if previous!=score:return None
    if overtime and normal:
        normal_goals=[g for g,_ in verified if clock(g)<=(90,999)]
        normal_score=scored(normal_goals[-1]) if normal_goals else (0,0)
        if scored(normal[0])!=normal_score:return None
    if absent:
        # A scorer, substitute or card recipient omitted by the roster makes
        # the feed contradictory. Never turn this into a zero appearance.
        if any((e.get(field) or {}).get('id')==pid for e in events
               for field in ('player','player_in','player_out','assist1','assist2')):return None
        return {'appearances':0,'goals':0}
    incoming=[];outgoing=[]
    for event in events:
        if event.get('type')=='substitution':
            if (event.get('player_in') or {}).get('id')==pid:incoming.append(event)
            if (event.get('player_out') or {}).get('id')==pid:outgoing.append(event)
        if (event.get('type')=='card' and event.get('class') in {'red','yellowRed'}
                and event.get('rescinded') is not True and (event.get('player') or {}).get('id')==pid):
            outgoing.append(event)
    if len(incoming)>1 or len(outgoing)>1 or any(clock(e) is None for e in incoming+outgoing):return None
    for event in incoming+outgoing:
        if clock(event)>(end['time'],999):return None
        if event.get('is_home') is not None and (type(event['is_home']) is not bool or event['is_home']!=home):return None
    if entry['is_substitute']:
        if not incoming:
            if outgoing or any((g.get('player') or {}).get('id')==pid for g,_ in verified):return None
            minutes=(entry.get('statistics') or {}).get('minutes_played')
            if minutes is not None and (type(minutes) not in (int,float) or minutes!=0):return None
            return {'appearances':0,'goals':0}
        start=clock(incoming[0])
    else:
        if incoming:return None
        start=(0,0)
    stop=clock(outgoing[0]) if outgoing else (end['time'],999)
    if start>=stop:return None
    minutes=(entry.get('statistics') or {}).get('minutes_played')
    if minutes is not None and (type(minutes) not in (int,float) or not math.isfinite(minutes) or not 0<minutes<=130):return None
    if minutes is not None and not outgoing:
        # FT's (90/120, 999) is a terminal marker, not the final whistle's
        # actual clock. Announced injury time is a minimum, and historical
        # feeds may omit it altogether. Confirmed entry + no exit + complete
        # scoring events prove participation and goals independently of an
        # upper bound on minutes. Do retain a contradictory lower-bound check.
        if minutes < max(0, stop[0]-start[0]-start[1]-1):return None
    if minutes is not None and outgoing:
        nominal=stop[0]-start[0]
        if abs(minutes-nominal)>1:
            # Substitute statistics may include stoppage minutes, whereas
            # starters' statistics often use the nominal 90/120 minutes.
            # Expand the bound only with explicit period stoppage evidence.
            allowance=0;periods_seen=set()
            for event in events:
                if event.get('type')!='injuryTime':continue
                boundary=event.get('time');length=event.get('length')
                if (type(boundary) is not int or boundary not in {45,90,105,120}
                        or type(length) is not int or not 0<=length<=30 or boundary in periods_seen):return None
                periods_seen.add(boundary)
                if start[0]<=boundary<=stop[0]:
                    allowance+=stop[1] if boundary==stop[0] and outgoing else length
            allowance-=start[1]
            if not nominal-1<=minutes<=nominal+max(0,allowance)+1:return None
    value=0
    for goal,scoring_home in verified:
        moment=clock(goal)
        relevant=scoring_home!=home if goalkeeper else (goal.get('player') or {}).get('id')==pid and goal['class']!='ownGoal'
        if not relevant:continue
        if moment==start or outgoing and moment==stop:return None
        if start<moment<stop:value+=-1 if goalkeeper else 1
        elif not goalkeeper:return None
    return {'appearances':1,'goals':value}
