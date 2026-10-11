# SPDX-License-Identifier: MIT
from datetime import datetime


def omitted_verified_appearances(records, history, secondary):
    """Identify an omitted source-list appearance using already verified timelines.

    Records must come from the internal match reconstruction, never directly
    from an external aggregate. Require all games to be played and zero goals,
    so neither unused bench entries nor uncertain goal attribution can qualify.
    """
    if history.get('complete') is not True or not records: return None
    ids = []; team_ids = set(); total = 0
    for record in records:
        numbers = record.get('numbers', {})
        matches = record.get('confirmed_match_ids')
        if (record.get('match_reconstructed') is not True or not isinstance(matches, list)
                or not matches or type(numbers.get('appearances')) is not int
                or numbers['appearances'] != len(matches) or type(numbers.get('goals')) is not int
                or numbers['goals'] != 0): return None
        ids.extend(matches); total += numbers['appearances']; team_ids.add(record['team_id'])
    if len(team_ids) != 1 or any(type(i) is not int or i <= 0 for i in ids) or len(set(ids)) != len(ids): return None
    source = secondary.get('numbers', {})
    if (type(source.get('appearances')) is not int or not 0 < source['appearances'] < total
            or type(source.get('goals')) is not int or source['goals'] != 0): return None
    selected = [m for m in history.get('matches', []) if m.get('id') in ids]
    if len(selected) != len(ids): return None
    dates = {}
    for row in selected:
        if row.get('is_finished') is not True or (row.get('status') or {}).get('type') != 'finished': return None
        if sum((row.get(side) or {}).get('id') in team_ids for side in ('home_team','away_team')) != 1: return None
        try:
            day = datetime.fromisoformat(row['start_time'].replace('Z','+00:00'))
            if day.tzinfo is None: return None
        except (KeyError, TypeError, ValueError): return None
        label = day.date().isoformat()
        if label in dates: return None
        dates[label] = row['id']
    matches = secondary.get('matches')
    if not isinstance(matches,list) or len(matches) != source['appearances']: return None
    source_dates = [m.get('date') for m in matches]
    if len(set(source_dates)) != len(source_dates) or not set(source_dates) < set(dates): return None
    return sorted(mid for day,mid in dates.items() if day not in source_dates)
