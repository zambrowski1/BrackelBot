# SPDX-License-Identifier: MIT
import json
import re
import mwparserfromhell as mw
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
             'Q1041':'Сенегала','Q1008':'Кот-д’Ивуара','Q117':'Ганы','Q258':'ЮАР',
             'Q235':'Монако','Q213':'Чехии','Q21':'Англии','Q22':'Шотландии','Q25':'Уэльса','Q26':'Северной Ирландии'}


@dataclass(frozen=True)
class ResolvedEntity:
    title: str
    qid: str
    flag: str
    display: str
    kind: str
    age: int | None = None
    foreign_language: str | None = None
    foreign_title: str | None = None
    team_variant: str = 'primary'

    @property
    def wikitext(self):
        link = ('{{нп5|'+self.title+'|'+self.display+'|'+self.foreign_language+'|'+self.foreign_title+'}}'
                if self.foreign_language else '[['+self.title+'|'+self.display+']]')
        return '{{Флаг '+self.flag+'|20px}} '+link


def reference_identity(client, text):
    """Recognize one club link, including the four positional NP parameters."""
    code=mw.parse(text)
    links=code.filter_wikilinks()
    translations=[t for t in code.filter_templates() if str(t.name).strip().casefold() in {'нп','нп5','не переведено','iw'}]
    annotations = [t for t in code.filter_templates() if str(t.name).strip().casefold() in {'аренда','фарм-клуб'} and not t.params]
    if any(not (str(t.name).strip().casefold().startswith('флаг ') or str(t.name).strip().casefold() in {'флаг','flagicon','флагификация'}) and t not in translations and t not in annotations for t in code.filter_templates()):
        raise UpdateError('needs_review','Аренда, резерв или сложная ссылка требуют проверки')
    if len(links)==1 and not translations:
        return client.get_page_identity(str(links[0].title).strip())
    if len(translations)==1 and not links:
        template=translations[0]
        try:
            title,display,language,foreign=[str(template.get(str(i)).value).strip() for i in range(1,5)]
            if len(template.params)!=4 or language not in {'de','en'}: raise ValueError()
        except ValueError:
            raise UpdateError('needs_review','Нужны четыре явных параметра нп5 и проверяемый языковой раздел') from None
        page=client.get_foreign_page_identity(foreign,language)
        resolved=EntityResolver(client).resolve({'name':title,'kind':'club','wikidata_id':page['qid'],
                                                'page_title':title,'display_name':display})
        if resolved.foreign_language!=language or resolved.foreign_title!=foreign:
            raise UpdateError('entity_mismatch','Ссылка нп5 не совпадает с проверенной статьёй клуба')
        return {'title':resolved.title,'qid':resolved.qid}
    raise UpdateError('needs_review','Нужна единственная однозначная ссылка на клуб')


def claim_ids(entity, prop):
    return {x.get('mainsnak',{}).get('datavalue',{}).get('value',{}).get('id')
            for x in entity.get('claims',{}).get(prop,[]) if isinstance(x.get('mainsnak',{}).get('datavalue',{}).get('value'),dict)} - {None}


