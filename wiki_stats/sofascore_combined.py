# SPDX-License-Identifier: MIT
"""Two delivery providers, one Sofascore identity space and normalized cache.

Bulk career data is deliberately not accepted as authoritative season data:
the live probe found contradictory values and omitted old/reserve seasons.
"""
import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone

from .errors import UpdateError
from .sofascore_client import _SofascoreAccount, SofascoreClient, PATHS


def snake(value):
    if isinstance(value, list): return [snake(v) for v in value]
    if isinstance(value, dict):
        return {re.sub(r'(?<!^)(?=[A-Z])', '_', k).lower():snake(v) for k,v in value.items()}
    return value


def stamp(value):
    if type(value) not in (int, float):
        raise UpdateError('api_invalid', 'SofaScore6 не подтвердил дату')
    try: return datetime.fromtimestamp(value, timezone.utc).isoformat().replace('+00:00','Z')
    except (ValueError, OverflowError, OSError):
        raise UpdateError('api_invalid', 'Неверная дата SofaScore6') from None


def team(value):
    if not isinstance(value,dict): raise UpdateError('api_invalid','Не подтверждена команда SofaScore6')
    result=snake(value)
    result['is_national']=value.get('national',False)
    result.setdefault('sport',{'slug':'football'})
    return result


def game(value, *, player=False):
    result=snake(value)
    result['start_time']=stamp(value.get('timestamp'))
    result['home_team']=team(value.get('homeTeam'))
    result['away_team']=team(value.get('awayTeam'))
    # The cycle uses the unique tournament ID, not its stage-specific ID.
    result['tournament']=snake(value.get('uniqueTournament') or value.get('tournament'))
    result['is_finished']=value.get('status',{}).get('type')=='finished'
    if player:
        result['player']={'statistics':snake(value.get('stats',{})),
                          'is_on_bench':value.get('stats',{}).get('onBench')}
        # This endpoint does not prove which club the player represented.
        # Do not infer it from home/away sides. Lineup checks remain required.
    return result


class Sofa6Account(_SofascoreAccount):
    host='sofascore6.p.rapidapi.com'
    quota_limit=500
    cooldown_key='sofascore6-cooldown'

    def __init__(self,store,key,session=None,clock=time.time):
        super().__init__(store,key,session,clock)
        self.account='sofascore6:'+self.account

    def wire_request(self,path,params):
        routes={'/players/details':'player/details',
                '/players/statistics-seasons':'player/statistics/seasons',
                '/players/national-team-statistics':'player/national-team-statistics',
                '/players/transfer-history':'player/transfer-history',
                '/players/statistics':'player/statistics'}
        if path not in routes:
            raise UpdateError('endpoint_invalid','Этот метод выполняет резервный провайдер')
        wire=dict(params)
        if path=='/players/statistics':
            if params.get('type')!='overall':
                raise UpdateError('endpoint_invalid','Раздельные стадии проверяет OmkarCloud')
            wire={'player_id':params['player_id'],'unique_tournament_id':params['tournament_id'],'season_id':params['season_id']}
        return '/api/sofascore/v1/'+routes[path],wire

    def normalize_response(self,path,params,data):
        if isinstance(data,dict) and any(data.get(k) for k in ('error','errors','messages')):
            raise UpdateError('api_invalid','SofaScore6 не подтвердил ответ')
        pid=params['player_id']
        if path=='/players/details':
            if not isinstance(data,dict) or 'id' not in data:
                raise UpdateError('api_invalid','Нет профиля SofaScore6')
            if data.get('id')!=pid or data.get('position') not in {'G','D','M','F'}:
                raise UpdateError('identity_mismatch','SofaScore6 не подтвердил футболиста')
            result=snake(data);result['date_of_birth']=stamp(data.get('dateOfBirthTimestamp'))[:10]
            result['jersey_number']=data.get('shirtNumber') or data.get('jerseyNumber')
            result['team']=team(data.get('team'));return {'player':result}
        if path in {'/players/statistics-seasons','/players/national-team-statistics','/players/transfer-history'}:
            if not isinstance(data,list):raise UpdateError('api_invalid','Ожидался список SofaScore6')
            if path=='/players/statistics-seasons':
                return {'player_id':pid,'count':len(data),'tournament_seasons':[
                    {'tournament':snake(r['uniqueTournament']),'seasons':snake(r['seasons'])} for r in data]}
            if path=='/players/national-team-statistics':
                return {'player_id':pid,'count':len(data),'statistics':[
                    {**snake(r),'team':team(r['team'])} for r in data]}
            transfers=[]
            for r in data:
                transfers.append({**snake(r),'transfer_from':team(r['transferFrom']),
                    'transfer_to':team(r['transferTo']),'transfer_date':stamp(r['transferDateTimestamp'])[:10]})
            return {'player_id':pid,'count':len(data),'transfers':transfers}
        if path=='/players/statistics':
            if not isinstance(data,dict) or not isinstance(data.get('statistics'),dict):
                raise UpdateError('api_invalid','Нет сезонной статистики SofaScore6')
            if any(type(data['statistics'].get(k)) is not int or data['statistics'][k]<0
                   for k in ('appearances','goals')):
                raise UpdateError('api_invalid','Нет подтверждённых матчей и голов SofaScore6')
            return {'player_id':pid,'tournament_id':params['tournament_id'],'season_id':params['season_id'],
                    'type':'overall','statistics':snake(data['statistics']),'team':team(data.get('team'))}
        if path=='/players/matches':
            if not isinstance(data,dict) or not isinstance(data.get('matches'),list) or type(data.get('hasNextPage')) is not bool:
                raise UpdateError('api_invalid','Нет страницы матчей SofaScore6')
            rows=[game(r,player=True) for r in data['matches']]
            return {'player_id':pid,'page':params['page'],'span':'last','matches':rows,
                    'count':len(rows),'has_next_page':data['hasNextPage']}
        raise UpdateError('endpoint_invalid','Метод не поддерживается')


