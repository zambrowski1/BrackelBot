# SPDX-License-Identifier: MIT
"""Verify a complete senior national scope before exporting any match list."""
from datetime import datetime

from .errors import UpdateError
from .sofascore_match_stats import reconstruct
from .sofascore_score import match_score


def explicit_history_goals(row):
    player=row.get('player') or {}
    statistics=player.get('statistics') or {}
    incidents=player.get('incidents') or {}
    values=[obj['goals'] for obj in (statistics,incidents) if 'goals' in obj]
    if not values:return None
    if any(type(v) is not int or v<0 for v in values) or len(set(values))!=1:return None
    return values[0]


def embedded_numbers(row, team_id, *, goalkeeper=False, goals_closed=False):
    """Reuse explicit participation; missing goals need aggregate closure."""
    player=row.get('player') or {}
    minutes=(player.get('statistics') or {}).get('minutes_played')
    if type(minutes) not in (int,float) or not 0<=minutes<=130:return None
    goals=explicit_history_goals(row)
    if minutes==0:
        return {'appearances':0,'goals':0} if goals in (None,0) else None
    if goalkeeper:
        score=match_score(row)
        if score is None:return None
        opponent=score[1] if row['home_team']['id']==team_id else score[0]
        # A shutout proves zero concessions even for a substituted keeper.
        if opponent!=0:return None
        goals=0
    elif goals is None:
        if not goals_closed:return None
        goals=0
    return {'appearances':1,'goals':goals}


def collect_national_scope(pid, team_id, history, api, budget, now, *, goalkeeper=False):
    def fail(code, mid=None):
        budget.setdefault('failures', []).append({'code':code,'team_id':team_id,
            'match_id':mid,'remaining':budget['remaining']})
        return None
    if history.get('complete') is not True:return fail('national_history_incomplete')
    if not hasattr(api,'national_statistics'):return fail('national_adapter_unsupported')
    if budget['remaining']<=0:return fail('match_request_cap')
    budget['remaining']-=1
    try:
        aggregate=api.national_statistics(pid)
        if type(aggregate.get('player_id')) is not int or aggregate['player_id']!=pid:
            return fail('national_identity_mismatch')
        statistics=aggregate.get('statistics')
        if not isinstance(statistics,list):return fail('national_aggregate_unproven')
        scopes=[s for s in statistics if isinstance(s,dict) and (s.get('team') or {}).get('id')==team_id]
        if len(scopes)!=1:return fail('national_aggregate_unproven')
        expected=scopes[0];team=expected['team']
        if (type(team.get('id')) is not int or team.get('is_national') is not True
                or (team.get('sport') or {}).get('slug')!='football'
                or any(type(expected.get(k)) is not int or expected[k]<0 for k in ('appearances','goals'))):
            return fail('national_aggregate_unproven')
        all_rows=history.get('matches')
        if not isinstance(all_rows,list):return fail('national_history_incomplete')
        rows=[];seen=set()
        for row in all_rows:
            if not isinstance(row,dict):return fail('national_history_invalid')
            if (row.get('player') or {}).get('team_id')!=team_id:continue
            mid=row.get('id')
            if type(mid) is not int or mid<=0 or mid in seen:return fail('national_history_invalid',mid)
            seen.add(mid)
            day=datetime.fromisoformat(row['start_time'].replace('Z','+00:00'))
            if day.tzinfo is None:return fail('national_history_invalid',mid)
            if day>now:continue
            if row.get('is_finished') is not True or (row.get('status') or {}).get('type')!='finished':continue
            teams=[row.get(k) or {} for k in ('home_team','away_team')]
            if (sum(t.get('id')==team_id for t in teams)!=1
                    or any(type(t.get('id')) is not int or t['id']<=0
                           or t.get('is_national') is not True
                           or (t.get('sport') or {}).get('slug')!='football' for t in teams)):
                return fail('national_match_identity_mismatch',mid)
            rows.append((day,row))
        verified=[];absences=[];total={'appearances':0,'goals':0}
        known_goals=[explicit_history_goals(row) for _,row in rows]
        invalid_goals=any(explicit_history_goals(row) is None and any('goals' in ((row.get('player') or {}).get(k) or {})
            for k in ('statistics','incidents')) for _,row in rows)
        goals_closed=not goalkeeper and not invalid_goals and sum(g for g in known_goals if g is not None)==expected['goals']
        for day,row in sorted(rows,key=lambda item:(item[0],item[1]['id'])):
            record={'team_id':team_id,'season':str((row.get('season') or {}).get('year','')),
                    'tournament_id':(row.get('tournament') or {}).get('id')}
            numbers=embedded_numbers(row,team_id,goalkeeper=goalkeeper,goals_closed=goals_closed)
            if numbers is None:
                numbers=reconstruct(api,pid,record,[row],budget,goalkeeper=goalkeeper,allow_absent=True)
            if numbers is None:return fail('national_match_unproven',row['id'])
            if numbers['appearances']==0:
                absences.append(row['id']);continue
            if numbers['appearances']!=1:return fail('national_match_unproven',row['id'])
            own_home=row['home_team']['id']==team_id
            score=match_score(row)
            if score is None:return fail('national_match_score_mismatch',row['id'])
            own_score,other_score=score if own_home else score[::-1]
            if (type(own_score) is not int or type(other_score) is not int
                    or own_score<0 or other_score<0
                    or (not goalkeeper and not 0<=numbers['goals']<=own_score)
                    or (goalkeeper and not -other_score<=numbers['goals']<=0)):
                return fail('national_match_score_mismatch',row['id'])
            for field in total:total[field]+=numbers[field]
            verified.append({'match':row,'numbers':numbers,'date':day.date().isoformat(),
                             'own_score':own_score,'opponent_score':other_score})
        if (total['appearances']!=expected['appearances']
                or not goalkeeper and total['goals']!=expected['goals']):
            return fail('national_aggregate_mismatch')
        # Aggregate "goals" means scored goals, never keeper concessions.
        # Concessions are derived only from the independently verified games.
        return {'complete':True,'team':team,'numbers':total,'matches':verified,
                'verified_absence_match_ids':absences,'source_aggregate':expected,
                'statistics_as_of':verified[-1]['date'] if verified else None}
    except UpdateError as exc:
        if exc.code in {'quota_exhausted','api_rate_limited','api_authentication'}:budget['remaining']=0
        return fail(exc.code)
    except (KeyError,TypeError,ValueError,AttributeError):
        return fail('national_source_invalid')
