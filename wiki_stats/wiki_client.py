# SPDX-License-Identifier: MIT
import json
import time
from pathlib import Path
import requests
from .errors import UpdateError
from .models import PageSnapshot


class WikiClient:
    """Action API adapter; local publication remains sandbox-only by default."""
    endpoint = "https://ru.wikipedia.org/w/api.php"

    def __init__(self, session=None, interval=1.0, timeout=30, user_agent=None, publication_policy=None):
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": user_agent or "BrackelBot/0.4 (football statistics; Wikipedia user Zambrowski)"})
        self.interval = max(1.0, interval)
        self.timeout = timeout
        self.last_request = 0.0
        self.authenticated_user = None
        self.publication_policy = publication_policy

    def fetch_page(self, title):
        params = {"action": "query", "format": "json", "formatversion": 2, "prop": "revisions|info",
                  "rvprop": "ids|timestamp|content", "rvslots": "main", "titles": title,
                  "inprop": "protection", "maxlag": 5, "curtimestamp":1}
        for attempt in range(3):
            time.sleep(max(0, self.interval - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            try:
                response = self.session.get(self.endpoint, params=params, timeout=self.timeout)
                if response.status_code in {401, 403}:
                    self.authenticated_user=None
                    raise UpdateError("authorization_error", "API отказал в доступе")
                if response.status_code == 429:
                    raise UpdateError("rate_limited", "API ограничил частоту; обработка остановлена")
                response.raise_for_status()
                data = response.json()
                if "error" in data:
                    error = data["error"]
                    code = error.get("code", "mediawiki_error")
                    if code == "maxlag" and attempt < 2:
                        time.sleep(2*(attempt+1))
                        continue
                    mapped = "authorization_error" if code in {"readapidenied", "permissiondenied", "assertuserfailed"} else "mediawiki_error"
                    if mapped=='authorization_error': self.authenticated_user=None
                    raise UpdateError(mapped, f"{code}: {error.get('info', '')}")
                page = data["query"]["pages"][0]
                if "missing" in page:
                    raise UpdateError("page_not_found", "Статья не существует")
                if "redirect" in page:
                    raise UpdateError("redirect_not_supported", "Укажите точное название статьи, а не перенаправление")
                if not (page.get("ns") == 0 or (page.get("ns") == 2 and page.get("title") == "Участник:Zambrowski/testbot")):
                    raise UpdateError("namespace_not_supported", "Разрешены статьи и только согласованная личная тестовая страница")
                rev = page["revisions"][0]
                return PageSnapshot(page["title"], rev["revid"], rev["timestamp"], rev["slots"]["main"]["content"], page.get('ns',0), data.get('curtimestamp'))
            except (requests.RequestException, ValueError) as exc:
                if attempt == 2:
                    raise UpdateError("connection_error", f"Не удалось получить страницу: {exc}") from exc
                time.sleep(2*(attempt+1))
            except (KeyError, IndexError, TypeError) as exc:
                raise UpdateError("invalid_api_response", "В ответе API нет доступного викитекста") from exc
        raise UpdateError("connection_error", "Исчерпаны попытки получения страницы")

    def _api(self, params, post=False, endpoint=None):
        url = endpoint or self.endpoint
        if post and url != 'https://ru.wikipedia.org/w/api.php':
            raise UpdateError('endpoint_not_allowed', 'Запись разрешена только через API русской Википедии')
        if post and params.get('action')=='edit':
            if self.publication_policy:
                self.publication_policy.gate_page(params.get('title'))
            elif params.get('title')!='Участник:Zambrowski/testbot':
                raise UpdateError('page_not_allowed','Запись в основное пространство запрещена на уровне API-клиента')
        time.sleep(max(0, self.interval-(time.monotonic()-self.last_request)))
        self.last_request = time.monotonic()
        params = {'format':'json','formatversion':2, **params}
        try:
            response = self.session.post(url, data=params, timeout=self.timeout) if post else self.session.get(url, params=params, timeout=self.timeout)
            if response.status_code in {401,403}:
                self.authenticated_user = None
                raise UpdateError('authorization_error','API отказал в доступе; сессия остановлена')
            if response.status_code == 429:
                raise UpdateError('rate_limited','API ограничил частоту; повторная запись не выполняется')
            response.raise_for_status()
            result = response.json()
        except (requests.RequestException, ValueError) as exc:
            # Never include POST data, response text or credentials in diagnostics.
            code = 'publication_unknown' if post and params.get('action') == 'edit' else 'connection_error'
            raise UpdateError(code, 'Нет подтверждённого ответа API. Проверьте страницу заново; запрос не повторяется.') from None
        if not isinstance(result,dict):
            raise UpdateError('invalid_api_response','Неожиданный формат ответа API')
        if 'error' in result:
            code = result['error'].get('code','mediawiki_error')
            if code in {'assertuserfailed','assertnameduserfailed','notloggedin','badtoken','permissiondenied','readapidenied'}:
                self.authenticated_user = None
                raise UpdateError('authorization_error',f'Авторизация остановлена: {code}')
            if code in {'editconflict','pagedeleted','articleexists'}:
                raise UpdateError('revision_conflict','Конфликт редактирования; требуется новый просмотр и подтверждение')
            if code in {'protectedpage','cascadeprotected','blocked','autoblocked','abusefilter-disallowed','spamblacklist','captcha-required'}:
                raise UpdateError('edit_rejected',f'Правка отклонена Википедией: {code}')
            raise UpdateError('mediawiki_error',f'Ошибка API: {code}')
        return result

    def get_page_identity(self, title, namespace=0):
        data = self._api({'action':'query','prop':'pageprops','ppprop':'wikibase_item','redirects':1,'titles':title})
        try:
            page = data['query']['pages'][0]
            if 'missing' in page or page.get('ns') != namespace:
                raise UpdateError('entity_not_found','Статья клуба/сборной не найдена')
            return {'title':page['title'],'qid':page.get('pageprops',{}).get('wikibase_item')}
        except (KeyError,IndexError,TypeError):
            raise UpdateError('invalid_api_response','Нет данных о статье клуба/сборной') from None

    def get_foreign_page_identity(self,title,language):
        if language not in {'de','en'}:
            raise UpdateError('endpoint_not_allowed','Для клубов разрешены немецкая и английская Википедии')
        data=self._api({'action':'query','prop':'pageprops','ppprop':'wikibase_item','redirects':1,'titles':title},
                       endpoint=f'https://{language}.wikipedia.org/w/api.php')
        try:
            page=data['query']['pages'][0]
            if 'missing' in page or page.get('ns')!=0:
                raise UpdateError('entity_not_found','Иноязычная статья клуба не найдена')
            return {'title':page['title'],'qid':page.get('pageprops',{}).get('wikibase_item')}
        except (KeyError,IndexError,TypeError):
            raise UpdateError('invalid_api_response','Нет данных об иноязычной статье') from None

    def get_wikidata_entity(self, qid):
        data = self._api({'action':'wbgetentities','ids':qid,'props':'sitelinks|claims|labels|aliases','languages':'ru|en|de'},endpoint='https://www.wikidata.org/w/api.php')
        try:
            entity = data['entities'][qid]
            if 'missing' in entity:
                raise UpdateError('entity_not_found','Объект Wikidata не найден')
            return entity
        except (KeyError,TypeError):
            raise UpdateError('invalid_api_response','Нет объекта Wikidata') from None

    def verify_authenticated(self):
        if not self.authenticated_user:
            raise UpdateError('authorization_required','Сначала войдите через BotPasswords')
        data = self._api({'action':'query','meta':'userinfo','assert':'user','assertuser':self.authenticated_user})
        user = data.get('query',{}).get('userinfo',{})
        if not user.get('id') or user.get('name') != self.authenticated_user:
            self.authenticated_user = None
            raise UpdateError('authorization_error','Авторизация утрачена; публикация остановлена')

    def login_botpassword(self, username, password):
        self.logout_local()
        if '@' not in username or not password:
            raise UpdateError('authorization_error','Используйте имя вида Участник@BrackelBot и пароль бота')
        try:
            token = self._api({'action':'query','meta':'tokens','type':'login'})['query']['tokens']['logintoken']
            result = self._api({'action':'login','lgname':username,'lgpassword':password,'lgtoken':token},post=True)
            if result.get('login',{}).get('result') != 'Success':
                raise UpdateError('authorization_error','Вход не выполнен. Проверьте BotPassword; данные не сохранены.')
            user = self._api({'action':'query','meta':'userinfo'})['query']['userinfo']
            expected = username.split('@',1)[0].replace('_',' ')
            expected = expected[:1].upper()+expected[1:]
            if not user.get('id') or user.get('name') != expected:
                raise UpdateError('authorization_error','Учётная запись не соответствует имени BotPassword')
            self.authenticated_user = user['name']
            return self.authenticated_user
        except (UpdateError,KeyError,TypeError):
            self.logout_local()
            raise UpdateError('authorization_error','Вход не выполнен; пароль и токены не сохранены') from None
        finally:
            password = None

    def logout_local(self):
        self.authenticated_user = None
        self.session.cookies.clear()

    def save_test_page(self, snapshot, text, summary):
        # A second independent hard gate, even if the publisher is bypassed.
        if snapshot.title != 'Участник:Zambrowski/testbot' or snapshot.namespace != 2:
            raise UpdateError('page_not_allowed','Запись разрешена только на Участник:Zambrowski/testbot')
        return self._save_page(snapshot,text,summary)

    def save_allowed_page(self, snapshot, text, summary):
        if not self.publication_policy:
            raise UpdateError('page_not_allowed','Не установлена политика серверной публикации')
        self.publication_policy.gate_page(snapshot.title)
        if not (snapshot.namespace==0 or (snapshot.title=='Участник:Zambrowski/testbot' and snapshot.namespace==2)):
            raise UpdateError('page_not_allowed','Пространство страницы не разрешено')
        return self._save_page(snapshot,text,summary)

    def _save_page(self, snapshot, text, summary):
        self.verify_authenticated()
        data = self._api({'action':'query','meta':'tokens','type':'csrf','assert':'user','assertuser':self.authenticated_user})
        token = data.get('query',{}).get('tokens',{}).get('csrftoken')
        if not token or token == '+\\':
            raise UpdateError('authorization_error','Не получен CSRF-токен')
        params = {'action':'edit','title':snapshot.title,'text':text,'summary':summary,'token':token,
                  'baserevid':snapshot.revid,'basetimestamp':snapshot.timestamp,'assert':'user',
                  'assertuser':self.authenticated_user,'nocreate':1,'watchlist':'nochange','maxlag':5}
        if snapshot.starttimestamp:
            params['starttimestamp'] = snapshot.starttimestamp
        if self.publication_policy and self.publication_policy.mode=='automatic' and snapshot.namespace==0:
            params['bot']=1
        result = self._api(params,post=True)
        edit = result.get('edit',{})
        if edit.get('result') != 'Success':
            code = 'captcha_required' if 'captcha' in edit else 'edit_rejected'
            raise UpdateError(code,'API не подтвердил сохранение; запрос не повторяется')
        return {'title':snapshot.title,'oldrevid':snapshot.revid,'newrevid':edit.get('newrevid',snapshot.revid),'nochange':'nochange' in edit}


class FixtureClient:
    """Offline replay of captured revisions; never substitutes for a live client implicitly."""
    def __init__(self, directory):
        self.pages = {}
        for meta in Path(directory).glob("*.json"):
            data = json.loads(meta.read_text(encoding="utf-8"))
            with meta.with_suffix(".wiki").open(encoding="utf-8", newline="") as stream:
                self.pages[data["title"]] = PageSnapshot(data["title"], data["revid"], data["timestamp"], stream.read(),2 if data['title']=='Участник:Zambrowski/testbot' else 0)

    def fetch_page(self, title):
        try:
            return self.pages[title]
        except KeyError as exc:
            raise UpdateError("page_not_found", "В локальных копиях нет этой статьи") from exc
