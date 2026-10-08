# SPDX-License-Identifier: MIT
from copy import deepcopy
from wiki_stats.models import PageSnapshot
from wiki_stats.entity_resolver import ResolvedEntity
from wiki_stats.transactions import TEST_TITLE
from wiki_stats.errors import UpdateError

AUGSBURG = '{{Флаг Германии|20px}} [[Аугсбург (футбольный клуб)|Аугсбург]]'
WERDER = '{{Флаг Германии|20px}} [[Вердер (футбольный клуб)|Вердер]]'
BVB = '{{Флаг Германии|20px}} [[Боруссия (футбольный клуб, Дортмунд)|Боруссия Дортмунд]]'
U18 = '{{Флаг Германии|20px}} [[Сборная Германии по футболу (до 18 лет)|Германия (до 18)]]'
LEAGUE = '[[Чемпионат Германии по футболу|Бундеслига]]'
TABLE_AUGSBURG = '{{Флаг Германии|20px}} «[[Аугсбург (футбольный клуб)|Аугсбург]]»'
MINI = '''{{Футболист
| имя = Кальвин Браккельман
| номер = 2<!-- сохранить -->
| нынешний клуб = '''+AUGSBURG+'''
| клубы = {{Спортивная карьера
  |2010—2012|'''+WERDER+'''|10 (1)
  |2026—{{н.в.}}|'''+AUGSBURG+'''| 4 (1)<ref name="x"/><!-- статистика -->
 }}
| национальная сборная = {{спортивная карьера
  |2016—2017|'''+U18+'''|6 (0)
 }}
| обновление данных о клубе = 04.10.2026
| обновление данных о сборной = 04.10.2026
}}
Текст 4 (1) с [[ссылкой]] и <ref name="x">Неприкосновенный источник</ref>.
{{КлСтат|Чемпионат|Национальный кубок|Еврокубки|Стыковые матчи}}
{{КлСтат/Клуб|'''+TABLE_AUGSBURG+'''|сезоны=1}}
{{КлСтат/Лига|'''+LEAGUE+'''|сезоны=1}}
{{КлСтат/Сезон|2025/26|4|1|0|0|0|0|0|0}}
{{КлСтат/ИтогоЗаКлуб|Итого|4|1|0|0|0|0|0|0}}
{{КлСтат/Итого|4|1|0|0|0|0|0|0}}
Конец статьи и комментарий <!-- 4 (1) -->.
'''


def snapshot(text=MINI,revid=100):
    return PageSnapshot(TEST_TITLE,revid,'2026-10-08T00:00:00Z',text,2,'2026-10-08T10:00:00Z')


def source():
    return [{'url':'https://ru.wikipedia.org/wiki/Участник:Zambrowski/testbot','provenance':'user_provided',
             'coverage_as_of':'2026-10-08','note':'ТОЛЬКО ВЫМЫШЛЕННЫЙ ТЕСТ'}]


def operation(id,type,entity=None,target=None,**fields):
    return {'id':id,'type':type,'entity':entity or {'name':'Аугсбург','wikitext':AUGSBURG},
            'target':target or {'structure':'career','field':'клубы','period':'2026—{{н.в.}}'},
            'as_of':'2026-10-08','sources':source(),**fields}


def stats_operation(id='stats',group_id=None):
    op=operation(id,'update_stats',season=None,competition={'name':'Чемпионат','category':'all','scope':'league'},
                 expected={'appearances':4,'goals':1},new={'appearances':5,'goals':2})
    if group_id: op['group_id']=group_id
    return op


def package(operations):
    return {'schema_version':'1.1','package_id':'TEST-ONLY','generated_at':'2026-10-08T12:00:00Z','is_test':True,
            'articles':[{'title':TEST_TITLE,'player':'Кальвин Браккельман','operations':operations}]}


def transfer(name='Боруссия Дортмунд',qid='Q41420'):
    entity={'name':name,'kind':'club','wikidata_id':qid}
    return [
      operation('close','update_career_period',group_id='transfer',payload={'period':'2026—2026'}),
      operation('add','add_club',entity=entity,target={'structure':'career','field':'клубы'},group_id='transfer',
                payload={'period':'2026—{{н.в.}}','role':'primary','stats':{'appearances':2,'goals':1},
                         'previous':{'wikitext':AUGSBURG,'period':'2026—2026'}}),
      operation('current','update_current_club',entity=entity,target={'structure':'career','field':'клубы'},
                group_id='transfer',expected=AUGSBURG,payload={})]


def team_operation(age=21):
    return operation('team','add_national_team',entity={'name':'Германия','kind':'national_team','age':age,
                     'wikidata_id':'Q900'+str(age or 0)},target={'structure':'career','field':'национальная сборная'},
                     payload={'period':'2026—{{н.в.}}','stats':{'appearances':2,'goals':1}})


def season_operation():
    return operation('season','add_season',entity={'name':'Аугсбург','wikitext':TABLE_AUGSBURG},
            target={'structure':'club_table'},season='2026/27',payload={'categories':{
            c:{'appearances':2 if c=='Чемпионат' else 0,'goals':1 if c=='Чемпионат' else 0}
            for c in ['Чемпионат','Национальный кубок','Еврокубки','Стыковые матчи']},
            'league_wikitext':LEAGUE,'insert_after_season':'2025/26','complete':True})


class FakeResolver:
    def resolve(self,entity):
        if entity.get('wikidata_id')=='Q999':
            raise UpdateError('entity_mismatch','Неправильный клуб')
        if entity['kind']=='national_team':
            age=entity['age'];title='Сборная Германии по футболу'+(f' (до {age} '+('года' if age==21 else 'лет')+')' if age else '')
            return ResolvedEntity(title,entity['wikidata_id'],'Германии',title,'national_team',age)
        title={'Q41420':'Боруссия (футбольный клуб, Дортмунд)','Q101959':'Боруссия (футбольный клуб, Мёнхенгладбах)',
               'Q13174':'Вердер (футбольный клуб)','Q15755':'Аугсбург (футбольный клуб)'}[entity['wikidata_id']]
        return ResolvedEntity(title,entity['wikidata_id'],'Германии',entity['name'],'club')


class MemoryClient:
    authenticated_user='Zambrowski'

    def __init__(self,page=None):
        self.page=page or snapshot()
        self.writes=[]
        self.failure=None
        self.auth_checks=0

    def verify_authenticated(self):
        self.auth_checks+=1
        if not self.authenticated_user: raise UpdateError('authorization_required','Нет сессии')

    def fetch_page(self,title):
        return self.page

    def save_test_page(self,page,text,summary):
        if self.failure: raise self.failure
        self.writes.append((page,text,summary))
        return {'oldrevid':page.revid,'newrevid':page.revid+1,'title':page.title}
