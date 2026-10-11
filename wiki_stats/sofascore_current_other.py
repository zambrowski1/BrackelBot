# SPDX-License-Identifier: MIT
"""Prove German current-season Other zeros from dated competition evidence."""
from datetime import datetime, date
import math
from .errors import UpdateError

DFL_CALENDAR='https://www.bundesliga.com/en/bundesliga/news/calendar-for-2026-27-season-world-cup-34676'
TELEKOM_FRIENDLY='https://fcbayern.com/en/news/matchreports/2026/08/match-report-telekom-cup-fc-bayern-v-rb-leipzig-150826'


def prove_current_other(history,anchor,league_calendar,supercup_calendar,now):
    """The published 2026/27 schedule expires when relegation playoffs begin."""
    try:
        tid=anchor['tournament_id'];sid=anchor['season_id'];team_id=anchor['team_id']
        if (anchor['season']!='2026/27' or tid not in {35,44}
                or not date(2026,8,23)<=now.date()<date(2027,5,27)
                or history.get('complete') is not True
                or type(anchor['numbers']['appearances']) is not int
                or anchor['numbers']['appearances']<=0):return None
        calendars=[]
        for calendar,competition,season_id in [(league_calendar,tid,sid),
                                              (supercup_calendar,799,supercup_calendar.get('season_id'))]:
            rows=calendar.get('matches')
            if (calendar.get('complete') is not True or calendar.get('tournament_id')!=competition
                    or calendar.get('season_id')!=season_id or type(season_id) is not int
                    or season_id<=0 or not isinstance(rows,list) or not rows):return None
            ids=[]
            for row in rows:
                mid=row.get('id');season=row.get('season') or {};tournament=row.get('tournament') or {}
                if (type(mid) is not int or mid<=0 or mid in ids
                        or season.get('id')!=season_id or season.get('year') not in {'26/27','2026/27'}
                        or tournament.get('id')!=competition):return None
                ids.append(mid)
                day=datetime.fromisoformat(row['start_time'].replace('Z','+00:00'))
                status=(row.get('status') or {}).get('type')
                if (day.tzinfo is None or day>now or day.date()<date(2026,7,1)
                        or status not in ({'finished'} if competition==799 else {'finished','inprogress'})
                        or status=='finished' and row.get('is_finished') is not True):return None
                teams=[row.get(side) or {} for side in ('home_team','away_team')]
                if (any(type(t.get('id')) is not int or t['id']<=0 or t.get('is_national') is not False
                        or (t.get('sport') or {}).get('slug')!='football' for t in teams)
                        or teams[0]['id']==teams[1]['id']):return None
                round_=row.get('round') or {}
                if competition!=799 and (round_.get('cup_round_type') or any('play' in str(round_.get(k,'')).lower()
                                                                         for k in ('name','slug'))):return None
            calendars.append(ids)
        cup=supercup_calendar['matches']
        if (len(cup)!=1 or datetime.fromisoformat(cup[0]['start_time'].replace('Z','+00:00')).date()!=date(2026,8,22)
                or any(cup[0][side]['id']==team_id for side in ('home_team','away_team'))):return None
        club_rows={r['id']:r for r in league_calendar['matches'] if any(r[side]['id']==team_id for side in ('home_team','away_team'))}
        club_calendar=set(club_rows)
        history_ids=[];league_ids=set();played=set();friendly_ids=[]
        for row in history['matches']:
            mid=row.get('id')
            if type(mid) is not int or mid<=0 or mid in history_ids:return None
            history_ids.append(mid)
            if (row.get('player') or {}).get('team_id')!=team_id:continue
            day=datetime.fromisoformat(row['start_time'].replace('Z','+00:00'))
            if day.tzinfo is None:return None
            if not date(2026,7,1)<=day.date()<=now.date() or day>now:continue
            if row.get('is_finished') is not True or (row.get('status') or {}).get('type')!='finished':continue
            if sum((row.get(side) or {}).get('id')==team_id for side in ('home_team','away_team'))!=1:return None
            tournament=row.get('tournament') or {};competition=tournament.get('id')
            if competition in {853,889}:
                if tournament.get('name')!={853:'Club Friendly Games',889:'Telekom Cup'}[competition]:return None
                friendly_ids.append(mid);continue
            if competition not in {tid,217,7,679}:return None
            if competition==tid:
                season=row.get('season') or {};round_=row.get('round') or {}
                if (season.get('id')!=sid or season.get('year') not in {'26/27','2026/27'}
                        or round_.get('cup_round_type') or any('play' in str(round_.get(k,'')).lower() for k in ('name','slug'))):return None
                # A calendar obtained during play can be corroborated by a
                # later finished personal-history row for exactly the same game.
                calendar_row=club_rows.get(mid)
                if (calendar_row is None or row['start_time']!=calendar_row['start_time']
                        or any(row[side]['id']!=calendar_row[side]['id'] for side in ('home_team','away_team'))):return None
                league_ids.add(mid)
                minutes=((row.get('player') or {}).get('statistics') or {}).get('minutes_played')
                if type(minutes) not in (int,float) or not math.isfinite(minutes) or not 0<=minutes<=130:return None
                if minutes>0:played.add(mid)
        if not club_calendar or league_ids!=club_calendar or len(played)!=anchor['numbers']['appearances']:return None
        return {'numbers':{'appearances':0,'goals':0},'league_match_ids':sorted(club_calendar),
                'calendar_match_ids':calendars,'excluded_friendly_ids':friendly_ids,
                'playoffs_not_before':'2027-05-27','expires_on':'2027-05-27',
                'primary_sources':[DFL_CALENDAR,TELEKOM_FRIENDLY]}
    except (KeyError,TypeError,ValueError,AttributeError):return None


def recover_current_other(history,anchor,api,budget,now):
    if (anchor.get('season')!='2026/27' or anchor.get('tournament_id') not in {35,44}
            or not date(2026,8,23)<=now.date()<date(2027,5,27)
            or not all(hasattr(api,name) for name in ('tournament_seasons','tournament_matches'))):return None
    try:
        if budget['remaining']<=0:raise UpdateError('match_request_cap','Нет лимита проверки Суперкубка')
        budget['remaining']-=1
        index=api.tournament_seasons(799)
        seasons=index.get('seasons')
        if (index.get('tournament_id')!=799 or not isinstance(seasons,list)
                or type(index.get('count')) is not int or index['count']!=len(seasons)):return None
        scopes=[s for s in seasons if s.get('year') in {'26/27','2026/27'}]
        if len(scopes)!=1 or type(scopes[0].get('id')) is not int:return None
        league=api.tournament_matches(anchor['tournament_id'],anchor['season_id'],request_budget=budget)
        cup=api.tournament_matches(799,scopes[0]['id'],request_budget=budget)
        return prove_current_other(history,anchor,league,cup,now)
    except UpdateError as exc:
        if exc.code in {'quota_exhausted','api_rate_limited','api_authentication'}:budget['remaining']=0
        budget.setdefault('failures',[]).append({'code':exc.code,'season':anchor['season'],
            'team_id':anchor['team_id'],'tournament_id':799,'remaining':budget['remaining']})
        return None
    except (KeyError,TypeError,ValueError,AttributeError):return None
