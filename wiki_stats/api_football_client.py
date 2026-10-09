# SPDX-License-Identifier: MIT
"""Official API-Sports client: bounded calls, persistent cache and checkpoints."""
import os
import time
from datetime import datetime, timezone
import requests
from .errors import UpdateError
from .transactions import digest


def utcnow():
    return datetime.now(timezone.utc).isoformat()


class ApiFootballClient:
    endpoint = 'https://v3.football.api-sports.io'
    allowed = {'/status','/leagues','/teams','/players','/transfers','/players/squads'}

    def __init__(self, store, key=None, session=None, timeout=30, interval=2.0, clock=time.time, sleep=time.sleep):
        self.store, self.session = store, session or requests.Session()
        key = key or os.environ.get('API_FOOTBALL_KEY')
        if not key:
            raise UpdateError('api_key_missing', 'Задайте API_FOOTBALL_KEY через Envvars или окружение')
        self.session.headers.update({'x-apisports-key':key, 'User-Agent':'BrackelBot/0.4 (operator Zambrowski)'})
        self.timeout, self.interval = timeout, max(2.0, interval)
        self.clock, self.sleep = clock, sleep
        self.last = 0.0
        self.used = []

    def request(self, path, params=None, ttl=0):
        if path not in self.allowed:
            raise UpdateError('api_endpoint_not_allowed', 'Неподдерживаемый endpoint')
        params = params or {}
        cache_key = digest([path, params])
        now = self.clock()
        cached = self.store.get('api_cache', cache_key)
        if ttl and cached and now-cached['fetched_epoch'] < ttl:
            self.used.append({'path':path,'params':params,'cached':True})
            return cached
        quota = self.store.get('api_state', 'quota', {})
        if path != '/status' and quota.get('remaining') == 0 and now < quota.get('retry_at', 0):
            raise UpdateError('api_quota_exhausted', 'Лимит исчерпан; продолжение сохранено для следующего запуска')
        wait = max(0, self.interval-(now-self.last), quota.get('minute_retry_at',0)-now)
        if wait > 60:
            raise UpdateError('api_rate_limited', 'Минутный лимит: запустите цикл позднее')
        self.sleep(wait)
        self.last = self.clock()
        self.used.append({'path':path,'params':params,'cached':False})
        try:
            response = self.session.get(self.endpoint+path, params=params, timeout=self.timeout, allow_redirects=False)
        except requests.RequestException:
            raise UpdateError('api_unavailable', 'API-Football недоступен; автоматических повторов нет') from None
        headers = {k.lower():v for k,v in response.headers.items()}
        for name, target in [('x-ratelimit-requests-remaining','remaining'), ('x-ratelimit-remaining','minute_remaining')]:
            if name in headers:
                try: quota[target] = max(0, int(headers[name]))
                except (TypeError, ValueError): pass
        if quota.get('remaining') == 0:
            quota['retry_at'] = (int(self.clock())//86400+1)*86400
        if quota.get('minute_remaining') == 0:
            quota['minute_retry_at'] = self.clock()+60
        self.store.put('api_state','quota',quota)
        if response.status_code in {401,403}:
            raise UpdateError('api_authentication', 'API-Football отклонил ключ')
        if response.status_code == 429:
            raise UpdateError('api_rate_limited', 'API-Football ограничил запросы; цикл остановлен')
        if response.status_code != 200:
            raise UpdateError('api_unavailable', f'API-Football: HTTP {response.status_code}; цикл остановлен')
        try: data = response.json()
        except ValueError:
            raise UpdateError('api_invalid_response','API вернул не JSON') from None
        if not isinstance(data,dict):
            raise UpdateError('api_invalid_response','API вернул неожиданный формат')
        errors = data.get('errors')
        if errors:
            # Never echo arbitrary server strings: they can reflect the key.
            names = set(errors) if isinstance(errors,dict) else set()
            if 'plan' in names:
                raise UpdateError('api_plan_restricted','Тариф API-Football не разрешает этот запрос или сезон')
            code = 'api_authentication' if names & {'token','key','authentication'} else ('api_quota_exhausted' if names & {'requests','rateLimit'} else 'api_data_unavailable')
            raise UpdateError(code,'API сообщил об ошибке или недоступном покрытии')
        if path == '/status':
            body = data.get('response',{})
            if not isinstance(body,dict) or not isinstance(body.get('requests'),dict):
                raise UpdateError('api_invalid_response','Нет сведений о запросах аккаунта')
            daily = body['requests']
            if type(daily.get('current')) is not int or type(daily.get('limit_day')) is not int or daily['current']<0 or daily['limit_day']<=0:
                raise UpdateError('api_invalid_response','Лимит и расход аккаунта не подтверждены числовыми полями')
            if type(daily.get('current')) is int and type(daily.get('limit_day')) is int:
                quota['remaining']=max(0,daily['limit_day']-daily['current'])
                if not quota['remaining']: quota['retry_at']=(int(self.clock())//86400+1)*86400
                self.store.put('api_state','quota',quota)
            # Strip account name/email; only subscription and quota are needed.
            data = {'response':{'requests':daily,'subscription':body.get('subscription',{})}}
        else:
            paging = data.get('paging',{})
            if (data.get('get') != path.lstrip('/') or not isinstance(data.get('response'),list)
                or type(data.get('results')) is not int or data['results'] != len(data['response'])
                or type(paging.get('current')) is not int or type(paging.get('total')) is not int
                or not 1 <= paging['current'] <= paging['total'] <= 10000):
                raise UpdateError('api_invalid_response','Неверная структура ответа или пагинация')
        record = {'data':data,'fetched_at':utcnow(),'fetched_epoch':self.clock()}
        self.store.put('api_cache',cache_key,record)
        return record

    def status(self): return self.request('/status')['data']['response']
    def _single(self,path,params,ttl=0):
        record=self.request(path,params,ttl=ttl)
        if record['data']['paging']['current']!=1 or record['data']['paging']['total']!=1:
            raise UpdateError('api_incomplete','Ожидался полный непагинируемый endpoint; частичный ответ отвергнут')
        return record
    def leagues(self, season): return self._single('/leagues',{'id':78,'season':season},ttl=86400)
    def teams(self, season): return self._single('/teams',{'league':78,'season':season},ttl=86400)
    def transfers(self, player_id, fresh=False): return self._single('/transfers',{'player':player_id},ttl=0 if fresh else 21600)
    def squads(self, team_id, fresh=False): return self._single('/players/squads',{'team':team_id},ttl=0 if fresh else 21600)
    def player(self, player_id, season):
        record=self.request('/players',{'id':player_id,'season':season})
        if record['data']['paging']!={'current':1,'total':1}:
            raise UpdateError('api_incomplete','Неоднозначная пагинация одного игрока')
        return {'complete':True,'league':78,'season':season,'response':record['data']['response'],'fetched_at':record['fetched_at']}

    def players(self, season):
        # Only complete datasets can escape this method. Partial pages are cached
        # so a quota outage resumes without treating absence as departure.
        key = str(season)
        self.store.put('api_checkpoint',key,{'complete':False,'next_page':1,'started_at':utcnow()})
        rows, times, total = [], [], None
        page_hashes = set()
        page = 1
        while True:
            record = self.request('/players',{'league':78,'season':season,'page':page},ttl=21600)
            data = record['data']
            if data['paging']['current'] != page or (total is not None and data['paging']['total'] != total):
                raise UpdateError('api_incomplete','Количество страниц изменилось; повторите сбор после истечения кэша')
            total = data['paging']['total']
            if not data['response']:
                raise UpdateError('api_incomplete','Пустая выборка игроков не является составом лиги')
            page_hash = digest(data['response'])
            if page_hash in page_hashes:
                raise UpdateError('api_incomplete','API повторил содержимое страницы; полнота списка игроков не подтверждена')
            page_hashes.add(page_hash)
            rows.extend(data['response']);times.append(record['fetched_at'])
            self.store.put('api_checkpoint',key,{'complete':False,'next_page':page+1,'total':total})
            if page == total: break
            page += 1
        dataset = {'league':78,'season':season,'complete':True,'response':rows,
                   'fetched_at':min(times),'latest_fetched_at':max(times),'pages':total}
        self.store.put('datasets',key,dataset)
        self.store.put('api_checkpoint',key,{'complete':True,'next_page':total+1,'total':total})
        return dataset
