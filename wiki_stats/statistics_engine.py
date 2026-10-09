# SPDX-License-Identifier: MIT
"""Season observations are never career totals. Anchors are immutable per stint."""
import re
from datetime import date, datetime, timezone
from .errors import UpdateError


def pair_valid(value):
    return (isinstance(value,dict) and set(value)=={'appearances','goals'}
            and all(type(n) is int and 0<=n<=10000 for n in value.values()))


def validate_anchor(anchor):
    needed={'team_id','season','period','career_wikitext','career','season_stats','as_of','evidence_url'}
    if not isinstance(anchor,dict) or not needed <= set(anchor):
        raise UpdateError('anchor_invalid','База требует клуб, сезон, точную строку, обе пары и подтверждающий источник')
    if not pair_valid(anchor['career']) or not pair_valid(anchor['season_stats']):
        raise UpdateError('anchor_invalid','База содержит неизвестные или неверные числа')
    if any(anchor['career'][k] < anchor['season_stats'][k] for k in anchor['career']):
        raise UpdateError('anchor_invalid','Карьерный итог меньше включённого сезонного показателя')
    if (type(anchor['team_id']) is not int or anchor['team_id']<=0 or type(anchor['season']) is not int
        or not 1000 <= anchor['season'] <= 9999 or not isinstance(anchor['period'],str)
        or not re.fullmatch(r'\d{4}—(?:\d{4}|\{\{н\.в\.\}\})',anchor['period'])
        or not isinstance(anchor['career_wikitext'],str) or not anchor['career_wikitext'].strip()
        or not isinstance(anchor['evidence_url'],str) or not re.match(r'^https://[^/\s]+/',anchor['evidence_url'])):
        raise UpdateError('anchor_invalid','База не указывает однозначный период и источник')
    try:
        day = date.fromisoformat(anchor['as_of'])
        start, end = anchor['period'].split('—')
        if (day>date.today() or int(start)>day.year or int(start)>anchor['season']+1
            or end.isdigit() and int(end)<int(start)):
            raise ValueError()
    except (ValueError,TypeError): raise UpdateError('anchor_invalid','Неверная дата базы') from None


def normalize_dataset(dataset, *, expected_season=None):
    if not isinstance(dataset,dict) or dataset.get('complete') is not True or type(dataset.get('league')) is not int or dataset.get('league')!=78:
        raise UpdateError('api_incomplete','Нельзя обрабатывать неполный сбор лиги')
    try:
        if (type(dataset['season']) is not int or not 1000 <= dataset['season'] <= 9999
            or expected_season is not None and dataset['season'] != expected_season
            or not isinstance(dataset['response'],list)):
            raise ValueError()
        fetched = datetime.fromisoformat(dataset['fetched_at'].replace('Z','+00:00'))
        if fetched.tzinfo is None or fetched > datetime.now(timezone.utc):
            raise ValueError()
    except (KeyError,TypeError,ValueError,AttributeError):
        raise UpdateError('api_invalid_response','Сезон, время получения или список игроков не подтверждены') from None
    observations, players = {}, {}
    for row in dataset['response']:
        try:
            player = row['player']; pid=player['id']
            if type(pid) is not int or pid<=0 or not isinstance(player['name'],str) or not player['name'].strip() or not isinstance(row['statistics'],list): raise ValueError()
            if pid in players and players[pid] != player:
                raise UpdateError('api_identity_conflict','Противоречивые профили одного API ID')
            players[pid] = player
            for stat in row['statistics']:
                league=stat['league'];team=stat['team'];tid=team['id']
                # Cubical/national records do not come from a Bundesliga query.
                if league['id']!=78 or league['season']!=dataset['season']: continue
                if type(tid) is not int or tid<=0 or not isinstance(team['name'],str) or not team['name'].strip(): raise ValueError()
                if not isinstance(stat['games'],dict) or not isinstance(stat['goals'],dict): raise ValueError()
                pair={'appearances':stat['games'].get('appearences'),'goals':stat['goals'].get('total')}
                if any(v is not None and (type(v) is not int or v<0 or v>10000) for v in pair.values()): raise ValueError()
                key=(pid,tid,78,dataset['season'])
                value={'api_id':pid,'team_id':tid,'team_name':team['name'],'league':78,
                       'season':dataset['season'],'stats':pair,'fetched_at':dataset['fetched_at'],
                       'status':'known' if pair_valid(pair) else 'needs_review'}
                if key in observations and observations[key] != value:
                    raise UpdateError('api_duplicate_conflict','Разные статистические записи одной команды/сезона; сложение запрещено')
                observations[key]=value
        except (KeyError,TypeError,ValueError,AttributeError):
            raise UpdateError('api_invalid_response','Неполный или неверный профиль игрока') from None
    if not observations:
        raise UpdateError('api_incomplete','Нет статистики выбранного сезона Бундеслиги')
    return players, list(observations.values())


def career_from_anchor(anchor, observation):
    validate_anchor(anchor)
    if (anchor['team_id'],anchor['season']) != (observation['team_id'],observation['season']):
        raise UpdateError('needs_review','База относится к другому клубу или сезону')
    if not pair_valid(observation['stats']):
        raise UpdateError('needs_review','null в API не является нулём')
    if observation['fetched_at'][:10] < anchor['as_of']:
        raise UpdateError('needs_review','Наблюдение старше подтверждённой базы')
    if any(observation['stats'][k] < anchor['season_stats'][k] for k in anchor['career']):
        raise UpdateError('needs_review','Сезонные данные уменьшились; требуется проверка исправления API')
    return {k:anchor['career'][k]+observation['stats'][k]-anchor['season_stats'][k] for k in anchor['career']}


def update_operation(mapping, anchor, observation, old):
    new = career_from_anchor(anchor,observation)
    if not pair_valid(old): raise UpdateError('needs_review','Исходные карьерные числа неизвестны')
    if any(new[k]<old[k] for k in new):
        raise UpdateError('needs_review','Вычисленный итог меньше Википедии; требуется проверка')
    as_of=observation['fetched_at'][:10]
    return {'id':f"api-{mapping['api_id']}-{observation['team_id']}-{observation['season']}",
            'type':'update_stats','entity':{'name':observation['team_name'],'wikitext':anchor['career_wikitext']},
            'target':{'structure':'career','field':'клубы','period':anchor['period']},'season':None,
            'competition':{'name':'Чемпионат','category':'all','scope':'league'},
            'expected':old,'new':new,'as_of':as_of,'sources':[
                {'url':f"https://v3.football.api-sports.io/players?id={mapping['api_id']}&season={observation['season']}",
                 'provenance':'api_football','coverage_as_of':as_of,
                 'note':'Дата получения API, не подтверждённая дата последнего матча; карьерный итог = база + дельта сезона.'},
                {'url':anchor['evidence_url'],'provenance':'api_football' if anchor.get('transfer_plan_hash') else 'user_provided','coverage_as_of':as_of,
                 'note':f"Подтверждённая оператором база от {anchor['as_of']}; {anchor['career']} / {anchor['season_stats']}"}]}
