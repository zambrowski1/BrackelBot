# SPDX-License-Identifier: MIT
"""Recover an unindexed German cup season from complete, anchored history."""
from datetime import datetime,date
from .errors import UpdateError
from .sofascore_match_stats import played_matches
from .sofascore_timeline import timeline_numbers


def recover_cup(history,league,pid,api,budget,now,*,goalkeeper=False):
    if not all(hasattr(api,name) for name in ('match_lineups','match_incidents')):return None
    year=int(league['season'][:4]);start=date(year,7,1);end=date(year+1,7,1)
    if history.get('complete') is not True or now.date()<end or league['numbers']['appearances']<=0:
        return None
    anchors=played_matches(history,league,now,allow_unknown_minutes=True)
    if anchors is None:return None
    known=sum(type((r.get('player') or {}).get('statistics',{}).get('minutes_played')) in (int,float)
              and (r['player']['statistics']['minutes_played']>0) for r in anchors if r['player'].get('statistics'))
    if known!=league['numbers']['appearances']:return None
    bounds=[];candidates=[]
    for row in history.get('matches',[]):
        if row.get('is_finished') is not True or (row.get('status') or {}).get('type')!='finished':continue
        try:day=datetime.fromisoformat(row['start_time'].replace('Z','+00:00'))
        except (KeyError,ValueError,TypeError):return None
        if day.tzinfo is None:return None
        if day>now:continue
        bounds.append(day.date())
        if (row.get('player') or {}).get('team_id')!=league['team_id'] or (row.get('tournament') or {}).get('id')!=217:continue
        season=row.get('season') or {}
        if season.get('year') not in {league['season'],league['season'][2:]}:
            if start<=day.date()<end:return None
            continue
        if not start<=day.date()<end:return None
        candidates.append(row)
    if not bounds or min(bounds)>start or max(bounds)<end or not candidates:return None
    sids={r['season'].get('id') for r in candidates}
    if len(sids)!=1 or any(type(sid) is not int for sid in sids):return None
    record={**league,'tournament_id':217,'season_id':next(iter(sids))}
    rows=played_matches(history,record,now,league=False,allow_unknown_minutes=True)
    if not rows or 2*len(rows)>budget['remaining']:return None
    numbers={'appearances':0,'goals':0}
    for row in rows:
        budget['remaining']-=2
        try:
            lineup=api.match_lineups(row['id']);timeline=api.match_incidents(row['id'])
        except UpdateError as exc:
            if exc.code in {'quota_exhausted','api_rate_limited','api_authentication'}:budget['remaining']=0
            return None
        value=timeline_numbers(row,pid,league['team_id'],lineup,timeline,goalkeeper=goalkeeper)
        if value is None:return None
        for field in numbers:numbers[field]+=value[field]
    return numbers,[r['id'] for r in rows]
