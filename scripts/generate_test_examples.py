# SPDX-License-Identifier: MIT
"""Build explicitly fictional test packages, without contacting or writing Wikipedia."""
from pathlib import Path
import json
import hashlib
from copy import deepcopy
import mwparserfromhell as mw

ROOT=Path(__file__).resolve().parents[1]
EXAMPLES=ROOT/'examples'/'stage23'
EXAMPLES.mkdir(parents=True,exist_ok=True)
TITLE='Участник:Zambrowski/testbot'
PLAYER='Кальвин Браккельман'
FLAG='{{Флаг Германии|20px}} '
AUGSBURG=FLAG+'[[Аугсбург (футбольный клуб)|Аугсбург]]'
TABLE_AUGSBURG=FLAG+'«[[Аугсбург (футбольный клуб)|Аугсбург]]»'
TABLE_PADERBORN=FLAG+'«[[Падерборн 07]]»'
CUP='[[Кубок Германии по футболу|Кубок Германии]]'
SOURCE=[{'url':'https://ru.wikipedia.org/wiki/Участник:Zambrowski/testbot','provenance':'user_provided',
         'coverage_as_of':'2026-10-08','note':'ТОЛЬКО ВЫМЫШЛЕННЫЙ ТЕСТ БРАККЕЛЬМАННА. Новые числа и переходы не являются спортивными фактами.'}]


def op(id,type,entity=None,target=None,**values):
    return {'id':id,'type':type,'entity':entity or {'name':'Аугсбург','wikitext':AUGSBURG},
            'target':target or {'structure':'career','field':'клубы','period':'2026—{{н.в.}}'},
            'as_of':'2026-10-08','sources':deepcopy(SOURCE),**values}


def save(name,operations):
    pkg={'schema_version':'1.1','package_id':'FICTIONAL-TEST-'+name,'generated_at':'2026-10-08T12:00:00Z','is_test':True,
         'articles':[{'title':TITLE,'player':PLAYER,'operations':operations}]}
    (EXAMPLES/(name+'.json')).write_text(json.dumps(pkg,ensure_ascii=False,indent=2),encoding='utf-8')


original=(ROOT/'tests/fixtures/testbot.wiki').read_text(encoding='utf-8')
metadata=json.loads((ROOT/'tests/fixtures/testbot.json').read_text(encoding='utf-8'))
code=mw.parse(original)
for t in code.filter_templates(recursive=False):
    name=str(t.name).strip().casefold()
    if name=='клстат':
        t.add('3','Еврокубки',showkey=False)
        t.add('4','Стыковые матчи',showkey=False)
    elif name in {'клстат/сезон','клстат/итогозаклуб','клстат/итого'}:
        start=2 if name!='клстат/итого' else 1
        old=[str(t.get(str(i)).value) for i in range(start,start+6)]
        values=old[:4]+['0','0']+old[4:]
        for i,value in enumerate(values,start):t.add(str(i),value,showkey=False)
baseline='<!-- ВЫМЫШЛЕННЫЙ ТЕСТОВЫЙ НАБОР BrackelBot; добавленные еврокубковые нули заданы как тестовые значения, не спортивные факты. -->\n'+str(code)


def backup(name,text):
    data={'format':'brackelbot-backup-1','title':TITLE,'revid':metadata['revid'],'timestamp':metadata['timestamp'],
          'namespace':2,'starttimestamp':None,'text':text,'sha256':hashlib.sha256(text.encode('utf-8')).hexdigest()}
    (EXAMPLES/(name+'.backup.json')).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')


backup('00-comprehensive-baseline',baseline)
backup('99-original-testbot',original)
(EXAMPLES/'00-comprehensive-baseline.wiki').write_text(baseline,encoding='utf-8',newline='')
stats=op('stats','update_stats',season=None,competition={'name':'Чемпионат (вымышленный тест)','category':'all','scope':'league'},
         expected={'appearances':4,'goals':1},new={'appearances':5,'goals':2})
