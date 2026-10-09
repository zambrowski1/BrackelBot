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


class SofascoreClient:
    def __init__(self, store, key=None, session=None, clock=time.time):
        self.store = store
        self.key = key or os.environ.get('SOFASCORE_API_KEY')
        if not self.key:
            raise UpdateError('credentials_missing', 'Нужен SOFASCORE_API_KEY через Envvars')
        self.session = session or requests.Session()
        self.clock = clock
        self.account = hashlib.sha256(self.key.encode()).hexdigest()[:20]

    def quota_status(self):
        return self.store.get('quota', self.account, {'limit': 1000, 'remaining': 1,
                             'reset_at': None, 'confirmed': False})

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
            self.store.put('quota', self.account, {**quota, 'remaining': 0})
            raise UpdateError('quota_exhausted', 'Sofascore вернул ограничение квоты')
        if response.status_code != 200:
            raise UpdateError('api_not_found' if response.status_code==404 else 'api_unavailable',
                              'Sofascore вернул HTTP '+str(response.status_code))
        try:
            data = response.json()
        except ValueError:
            raise UpdateError('api_invalid', 'Sofascore вернул некорректный JSON') from None
        if not isinstance(data, dict) or data.get('errors') or data.get('error') or data.get('messages'):
            raise UpdateError('api_invalid', 'Sofascore не подтвердил данные')
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
