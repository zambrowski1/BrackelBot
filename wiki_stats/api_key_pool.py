# SPDX-License-Identifier: MIT
"""Persistent quotas belong to subscriptions, not the number of credentials.

Callers hold the store's run lock across selection, request and accounting.
Secrets are only in memory. Reports contain opaque fingerprints and counters.
"""
import hashlib
import json
import os
import re
from .errors import UpdateError


class ApiKeyPool:
    def __init__(self, store, entries=None, *, clock):
        self.store, self.clock = store, clock
        if entries is None:
            raw = os.environ.get('HIGHLIGHTLY_KEYS')
            try:
                entries = json.loads(raw) if raw else [{'key': os.environ.get('HIGHLIGHTLY_API_KEY'), 'group': 'default'}]
            except ValueError:
                raise UpdateError('api_key_configuration', 'HIGHLIGHTLY_KEYS должен содержать JSON-массив') from None
        if not isinstance(entries, list) or not 1 <= len(entries) <= 50:
            raise UpdateError('api_key_configuration', 'Нужен список из 1–50 ключей')
        self.entries, seen, budgets = [], set(), {}
        for entry in entries:
            if isinstance(entry, str): entry = {'key': entry}
            if not isinstance(entry, dict):
                raise UpdateError('api_key_configuration', 'Неверная запись ключа')
            key, group, budget = entry.get('key'), entry.get('group', 'default'), entry.get('daily_budget', 100)
            if not isinstance(key, str) or not key.strip() or '\n' in key or '\r' in key:
                raise UpdateError('api_key_missing', 'Задайте HIGHLIGHTLY_API_KEY или HIGHLIGHTLY_KEYS')
            if not isinstance(group, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,40}', group):
                raise UpdateError('api_key_configuration', 'Неверный идентификатор группы квоты')
            if type(budget) is not int or not 1 <= budget <= 1000000:
                raise UpdateError('api_key_configuration', 'daily_budget должен быть положительным целым')
            if group in budgets and budgets[group] != budget:
                raise UpdateError('api_key_configuration', 'У ключей одной группы должен быть один daily_budget')
            budgets[group] = budget
            fingerprint = hashlib.sha256(key.encode()).hexdigest()[:20]
            if fingerprint in seen:
                raise UpdateError('api_key_configuration', 'Один ключ указан несколько раз')
            seen.add(fingerprint)
            # Hash group names too: a mistaken secret as a label must not be saved.
            gid = hashlib.sha256(group.encode()).hexdigest()[:20]
            binding = store.get('key_bindings', fingerprint)
            if binding and binding != gid:
                raise UpdateError('api_key_configuration', 'Изменение группы существующего ключа требует разбора квоты')
            self.entries.append({'key': key, 'id': fingerprint, 'group': gid, 'budget': budget})
        # Invalid configuration must not leave half-installed bindings.
        for entry in self.entries: store.put('key_bindings', entry['id'], entry['group'])

    def quota(self, entry):
        now = self.clock()
        day = int(now) // 86400
        state = self.store.get('key_quotas', entry['group'], {})
        if state.get('day') != day:
            state = {'day': day, 'spent': 0, 'remaining': None, 'limit': None,
                     'reset_at': (day + 1) * 86400}
        return state

    def available(self, entry):
        q = self.quota(entry)
        return min(entry['budget'] - q['spent'], q['remaining'] if q['remaining'] is not None else entry['budget'])

    def select(self):
        if self.store.get('api_state', 'cooldown', 0) > self.clock():
            raise UpdateError('api_rate_limited', 'Highlightly просит подождать; переключение ключей не снимает ограничение')
        for entry in self.entries:
            if self.store.get('key_health', entry['id'], {}).get('disabled'): continue
            if self.available(entry) > 0: return entry
        if all(self.store.get('key_health', e['id'], {}).get('disabled') for e in self.entries):
            raise UpdateError('api_authentication', 'Все настроенные ключи отклонены')
        raise UpdateError('api_quota_exhausted', 'Доступные квоты исчерпаны; сбор продолжится после сброса')

    def reserve(self, entry):
        q = self.quota(entry)
        q['spent'] += 1
        if q['remaining'] is not None: q['remaining'] = max(0, q['remaining'] - 1)
        self.store.put('key_quotas', entry['group'], q)

    def record(self, entry, headers, status):
        headers = {k.lower(): v for k, v in headers.items()}
        q = self.quota(entry)
        for header, field in [('x-ratelimit-requests-limit', 'limit'), ('x-ratelimit-requests-remaining', 'remaining')]:
            try:
                value = int(headers[header])
                if value >= 0:
                    q[field] = min(q[field], value) if field == 'remaining' and q[field] is not None else value
            except (KeyError, ValueError, TypeError): pass
        if status in {401, 403}:
            self.store.put('key_health', entry['id'], {'disabled': True, 'http': status})
        if status == 429 and q['remaining'] != 0:
            try: delay = max(60, float(headers.get('retry-after', 60)))
            except (ValueError, TypeError): delay = 60
            self.store.put('api_state', 'cooldown', self.clock() + min(delay, 86400))
        self.store.put('key_quotas', entry['group'], q)

    def status(self):
        groups = {}
        keys = []
        for slot, e in enumerate(self.entries, 1):
            q = self.quota(e)
            groups[e['group']] = {**q, 'daily_budget': e['budget'], 'usable_remaining': max(0, self.available(e))}
            disabled = self.store.get('key_health', e['id'], {}).get('disabled', False)
            keys.append({'slot': slot, 'id': e['id'], 'quota_group': e['group'], 'remaining': q['remaining'],
                         'usable_remaining': 0 if disabled else max(0, self.available(e)), 'disabled': disabled})
        for gid, q in groups.items():
            if not any(k['quota_group'] == gid and not k['disabled'] for k in keys): q['usable_remaining'] = 0
        return {'provider': 'highlightly', 'keys': keys, 'quota_groups': groups,
                'usable_remaining': sum(q['usable_remaining'] for q in groups.values()),
                'cooldown_until': self.store.get('api_state', 'cooldown', 0)}
