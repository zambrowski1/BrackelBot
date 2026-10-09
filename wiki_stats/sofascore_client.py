# SPDX-License-Identifier: MIT
"""RapidAPI adapter. Quotas follow the provider's billing window, not UTC days.

The caller holds the store's run_lock across collection. A failed request is
reserved too: retrying without accounting could cross the paid overage limit.
"""
import hashlib
import json
import os
import time

import requests

from .errors import UpdateError

HOST = 'sofascore-scraper-1000-free-calls.p.rapidapi.com'
PATHS = {'/search', '/players/details', '/players/statistics-seasons',
         '/players/statistics', '/players/matches', '/players/transfer-history'}


class _SofascoreAccount:
    def __init__(self, store, key=None, session=None, clock=time.time):
        self.store = store
        self.key = key or os.environ.get('SOFASCORE_API_KEY')
        if not self.key:
            raise UpdateError('credentials_missing', 'Нужен SOFASCORE_API_KEY через Envvars')
        self.session = session or requests.Session()
        self.clock = clock
        self.account = hashlib.sha256(self.key.encode()).hexdigest()[:20]

    def quota_status(self):
        quota = self.store.get('quota', self.account, {'limit': 1000, 'remaining': 1,
                               'reset_at': None, 'confirmed': False})
        if quota.get('confirmed') and quota.get('reset_at') and self.clock()>=quota['reset_at']:
            return {'limit': 1000, 'remaining': 1, 'reset_at': None, 'confirmed': False}
        return quota

    def request(self, path, params, *, fresh=False, ttl=3600):
        if path not in PATHS:
            raise UpdateError('endpoint_invalid', 'Endpoint не разрешён адаптером')
        cache_key = hashlib.sha256(json.dumps([path, params], sort_keys=True).encode()).hexdigest()
        now = self.clock()
        cached = self.store.get('cache', cache_key)
        if not fresh and cached and 0 <= now-cached['at'] < ttl:
            return cached['data']
        quota = self.quota_status()
        # An unconfirmed window is capped at the free allowance; expired windows
        # allow one probe only, then require valid new provider headers.
        if quota.get('reset_at') and now >= quota['reset_at'] and quota.get('confirmed'):
            quota = {'limit': 1000, 'remaining': 1, 'reset_at': None, 'confirmed': False}
        if quota['remaining'] <= 0:
            raise UpdateError('quota_exhausted', 'Квота Sofascore исчерпана; платное превышение запрещено')
        quota = {**quota, 'remaining': quota['remaining']-1}
        self.store.put('quota', self.account, quota)
        try:
            response = self.session.get('https://'+HOST+path, params=params,
                headers={'x-rapidapi-key': self.key, 'x-rapidapi-host': HOST},
                timeout=30, allow_redirects=False)
        except requests.RequestException:
            raise UpdateError('api_unavailable', 'Sofascore недоступен; запрос учтён, автоматического повтора нет') from None
        headers = {k.lower(): v for k, v in response.headers.items()}
        try:
            limit = int(headers['x-ratelimit-requests-limit'])
            remaining = int(headers['x-ratelimit-requests-remaining'])
            reset = int(headers['x-ratelimit-requests-reset'])
            if not 0 <= remaining <= limit or not 0 < limit <= 1000 or reset <= 0:
                raise ValueError()
            # Don't let a stale/out-of-order response increase the same window.
            if quota.get('confirmed') and quota.get('reset_at', 0) > now:
                remaining = min(remaining, quota['remaining'])
            quota = {'limit': limit, 'remaining': remaining, 'reset_at': now+reset, 'confirmed': True}
            self.store.put('quota', self.account, quota)
        except (KeyError, ValueError, TypeError):
            pass
        if response.status_code == 429:
            if headers.get('x-ratelimit-requests-remaining')=='0':
                self.store.put('quota', self.account, {**quota, 'remaining': 0})
                raise UpdateError('quota_exhausted', 'Sofascore вернул ограничение квоты')
            try: delay = max(60, min(86400, float(headers.get('retry-after',60))))
            except (ValueError,TypeError): delay = 60
            self.store.put('api_state','cooldown',now+delay)
            raise UpdateError('api_rate_limited','Sofascore ограничил частоту; переключение ключей не выполняется')
        if response.status_code in {401,403}:
            self.store.put('key_health',self.account,{'disabled':True,'http':response.status_code})
            raise UpdateError('api_authentication','Sofascore отклонил ключ')
        if response.status_code != 200:
            raise UpdateError('api_not_found' if response.status_code==404 else 'api_unavailable',
                              'Sofascore вернул HTTP '+str(response.status_code))
        try:
            data = response.json()
        except ValueError:
            raise UpdateError('api_invalid', 'Sofascore вернул некорректный JSON') from None
        if not isinstance(data, dict) or data.get('errors') or data.get('error') or data.get('messages'):
            raise UpdateError('api_invalid', 'Sofascore не подтвердил данные')
        serialized = json.dumps(data,ensure_ascii=False)
        if any(secret in serialized for secret in getattr(self,'secrets',[self.key])):
            raise UpdateError('api_invalid','Ответ Sofascore содержит конфиденциальное значение')
        self.store.put('cache', cache_key, {'at': now, 'data': data})
        return data

    def details(self, player_id):
        data = self.request('/players/details', {'player_id': player_id})
        player = data.get('player', {})
        if player.get('id') != player_id:
            raise UpdateError('identity_mismatch', 'Sofascore вернул другого игрока')
        return player

    def search_players(self, name):
        return self.request('/search', {'query': name})

    def seasons(self, player_id):
        return self.request('/players/statistics-seasons', {'player_id': player_id}, ttl=86400)

    def statistics(self, player_id, tournament_id, season_id, part='regularSeason'):
        return self.request('/players/statistics', {'player_id': player_id,
            'tournament_id': tournament_id, 'season_id': season_id, 'type': part})

    def transfers(self, player_id):
        return self.request('/players/transfer-history', {'player_id': player_id}, ttl=86400)

    def matches(self, player_id, max_pages=12):
        matches, seen = [], set()
        for page in range(1, max_pages+1):
            data = self.request('/players/matches', {'player_id': player_id, 'span': 'last', 'page': page})
            rows = data.get('matches')
            if (data.get('player_id') != player_id or data.get('page') != page
                    or not isinstance(rows, list) or type(data.get('has_next_page')) is not bool):
                raise UpdateError('incomplete_matches', 'Неверная страница матчей Sofascore')
            ids = [r.get('id') for r in rows]
            if any(type(i) is not int or i in seen for i in ids) or len(set(ids)) != len(ids):
                raise UpdateError('incomplete_matches', 'Повторные или неоднозначные матчи Sofascore')
            seen.update(ids)
            matches.extend(rows)
            if not data['has_next_page']:
                return {'matches': matches, 'complete': True, 'pages': page}
            if not rows:
                raise UpdateError('incomplete_matches', 'Пустая промежуточная страница матчей')
        return {'matches': matches, 'complete': False, 'pages': max_pages}


