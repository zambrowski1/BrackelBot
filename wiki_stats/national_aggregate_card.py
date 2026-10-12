"""Update a bound senior card from the provider total, without exporting games."""
from datetime import datetime
from .entity_resolver import reference_identity
from .errors import UpdateError
from .wikitext_parser import WikitextParser


def prepare(snapshot, mapping, item, entity, history, wiki, now, *, goalkeeper=False):
    team = item['team']; tid = team['id']
    apps = item.get('appearances')
    metric = item.get('goals_conceded') if goalkeeper else item.get('goals')
    if type(apps) is not int or apps < 0 or type(metric) is not int or metric < 0: return []
    numbers = {'appearances':apps, 'goals':-metric if goalkeeper else metric}
    parser = WikitextParser(snapshot.text)
    rows = []
    for row in parser.career('национальная сборная',mapping['wiki_name']):
        try:
            if reference_identity(wiki,row[1]).get('qid') == entity['wikidata_id']: rows.append(row)
        except UpdateError:
            from .national_card_coverage import non_senior_row
            if not non_senior_row(row[1]): return []
    if len(rows) != 1: return []
    period,text,value = rows[0]
    from .structural_editor import validate_period
    first = validate_period(period)
    old = dict(zip(('appearances','goals'),[n.value for n in value.numeric(pair=True)]))
    if apps < old['appearances']: return []
    days = []; seen = set()
    for game in history.get('matches',[]):
        player = game.get('player') or {}
        if player.get('team_id') != tid: continue
        minutes = (player.get('statistics') or {}).get('minutes_played')
        if type(minutes) not in (int,float) or not 0 < minutes <= 130: continue
        if game.get('is_finished') is not True or (game.get('status') or {}).get('type') != 'finished': continue
        teams = [game.get(side) or {} for side in ('home_team','away_team')]
        if sum(t.get('id') == tid for t in teams) != 1 or any(t.get('is_national') is not True or (t.get('sport') or {}).get('slug') != 'football' for t in teams): continue
        try: day = datetime.fromisoformat(game['start_time'].replace('Z','+00:00'))
        except (KeyError,TypeError,ValueError): return []
        if day.tzinfo is None: return []
        if day > now: continue
        mid = game.get('id')
        if type(mid) is not int or mid <= 0 or mid in seen: return []
        seen.add(mid); days.append(day.date())
    if not days or len(days) > apps: return []
    if min(days).year < first: return []
    latest = max(days)
    box = parser.infobox(mapping['wiki_name']); old_date = box.get('обновление данных о сборной')
    old_value = old_date.text.strip() if old_date else None
    if old_value:
        try:
            if datetime.strptime(old_value,'%d.%m.%Y').date() > latest: return []
        except ValueError: return []
    base = {'as_of':now.date().isoformat(), 'group_id':'aggregate-national-card-'+str(tid),
            'entity':{**{k:v for k,v in entity.items() if k != 'gen'},'wikitext':text},
            'target':{'structure':'career','field':'национальная сборная','period':period},
            'sources':[{'url':'https://www.sofascore.com/football/player/'+str(mapping['api_id']), 'provenance':'sofascore', 'coverage_as_of':now.date().isoformat(), 'note':'Итог основной сборной из API; дата последнего подтверждённого выхода. Полный список матчей не заявляется.'}]}
    operations = []
    if old != numbers:
        operations.append({**base,'id':'aggregate-national-numbers-'+str(tid),'type':'update_stats',
            'season':None,'competition':{'name':'Матчи сборной','category':'all','scope':'national_all'},'expected':old,'new':numbers})
    if old_value != latest.strftime('%d.%m.%Y'):
        operations.append({**base,'id':'aggregate-national-date-'+str(tid),'type':'update_date',
            'payload':{'field':'обновление данных о сборной','expected_value':old_value,'statistics_as_of':latest.isoformat()}})
    return operations
