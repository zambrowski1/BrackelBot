# SPDX-License-Identifier: MIT
"""Direct Highlightly football API. No inferred full roster or missing = zero."""
import json
import re
import time
from datetime import datetime
import requests
from .api_football_client import utcnow
from .api_key_pool import ApiKeyPool
from .errors import UpdateError
from .transactions import digest


class HighlightlyClient:
    provider = 'highlightly'
    endpoint = 'https://soccer.highlightly.net'
    league_id = 67162

    def __init__(self, store, entries=None, session=None, timeout=30, interval=2,
                 clock=time.time, sleep=time.sleep):
        self.store, self.session = store, session or requests.Session()
        self.clock, self.sleep = clock, sleep
        self.timeout, self.interval, self.last = timeout, max(2, interval), 0
        self.pool = ApiKeyPool(store, entries, clock=clock)
        self.used = []

    def request(self, path, params=None, *, ttl=0):
        if path not in {'/leagues','/standings','/players','/teams'} and not re.fullmatch(r'/(players|teams)/[1-9][0-9]*(/statistics)?', path):
            raise UpdateError('api_endpoint_not_allowed', 'Неподдерживаемый endpoint Highlightly')
        params = params or {}
        cache_key = digest([self.provider, path, params])
        cached = self.store.get('api_cache', cache_key)
        if ttl and cached and 0 <= self.clock() - cached['fetched_epoch'] < ttl:
            self.used.append({'path': path, 'params': params, 'cached': True})
            return cached
        # At most one attempt per configured credential. 5xx/network errors never
        # trigger key rotation. Only rejected credentials or exhausted independent
        # subscriptions can select the next entry.
        for _ in self.pool.entries:
            entry = self.pool.select()
            self.sleep(max(0, self.interval - (self.clock() - self.last)))
            self.last = self.clock()
            self.pool.reserve(entry)  # also account for ambiguous network failures
            self.used.append({'path': path, 'params': params, 'cached': False, 'key_id': entry['id']})
            try:
                response = self.session.get(self.endpoint + path, params=params,
                    headers={'x-rapidapi-key': entry['key'], 'User-Agent': 'BrackelBot/0.4'},
                    timeout=self.timeout, allow_redirects=False)
            except requests.RequestException:
                raise UpdateError('api_unavailable', 'Highlightly недоступен; повтор не выполнен') from None
            self.pool.record(entry, response.headers, response.status_code)
            if response.status_code in {401, 403}: continue
            if response.status_code == 429:
                if self.pool.quota(entry)['remaining'] == 0: continue
                raise UpdateError('api_rate_limited', 'Highlightly ограничил частоту; цикл остановлен')
            if response.status_code != 200:
                raise UpdateError('api_unavailable', f'Highlightly: HTTP {response.status_code}')
            try: data = response.json()
            except ValueError:
                raise UpdateError('api_invalid_response', 'Highlightly вернул не JSON') from None
            if not isinstance(data, (dict, list)):
                raise UpdateError('api_invalid_response', 'Неверный формат Highlightly')
            # Never persist reflected secrets or error messages from a server.
            serialized = json.dumps(data, ensure_ascii=False)
            if any(e['key'] in serialized for e in self.pool.entries):
                raise UpdateError('api_invalid_response', 'Ответ API содержит конфиденциальное значение')
            if isinstance(data, dict):
                if data.get('errors') or data.get('error') or data.get('message'):
                    raise UpdateError('api_data_unavailable', 'Highlightly сообщил об ошибке')
                if 'data' in data:
                    plan = data.get('plan', {})
                    if not isinstance(plan, dict) or plan.get('message') != 'All data available with current plan.':
                        raise UpdateError('api_plan_restricted', 'Полнота ответа не подтверждена тарифом Highlightly')
                    page = data.get('pagination', {})
                    if (not isinstance(data['data'], list) or not isinstance(page, dict) or any(type(page.get(k)) is not int or page[k] < 0
                         for k in ('totalCount', 'limit', 'offset')) or page['limit'] < 1):
                        raise UpdateError('api_incomplete', 'Неверная пагинация Highlightly')
            record = {'data': data, 'fetched_at': utcnow(), 'fetched_epoch': self.clock(), 'provider': self.provider}
            self.store.put('api_cache', cache_key, record)
            return record
        # select supplies the precise final reason without issuing another call.
        self.pool.select()
        raise UpdateError('api_authentication', 'Ключи Highlightly отклонены')

    def status(self):
        self.request('/leagues', {'leagueName': 'Bundesliga', 'countryCode': 'DE', 'limit': 100})
        return self.pool.status()

    def quota_status(self): return self.pool.status()

    def listing(self, path, params=None, ttl=86400):
        rows, times, seen, expected, offset = [], [], set(), None, 0
        while True:
            rec = self.request(path, {**(params or {}), 'limit': 1000, 'offset': offset}, ttl=ttl)
            data = rec['data']
            if not isinstance(data, dict) or 'data' not in data:
                raise UpdateError('api_incomplete', 'Нет списка Highlightly')
            page, chunk = data['pagination'], data['data']
            total = page['totalCount']
            if (page['offset'] != offset or expected is not None and total != expected
                or total > 100000 or len(rows) + len(chunk) > total or not chunk and offset < total):
                raise UpdateError('api_incomplete', 'Список изменился или оборвался во время сбора')
            expected = total
            for row in chunk:
                pid = row.get('id') if isinstance(row, dict) else None
                if type(pid) is not int or pid <= 0 or pid in seen:
                    raise UpdateError('api_duplicate_conflict', 'Повтор или неверный ID Highlightly')
                seen.add(pid)
            rows.extend(chunk); times.append(rec['fetched_at'])
            if len(rows) == total: break
            offset += len(chunk)
        return {'data': rows, 'fetched_at': min(times), 'provider': self.provider}

    def search_players(self, name): return self.listing('/players', {'name': name}, ttl=900)

    def detail(self, path, pid, *, fresh=False):
        record = self.request(path, ttl=0 if fresh else 21600)
        rows = record['data']
        if (not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict)
            or type(rows[0].get('id')) is not int or rows[0]['id'] != pid):
            raise UpdateError('api_identity_conflict', 'Ответ Highlightly не подтверждает единственный запрошенный ID')
        return rows[0], record['fetched_at']

    def profile(self, pid, fresh=False):
        row, fetched = self.detail(f'/players/{pid}', pid, fresh=fresh)
        profile = row.get('profile')
        try:
            birth = datetime.strptime(profile['birthDate'], '%d/%m/%Y').date().isoformat()
            name = profile['fullName']
            if not isinstance(name, str) or not name.strip(): raise ValueError()
        except (KeyError, TypeError, ValueError):
            raise UpdateError('api_identity_conflict', 'Нет точной даты рождения и имени игрока') from None
        return {'id': pid, 'name': name, 'birth': {'date': birth}}, row, fetched

    def scope(self, season, league_id=None):
        lid = league_id or self.league_id
        rec = self.request('/standings', {'leagueId': lid, 'season': season}, ttl=86400)
        data = rec['data']
        if not isinstance(data, dict) or data.get('league', {}).get('id') != lid or data['league'].get('season') != season:
            raise UpdateError('api_coverage_missing', 'Highlightly не подтвердил лигу и сезон')
        clubs = {}
        for group in data.get('groups', []):
            for item in group.get('standings', []):
                team = item.get('team', {})
                tid = team.get('id')
                if type(tid) is not int or tid <= 0 or tid in clubs or not isinstance(team.get('name'), str):
                    raise UpdateError('api_incomplete', 'Неоднозначный состав лиги Highlightly')
                clubs[tid] = {'id': tid, 'name': team['name'], 'national': False}
        count = {67162: 18, 68013: 18, 68864: 20}.get(lid)
        if not clubs or count and len(clubs) != count:
            raise UpdateError('api_incomplete', 'Неполный состав немецкого дивизиона')
        self.store.put('scope', f'{lid}:{season}', {'teams': list(clubs.values()), 'fetched_at': rec['fetched_at']})
        return clubs

    def team(self, tid):
        row, fetched = self.detail(f'/teams/{tid}', tid)
        if row.get('type') != 'club' or not row.get('name'):
            raise UpdateError('club_mismatch', 'Highlightly не подтвердил клуб')
        return {'data': {'response': [{'team': {'id': tid, 'name': row['name'], 'national': False}}]}, 'fetched_at': fetched}

    def current_team(self, pid, season, fresh=False):
        _, row, _ = self.profile(pid, fresh=fresh)
        current = row['profile'].get('club', {}).get('current')
        matches = [t for t in self.scope(season).values() if t['name'] == current]
        if len(matches) != 1:
            raise UpdateError('needs_review', 'Текущий клуб профиля не совпал с единственным клубом Бундеслиги')
        return matches[0]

    def transfer_dates(self, pid, fresh=False):
        _, row, _ = self.profile(pid, fresh=fresh)
        dates = []
        transfers = row.get('transfers')
        if not isinstance(transfers, list):
            raise UpdateError('api_incomplete', 'Нет истории переходов Highlightly')
        try:
            for move in transfers:
                if move.get('type') not in {'transfer', 'loan', 'end of loan', 'retired'}: raise ValueError()
                dates.append(datetime.strptime(move['transferDate'], '%b %d, %Y').date().isoformat())
            joined = row['profile']['club']['joinedAt']
            dates.append(datetime.strptime(joined, '%d/%m/%Y').date().isoformat())
        except (KeyError, ValueError, TypeError):
            raise UpdateError('needs_review', 'Неоднозначные даты переходов или прихода в клуб') from None
        return sorted(set(dates))

    def player(self, pid, season, fresh=True):
        player, _, profile_time = self.profile(pid, fresh=fresh)
        team = self.current_team(pid, season)
        stats, stat_time = self.detail(f'/players/{pid}/statistics', pid, fresh=fresh)
        competitions = stats.get('perCompetition')
        if not isinstance(competitions, list):
            raise UpdateError('api_incomplete', 'Нет статистики игрока по соревнованиям')
        label = f'{season % 100:02}/{(season + 1) % 100:02}'
        matches = [s for s in competitions if s.get('type') == 'national league' and s.get('league') == 'Bundesliga'
                   and s.get('club') == team['name'] and s.get('season') == label]
        if len(matches) != 1:
            raise UpdateError('needs_review', 'Нет единственной записи игрока за клуб, чемпионат и сезон')
        s = matches[0]
        dataset = {'provider': self.provider, 'complete': True, 'coverage': 'single_player', 'league': 78, 'season': season,
                   'source_url': f'{self.endpoint}/players/{pid}/statistics', 'fetched_at': min(profile_time, stat_time),
                   'response': [{'player': player, 'statistics': [{'team': team, 'league': {'id': 78, 'season': season},
                       'games': {'appearences': s.get('gamesPlayed')}, 'goals': {'total': s.get('goals')}}]}]}
        self.store.put('player_datasets', f'{pid}:{season}', dataset)
        return dataset