class SofascoreClient(_SofascoreAccount):
    """Independent subscriptions share a cache, but each retains its quota.

    SOFASCORE_KEYS is a JSON array of keys from independently subscribed
    accounts. An explicit key argument overrides environment configuration.
    Existing single-key quota fingerprints remain unchanged on migration.
    """
    def __init__(self,store,key=None,session=None,clock=time.time,keys=None):
        if key is not None and keys is not None:
            raise UpdateError('api_key_configuration','Укажите key или keys')
        if keys is None:
            raw = os.environ.get('SOFASCORE_KEYS') if key is None else None
            try: keys = json.loads(raw) if raw else [key or os.environ.get('SOFASCORE_API_KEY')]
            except ValueError:
                raise UpdateError('api_key_configuration','SOFASCORE_KEYS должен содержать JSON-массив') from None
        if (not isinstance(keys,list) or not 1<=len(keys)<=50
                or any(not isinstance(k,str) or not k.strip() or any(c.isspace() for c in k) for k in keys)):
            raise UpdateError('api_key_configuration','Нужны 1–50 непустых ключей Sofascore')
        if len(set(keys))!=len(keys):
            raise UpdateError('api_key_configuration','Один ключ указан несколько раз')
        self.store,self.clock,self.session = store,clock,session or requests.Session()
        self.accounts = [_SofascoreAccount(store,k,self.session,clock) for k in keys]
        for account in self.accounts: account.secrets = keys

    def quota_status(self):
        rows = []
        for slot,account in enumerate(self.accounts,1):
            q = account.quota_status()
            disabled = self.store.get('key_health',account.account,{}).get('disabled',False)
            rows.append({'slot':slot,'id':account.account,**q,'disabled':disabled,
                         'usable_remaining':0 if disabled else q['remaining']})
        reset = [r['reset_at'] for r in rows if r['reset_at']]
        return {'provider':'sofascore','keys':rows,'limit':sum(r['limit'] for r in rows),
                'remaining':sum(r['usable_remaining'] for r in rows),
                'confirmed':all(r['confirmed'] for r in rows),'reset_at':min(reset) if reset else None,
                'cooldown_until':self.store.get('api_state','cooldown',0)}

    def request(self,path,params,*,fresh=False,ttl=3600):
        if path not in PATHS:
            raise UpdateError('endpoint_invalid','Endpoint не разрешён адаптером')
        cache_key = hashlib.sha256(json.dumps([path,params],sort_keys=True).encode()).hexdigest()
        cached = self.store.get('cache',cache_key)
        if not fresh and cached and 0<=self.clock()-cached['at']<ttl:
            return cached['data']
        if self.store.get('api_state','cooldown',0)>self.clock():
            raise UpdateError('api_rate_limited','Sofascore просит подождать; переключение ключей не выполняется')
        for account in self.accounts:
            if self.store.get('key_health',account.account,{}).get('disabled') or account.quota_status()['remaining']<=0:
                continue
            try:
                return account.request(path,params,fresh=fresh,ttl=ttl)
            except UpdateError as exc:
                if exc.code not in {'quota_exhausted','api_authentication'}:
                    raise
        if all(self.store.get('key_health',a.account,{}).get('disabled') for a in self.accounts):
            raise UpdateError('api_authentication','Все ключи Sofascore отклонены')
        raise UpdateError('quota_exhausted','Квоты Sofascore исчерпаны; платное превышение запрещено')

    def check_keys(self,player_id):
        """One uncached read per subscription to confirm headers and access."""
        for account in self.accounts:
            if self.store.get('api_state','cooldown',0)>self.clock():
                raise UpdateError('api_rate_limited','Sofascore просит подождать')
            if self.store.get('key_health',account.account,{}).get('disabled') or account.quota_status()['remaining']<=0:
                continue
            try:
                account.request('/players/details',{'player_id':player_id},fresh=True)
            except UpdateError as exc:
                if exc.code not in {'quota_exhausted','api_authentication'}: raise
        return self.quota_status()