class EntityResolver:
    """Only Wikipedia/Wikidata identity lookups; never football statistics/news."""
    def __init__(self, client, catalogue=None):
        self.client = client
        self.catalogue = catalogue if catalogue is not None else json.loads(files('wiki_stats').joinpath('clubs.json').read_text(encoding='utf-8'))['clubs']
        self.cache = {}
        self.country_flags = {}
        self.football_types = {}

    def resolve(self, ref):
        key = json.dumps(ref,ensure_ascii=False,sort_keys=True)
        if key in self.cache:
            return self.cache[key]
        name = ref['name'].strip().casefold()
        matches = [c for c in self.catalogue if name in {a.casefold() for a in [c['name'],c['title'],c.get('display_name',c['name']),*c['aliases']]}]
        if name in {'borussia','боруссия'}:
            matches = [c for c in self.catalogue if c['title'].startswith('Боруссия')]
        if len(matches) > 1:
            selected=[c for c in matches if c['qid']==ref.get('wikidata_id')]
            if len(selected)!=1:
                raise UpdateError('entity_ambiguous','Название клуба неоднозначно; укажите полное название и QID')
            matches=selected
        known = matches[0] if matches else None
        qid = ref.get('wikidata_id') or (known['qid'] if known else None)
        title = ref.get('page_title') or (known['title'] if known else None)
        if known and ((ref.get('wikidata_id') and ref['wikidata_id'] != known['qid']) or (ref.get('page_title') and ref['page_title'] != known['title'])):
            raise UpdateError('entity_mismatch','Название и идентификатор относятся к разным клубам')
        wd = self.client.get_wikidata_entity(qid) if qid else None
        if wd:
            sitelink = wd.get('sitelinks',{}).get('ruwiki',{}).get('title')
            if not sitelink:
                result=self._foreign(ref,known,wd,qid,title)
                self.cache[key]=result
                return result
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
            if page['title'].startswith('Сборная') or not (known or self.is_football_team(wd)):
                raise UpdateError('unsupported_entity','Объект не подтверждён как футбольный клуб/команда')
        countries = claim_ids(wd,'P17')
        if known:
            countries = {known['country_qid']}
        elif kind=='club' and countries=={'Q145'}:
            # Football uses England/Scotland/Wales flags. Verify geography;
            # the United Kingdom country alone cannot select one of them.
            pending=list(claim_ids(wd,'P159')|claim_ids(wd,'P131'))
            visited=set();home=set()
            while pending and len(visited)<8:
                location=pending.pop(0)
                if location in visited:continue
                visited.add(location)
                if location in {'Q21','Q22','Q25','Q26'}:
                    home.add(location);continue
                geography=self.client.get_wikidata_entity(location)
                pending.extend(claim_ids(geography,'P131'))
            if len(home)!=1:
                raise UpdateError('flag_unverified','Не подтверждена футбольная страна клуба Великобритании')
            countries=home
        flags = {self.country_flag(c) for c in countries}
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
        result = ResolvedEntity(page['title'],qid,flag,display,kind,age,team_variant=known.get('team_variant','primary') if known else 'primary')
        self.cache[key] = result
        return result

    def is_football_team(self,entity):
        roots={'Q476028','Q15944511'}
        pending=[(qid,0) for qid in claim_ids(entity,'P31')];seen=set()
        while pending and len(seen)<16:
            qid,depth=pending.pop(0)
            if qid in roots:return True
            if qid in seen or depth>=3:continue
            seen.add(qid)
            if qid not in self.football_types:
                try:self.football_types[qid]=claim_ids(self.client.get_wikidata_entity(qid),'P279')
                except UpdateError as exc:
                    if exc.code=='entity_not_found':continue
                    raise
            pending.extend((parent,depth+1) for parent in self.football_types[qid])
        return False

    def country_flag(self,qid):
        if qid in COUNTRIES:return COUNTRIES[qid]
        if qid in self.country_flags:return self.country_flags[qid]
        country=self.client.get_wikidata_entity(qid)
        title=country.get('sitelinks',{}).get('ruwiki',{}).get('title')
        label=country.get('labels',{}).get('ru',{}).get('value')
        catalogue=json.loads(files('wiki_stats').joinpath('national_teams.json').read_text(encoding='utf-8'))
        candidates=set()
        for name in (title,label):
            canonical=catalogue['aliases'].get(name,name)
            entry=catalogue['countries'].get(canonical)
            if entry and entry.get('gen'):candidates.add(entry['gen'])
        if len(candidates)!=1:raise UpdateError('flag_unverified','Не подтверждён шаблон флага страны '+str(qid))
        flag=next(iter(candidates))
        if any(c in flag for c in '|{}[]<>\r\n'):raise UpdateError('flag_unverified','Недопустимое название флага')
        self.country_flags[qid]=flag
        return flag

    def _foreign(self,ref,known,wd,qid,title):
        if ref['kind']!='club' or not title or not known:
            raise UpdateError('entity_not_found','Для клуба без русской статьи нужна запись справочника с русским названием')
        if not any(lang+'wiki' in wd.get('sitelinks',{}) for lang in ('de','en')):
            raise UpdateError('entity_not_found','Нет проверяемой русской, немецкой или английской статьи')
        try:
            self.client.get_page_identity(title)
        except UpdateError as exc:
            if exc.code!='entity_not_found': raise
        else:
            raise UpdateError('entity_mismatch','Русское название уже занято; проверьте статью и связь Wikidata')
        language=foreign=None
        for lang in ('de','en'):
            candidate=wd.get('sitelinks',{}).get(lang+'wiki',{}).get('title')
            if not candidate: continue
            try:
                page=self.client.get_foreign_page_identity(candidate,lang)
            except UpdateError as exc:
                if exc.code=='entity_not_found': continue
                raise
            if page.get('qid')==qid and page['title']==candidate:
                language,foreign=lang,candidate;break
        if not language:
            raise UpdateError('entity_unverified','Иноязычная статья и обратная ссылка Wikidata не подтвердили клуб')
        flag=self.country_flag(known['country_qid'])
        if not flag or (ref.get('flag') and ref['flag']!=flag):
            raise UpdateError('flag_unverified','Не подтверждена страна клуба')
        display=ref.get('display_name') or known.get('display_name',known['name'])
        if display not in {known['name'],known['title'],known.get('display_name'),*known['aliases']}:
            raise UpdateError('entity_mismatch','Отображаемое название не подтверждено справочником')
        if any(ch in title+display+foreign for ch in '|{}[]\r\n'):
            raise UpdateError('unsupported_entity','Недопустимый викитекст в названии клуба')
        flag_page=self.client.get_page_identity('Шаблон:Флаг '+flag,namespace=10)
        if flag_page['title']!='Шаблон:Флаг '+flag:
            raise UpdateError('flag_unverified','Не подтвердился шаблон флага')
        template=self.client.get_page_identity('Шаблон:Нп5',namespace=10)
        if template['title'].casefold() not in {'шаблон:нп5','шаблон:не переведено'}:
            raise UpdateError('entity_unverified','Шаблон Нп5 не подтвердился')
        return ResolvedEntity(title,qid,flag,display,'club',foreign_language=language,foreign_title=foreign,
                              team_variant=known.get('team_variant','primary'))