save('01-update-stats',[stats])
club={'name':'Borussia Mönchengladbach','kind':'club','wikidata_id':'Q101959'}
transfer=[op('close-previous','update_career_period',group_id='transfer',payload={'period':'2026—2026'}),
          op('new-club','add_club',entity=club,target={'structure':'career','field':'клубы'},group_id='transfer',
             payload={'period':'2026—{{н.в.}}','role':'primary','stats':{'appearances':2,'goals':1},
                      'previous':{'wikitext':AUGSBURG,'period':'2026—2026'}}),
          op('current-club','update_current_club',entity=club,target={'structure':'career','field':'клубы'},group_id='transfer',
             expected=AUGSBURG,payload={})]
save('02-transfer',transfer)
team=op('national-u21','add_national_team',entity={'name':'Германия до 21 года','kind':'national_team','age':21,'wikidata_id':'Q314851'},
        target={'structure':'career','field':'национальная сборная'},payload={'period':'2026—{{н.в.}}','stats':{'appearances':2,'goals':1}})
save('03-add-national-team',[team])
period=op('historical-period','update_career_period',entity={'name':'Кёльн II','wikitext':FLAG+'[[Кёльн II]]'},
          target={'structure':'career','field':'клубы','period':'2018—2020'},payload={'period':'2018—2019'})
save('04-career-period',[period])
season=op('new-season','add_season',entity={'name':'Падерборн 07','wikitext':TABLE_PADERBORN},target={'structure':'club_table'},
          season='2026/27',payload={'categories':{c:{'appearances':2 if c=='Чемпионат' else 0,'goals':1 if c=='Чемпионат' else 0}
                   for c in ['Чемпионат',CUP,'Еврокубки','Стыковые матчи']},'league_wikitext':'[[Вторая Бундеслига]]',
                   'insert_after_season':'2025/26','complete':True})
save('05-add-season',[season])
europe=op('europe','add_competition',entity={'name':'Аугсбург','wikitext':TABLE_AUGSBURG},target={'structure':'club_table'},season='2026/27',
          competition={'name':'Лига Европы (вымышленный тест)','category':'Еврокубки','scope':'category',
                       'mapping_rule':'Полный тестовый итог категории Еврокубки; другие турниры категории отсутствуют.'},
          expected={'appearances':0,'goals':0},new={'appearances':2,'goals':1})
save('06-europe-existing-category',[europe])
mixed=[deepcopy(x) for x in [stats,*transfer,team,season,europe,period]]
for x in mixed:x['group_id']='all-linked'
save('07-combined-atomic',mixed)
bad=deepcopy(mixed);bad[0]['expected']['appearances']=99
save('08-mismatch-rolls-back-all',bad)
save('09-repeat-after-01',[deepcopy(stats)])
proposal=deepcopy(europe);proposal['competition'].update(name='Кубок лиги',category='Кубок лиги')
proposal['payload']={'structure_proposal':'ТЕСТ: добавить отдельную пару столбцов Кубок лиги. Требует отдельного проектирования и подтверждения; этот JSON не исполняет перестройку.'}
save('10-structure-proposal',[proposal])
incomplete=deepcopy(season);del incomplete['payload']['categories']['Еврокубки']
save('11-incomplete-statistics',[incomplete])
unknown=deepcopy(period);unknown['payload']['period']='2018—?'
save('12-unknown-years',[unknown])
wrong=deepcopy(transfer)
for x in wrong:
    if x['type'] in {'add_club','update_current_club'}:x['entity']['wikidata_id']='Q41420'
save('13-wrong-borussia-qid',wrong)
totals=op('totals','update_totals',entity={'name':'Полная таблица','wikitext':'КлСтат'},target={'structure':'club_table'},payload={'complete':True})
save('14-recompute-totals',[totals])
date=op('date','update_date',payload={'field':'обновление данных о клубе','expected_value':'04.10.2026'})
save('15-confirmed-coverage-date',[date])
print('Generated fictional examples and original/comprehensive backups')
