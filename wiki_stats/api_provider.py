# SPDX-License-Identifier: MIT
"""Provider state is isolated; the emergency switch is shared."""
import os
from .errors import UpdateError


class ProviderStore:
    def __init__(self, store, provider):
        if provider not in {'api_football', 'highlightly', 'sofascore', 'paused'}:
            raise UpdateError('provider_invalid', 'Неизвестный источник статистики')
        self.base, self.provider = store, provider

    def bucket(self, name):
        return ({'highlightly': 'hl_', 'sofascore': 'ss_'}.get(self.provider, '') + name) if name != 'policy' else name

    def get(self, bucket, key, default=None): return self.base.get(self.bucket(bucket), key, default)
    def put(self, bucket, key, value): return self.base.put(self.bucket(bucket), key, value)
    def items(self, bucket): return self.base.items(self.bucket(bucket))
    def run_lock(self): return self.base.run_lock()
    def close(self): return self.base.close()


def make_api(store):
    provider=getattr(store,'provider',os.environ.get('BRACKELBOT_API_PROVIDER','api_football'))
    if provider=='paused':
        raise UpdateError('provider_paused','Сбор данных приостановлен оператором')
    if provider not in {'api_football','highlightly','sofascore'}:
        raise UpdateError('provider_invalid','Неизвестный источник статистики')
    if provider == 'sofascore':
        from .sofascore_client import SofascoreClient
        return SofascoreClient(store)
    if provider == 'highlightly':
        from .highlightly_client import HighlightlyClient
        return HighlightlyClient(store)
    from .api_football_client import ApiFootballClient
    return ApiFootballClient(store)