class CombinedSofascoreClient(_SofascoreAccount):
    def __init__(self,store,primary_key=None,fallback_keys=None,session=None,clock=time.time):
        self.store,self.clock=store,clock
        key=primary_key or os.environ.get('SOFASCORE6_API_KEY')
        if not isinstance(key,str) or not key or any(c.isspace() for c in key):
            raise UpdateError('api_key_configuration','Нужен SOFASCORE6_API_KEY')
        self.primary=Sofa6Account(store,key,session,clock)
        self.fallback=SofascoreClient(store,keys=fallback_keys,session=session,clock=clock)
        self.secrets=[key]+[a.key for a in self.fallback.accounts]
        self.primary.secrets=self.secrets
        for account in self.fallback.accounts:account.secrets=self.secrets

    def request(self,path,params,*,fresh=False,ttl=3600):
        if path not in PATHS:raise UpdateError('endpoint_invalid','Метод не разрешён')
        # Successful semantic responses share the old cache; quotas do not.
        cache_key=hashlib.sha256(json.dumps([path,params],sort_keys=True).encode()).hexdigest()
        cached=self.store.get('cache',cache_key)
        if not fresh and cached and 0<=self.clock()-cached['at']<ttl:return cached['data']
        q=self.primary.quota_status()
        unavailable=(q['remaining']<=0 or self.store.get('key_health',self.primary.account,{}).get('disabled')
                     or self.store.get('api_state',self.primary.cooldown_key,0)>self.clock())
        if not unavailable:
            try:return self.primary.request(path,params,fresh=fresh,ttl=ttl)
            except UpdateError as exc:
                if exc.code not in {'endpoint_invalid','api_invalid','api_not_found','api_unavailable',
                                    'api_authentication','api_rate_limited','quota_exhausted'}:raise
                self.store.put('source_fallback',cache_key,{'at':self.clock(),'from':'sofascore6','to':'omkarcloud','reason':exc.code})
            except (KeyError,TypeError):
                self.store.put('source_fallback',cache_key,{'at':self.clock(),'from':'sofascore6','to':'omkarcloud','reason':'api_invalid'})
        return self.fallback.request(path,params,fresh=fresh,ttl=ttl)

    def quota_status(self):
        primary=self.primary.quota_status();fallback=self.fallback.quota_status()
        disabled=self.store.get('key_health',self.primary.account,{}).get('disabled',False)
        rows=[{'slot':1,'id':self.primary.account,'source':'sofascore6',**primary,'disabled':disabled,
               'usable_remaining':0 if disabled else primary['remaining']}]
        rows += [{**r,'slot':i+2,'source':'omkarcloud'} for i,r in enumerate(fallback['keys'])]
        return {'provider':'sofascore','sources':{'sofascore6':primary,'omkarcloud':fallback},'keys':rows,
                'limit':sum(r['limit'] for r in rows),'remaining':sum(r['usable_remaining'] for r in rows),
                'confirmed':all(r['confirmed'] for r in rows),'reset_at':min((r['reset_at'] for r in rows if r['reset_at']),default=None)}

    def check_keys(self,player_id):
        if (self.primary.quota_status()['remaining']>0
                and not self.store.get('key_health',self.primary.account,{}).get('disabled')
                and self.store.get('api_state',self.primary.cooldown_key,0)<=self.clock()):
            try:self.primary.request('/players/details',{'player_id':player_id},fresh=True)
            except UpdateError as exc:
                if exc.code not in {'api_authentication','quota_exhausted','api_unavailable','api_not_found','api_invalid','api_rate_limited'}:raise
        self.fallback.check_keys(player_id)
        return self.quota_status()


def make_sofascore_client(store):
    if os.environ.get('SOFASCORE6_API_KEY'):
        return CombinedSofascoreClient(store)
    return SofascoreClient(store)
