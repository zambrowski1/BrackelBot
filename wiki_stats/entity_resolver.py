# SPDX-License-Identifier: MIT
import json
import re
from dataclasses import dataclass
from importlib.resources import files
from .errors import UpdateError


COUNTRIES = {'Q183':'Германии','Q40':'Австрии','Q39':'Швейцарии','Q145':'Великобритании',
             'Q142':'Франции','Q38':'Италии','Q29':'Испании','Q31':'Бельгии','Q55':'Нидерландов',
             'Q43':'Турции','Q28':'Венгрии','Q36':'Польши','Q225':'Боснии и Герцеговины',
             'Q224':'Хорватии','Q30':'США','Q16':'Канады','Q155':'Бразилии','Q414':'Аргентины',
             'Q45':'Португалии','Q34':'Швеции','Q35':'Дании','Q20':'Норвегии','Q33':'Финляндии',
             'Q159':'России','Q212':'Украины','Q227':'Азербайджана','Q232':'Казахстана',
             'Q17':'Японии','Q884':'Южной Кореи','Q1028':'Марокко','Q1033':'Нигерии',
             'Q1041':'Сенегала','Q1008':'Кот-д’Ивуара','Q117':'Ганы','Q258':'ЮАР'}


@dataclass(frozen=True)
class ResolvedEntity:
    title: str
    qid: str
    flag: str
    display: str
    kind: str
    age: int | None = None

    @property
    def wikitext(self):
        return '{{Флаг '+self.flag+'|20px}} [['+self.title+'|'+self.display+']]'


def claim_ids(entity, prop):
    return {x.get('mainsnak',{}).get('datavalue',{}).get('value',{}).get('id')
            for x in entity.get('claims',{}).get(prop,[]) if isinstance(x.get('mainsnak',{}).get('datavalue',{}).get('value'),dict)} - {None}


class EntityResolver:
    """Only Wikipedia/Wikidata identity lookups; never football statistics/news."""
    def __init__(self, client, catalogue=None):
        self.client = client
        self.catalogue = catalogue if catalogue is not None else json.loads(files('wiki_stats').joinpath('clubs.json').read_text(encoding='utf-8'))['clubs']
        self.cache = {}

    def resolve(self, ref):
        key = json.dumps(ref,ensure_ascii=False,sort_keys=True)
        if key in self.cache:
            return self.cache[key]
        name = ref['name'].strip().casefold()
        matches = [c for c in self.catalogue if name in {a.casefold() for a in [c['name'],c['title'],*c['aliases']]}]
        if name in {'borussia','боруссия'}:
            matches = [c for c in self.catalogue if c['title'].startswith('Боруссия')]
        if len(matches) > 1:
            raise UpdateError('entity_ambiguous','Название клуба неоднозначно; укажите полное название и QID')
        known = matches[0] if matches else None
        qid = ref.get('wikidata_id') or (known['qid'] if known else None)
        title = ref.get('page_title') or (known['title'] if known else None)
        if known and ((ref.get('wikidata_id') and ref['wikidata_id'] != known['qid']) or (ref.get('page_title') and ref['page_title'] != known['title'])):
            raise UpdateError('entity_mismatch','Название и идентификатор относятся к разным клубам')
        wd = self.client.get_wikidata_entity(qid) if qid else None
        if wd:
            sitelink = wd.get('sitelinks',{}).get('ruwiki',{}).get('title')
            if not sitelink:
                raise UpdateError('entity_not_found','У объекта нет русской статьи')
            if title and title != sitelink:
                raise UpdateError('entity_mismatch','Название статьи не соответствует QID')
            title = sitelink
        if not title:
            raise UpdateError('entity_ambiguous','Для неизвестного справочнику объекта нужны точная русская статья или QID')
        page = self.client.get_page_identity(title)
        if not page.get('qid'):
            raise UpdateError('entity_unverified','У русской статьи отсутствует Wikidata QID')
        if qid and page['qid'] != qid:
            raise UpdateError('entity_mismatch','Статья и QID не совпадают')
        qid = page['qid']
        if wd is None:
            wd = self.client.get_wikidata_entity(qid)
        if wd.get('sitelinks',{}).get('ruwiki',{}).get('title') != page['title']:
            raise UpdateError('entity_mismatch','Обратная ссылка Wikidata не совпадает со статьёй')
        kind = ref['kind']
        age = None
        if kind == 'national_team':
            if not re.fullmatch(r'Сборная .+ по футболу(?: \(до [0-9]+ (?:лет|года)\))?',page['title']):
                raise UpdateError('unsupported_entity','Неподдерживаемая структура названия национальной сборной')
            match = re.search(r'\(до ([0-9]+) ',page['title'])
            age = int(match[1]) if match else None
            if ref.get('age') != age:
                raise UpdateError('entity_mismatch','Возрастная категория сборной не совпадает')
        else:
            if page['title'].startswith('Сборная') or not (known or 'Q476028' in claim_ids(wd,'P31') or 'Q15944511' in claim_ids(wd,'P31')):
                raise UpdateError('unsupported_entity','Объект не подтверждён как футбольный клуб/команда')
        countries = claim_ids(wd,'P17')
        if known:
            countries = {known['country_qid']}
        flags = {COUNTRIES[c] for c in countries if c in COUNTRIES}
        if len(flags) != 1:
            raise UpdateError('flag_unverified','Страна и флаг не определены однозначно')
        flag = next(iter(flags))
        if ref.get('flag') and ref['flag'] != flag:
            raise UpdateError('entity_mismatch','Заявленный флаг не соответствует стране объекта')
        # Do not allow arbitrary display text to masquerade as another team.
        display = known.get('display_name',known['name']) if known else page['title']
        if not known and kind=='club':
            match = re.fullmatch(r'(.+) \(футбольный клуб(?:, ([^()]+))?\)',page['title'])
            if match: display = match[1] + (f' ({match[2]})' if match[2] else '')
        if ref.get('display_name'):
            allowed = {display,page['title'],*(known['aliases'] if known else [])}
            if ref['display_name'] not in allowed:
                raise UpdateError('entity_mismatch','Отображаемое имя не подтверждено')
            display = ref['display_name']
        if any(ch in display+page['title'] for ch in '|{}[]\r\n'):
            raise UpdateError('unsupported_entity','Недопустимый викитекст в имени объекта')
        flag_page = self.client.get_page_identity('Шаблон:Флаг '+flag, namespace=10)
        if flag_page['title'] != 'Шаблон:Флаг '+flag:
            raise UpdateError('flag_unverified','Неожиданное название шаблона флага')
        result = ResolvedEntity(page['title'],qid,flag,display,kind,age)
        self.cache[key] = result
        return result
