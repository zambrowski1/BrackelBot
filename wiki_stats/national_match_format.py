# SPDX-License-Identifier: MIT
"""Format independently verified senior internationals for SbMatches."""
from datetime import date
import re
from .errors import UpdateError


COMPETITIONS={1:'EURO',16:'FIFA World Cup',11:'World Cup Qual. UEFA',
              27:'EURO, Qualification',10783:'UEFA Nations League',851:'International Friendly Games'}


def competition_label(row):
    tournament=row.get('tournament') or {}
    tid=tournament.get('id')
    if type(tid) is not int or tid<=0:
        raise UpdateError('national_competition_unproven','Не подтверждено соревнование')
    name=tournament.get('name')
    if tid not in COMPETITIONS:
        if name in COMPETITIONS.values():
            raise UpdateError('national_competition_unproven','Известное название получено с другим ID')
        # Use the provider's explicit name; never invent a Russian article.
        if not isinstance(name,str) or not name.strip() or len(name)>150 or any(c in name for c in '|{}<>[]\r\n'):
            raise UpdateError('national_competition_unproven','Нет безопасного имени соревнования')
        year=str((row.get('season') or {}).get('year',''))
        if not re.fullmatch(r'(?:19|20)\d{2}(?:/(?:\d{2}|(?:19|20)\d{2}))?',year):
            raise UpdateError('national_competition_unproven','Не подтверждён сезон соревнования')
        return name.strip()+' — '+year
    if COMPETITIONS[tid]!=name:
        raise UpdateError('national_competition_unproven','Название не совпало с известным ID соревнования')
    label=str((row.get('season') or {}).get('year',''))
    if tid==851:return 'Товарищеский матч'
    if tid==10783:
        match=re.fullmatch(r'(\d{2}|20\d{2})/(\d{2}|20\d{2})',label)
        if not match:raise UpdateError('national_competition_unproven','Не подтверждён сезон Лиги наций')
        first,last=(int(match[i]) for i in (1,2))
        first+=2000 if first<100 else 0;last+=2000 if last<100 else 0
        if first<2018 or last!=first+1:raise UpdateError('national_competition_unproven','Неверный сезон Лиги наций')
        return f'Лига наций УЕФА {first}/{last}'
    if not re.fullmatch(r'(?:19|20)\d{2}',label):
        raise UpdateError('national_competition_unproven','Не подтверждён год турнира')
    year=int(label)
    if tid in (1,27):
        # UEFA EURO 2020 retained its name when played in 2021.
        # https://www.uefa.com/uefaeuro/history/news/0254-0d41684d1216-06773df7faed-1000--all-the-uefa-euro-2020-results/
        year=2020 if year==2021 else year
        if year%4:raise UpdateError('national_competition_unproven','Неверный год чемпионата Европы')
        return ('Финальные матчи' if tid==1 else 'Отборочные матчи')+f' ЧЕ-{year}'
    if year%4!=2:raise UpdateError('national_competition_unproven','Неверный год чемпионата мира')
    return ('Финальные матчи' if tid==16 else 'Отборочные матчи')+f' ЧМ-{year}'


def format_scope(scope,resolve_team,*,goalkeeper=False):
    if not isinstance(scope,dict) or scope.get('complete') is not True:
        raise UpdateError('national_history_incomplete','Нужен полностью подтверждённый список')
    team_id=scope['team']['id'];metric='пропущенные голы' if goalkeeper else 'голы'
    rows=[];sources=[];seen=set()
    for verified in scope['matches']:
        match=verified['match'];mid=match['id']
        if mid in seen:raise UpdateError('national_history_invalid','Повторный матч')
        seen.add(mid)
        if verified['numbers']['appearances']!=1:
            raise UpdateError('national_match_unproven','В список попал несыгранный матч')
        own_home=match['home_team']['id']==team_id
        opponent=match['away_team' if own_home else 'home_team']
        identity=resolve_team(opponent)
        goals=verified['numbers']['goals']
        if type(goals) is not int or (goalkeeper and goals>0) or (not goalkeeper and goals<0):
            raise UpdateError('goal_metric_mismatch','Неверная метрика голов')
        row={'дата':date.fromisoformat(verified['date']).strftime('%d-%m-%Y'),
            'соперник':identity['wikitext'],
            'счёт':str(verified['own_score'])+':'+str(verified['opponent_score']),
            metric:str(-goals if goalkeeper else goals),'соревнование':competition_label(match)}
        home_pens=(match['home_score'].get('periods') or {}).get('penalties')
        away_pens=(match['away_score'].get('periods') or {}).get('penalties')
        if home_pens is not None or away_pens is not None:
            if (type(home_pens) is not int or type(away_pens) is not int or home_pens<0 or away_pens<0
                    or home_pens==away_pens or verified['own_score']!=verified['opponent_score']):
                raise UpdateError('national_match_score_mismatch','Не подтверждена серия пенальти')
            row['пенальти']=f'{home_pens}:{away_pens}' if own_home else f'{away_pens}:{home_pens}'
        rows.append(row)
        url=match.get('link')
        if not isinstance(url,str) or not url.startswith('https://www.sofascore.com/'):
            raise UpdateError('national_match_unproven','Нет отдельной ссылки на матч')
        sources.append({'url':url,'provenance':'sofascore','coverage_as_of':scope['statistics_as_of'],
            'note':'Участие и голы подтверждены отдельными ответами; полный список сверён с итогом сборной'})
    expected=scope['numbers']
    if (len(rows)!=expected['appearances']
            or sum(int(r[metric]) for r in rows)!=(-expected['goals'] if goalkeeper else expected['goals'])):
        raise UpdateError('national_aggregate_mismatch','Список не совпадает с итогом')
    return rows,sources
