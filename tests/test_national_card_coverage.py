from copy import deepcopy
from dataclasses import replace
from datetime import datetime,timezone
import pytest
from tests.stage23_helpers import MINI,U18,snapshot
from wiki_stats.national_card_coverage import close_national_card
from wiki_stats.structural_editor import execute_operation
from wiki_stats.change_planner import plan_article
from wiki_stats.schema_validator import validate_package
from wiki_stats.wikitext_parser import WikitextParser

NOW=datetime(2026,10,11,tzinfo=timezone.utc)
MAPPING={'wiki_name':'Кальвин Браккельман'}
TEAM='[[Сборная Германии по футболу|Германия]]'
TEXT=MINI.replace('|2016—2017|'+U18+'|6 (0)','|2024—{{н.в.}}|'+TEAM+'|6 (0)')

def scope():
    return {'complete':True,'numbers':{'appearances':6,'goals':0},
        'statistics_as_of':'2026-10-05','matches':[{'date':'2026-10-05','match':{'link':'https://www.sofascore.com/football/match/example'}}],
        'career_binding':{'period':'2024—{{н.в.}}','team':TEAM,'qid':'Q1',
                          'numbers':{'appearances':6,'goals':0},'verified_period':'2024—{{н.в.}}'}}

def test_correct_numbers_still_update_old_date_and_preserve_text_and_replay():
    ops,complete=close_national_card(snapshot(TEXT),MAPPING,[],[scope()],NOW)
    assert complete and len(ops)==1 and ops[0]['payload']['statistics_as_of']=='2026-10-05'
    package={'schema_version':'1.1','package_id':'date-proof','generated_at':NOW.isoformat(),
        'articles':[{'title':snapshot(TEXT).title,'base_revid':100,'player':MAPPING['wiki_name'],'operations':ops}]}
    validate_package(package)
    result,status=execute_operation(TEXT,MAPPING['wiki_name'],ops[0])
    assert status=='ready'
    assert result==TEXT.replace('обновление данных о сборной = 04.10.2026','обновление данных о сборной = 05.10.2026')
    assert close_national_card(snapshot(result),MAPPING,[],[scope()],NOW)==([],True)

@pytest.mark.parametrize('fault',['missing_binding','partial','wrong_team','wrong_period','wrong_old','wrong_new','duplicate','missing_matches','future','date_mismatch','unknown_period'])
def test_incomplete_card_never_advances_date(fault):
    s=scope();scopes=[s];text=TEXT
    if fault=='missing_binding':s.pop('career_binding')
    elif fault=='partial':s['complete']=False
    elif fault=='wrong_team':s['career_binding']['team']='[[Сборная Франции по футболу|Франция]]'
    elif fault=='wrong_period':s['career_binding']['period']='2023—{{н.в.}}'
    elif fault=='wrong_old':s['career_binding']['numbers']['appearances']=7
    elif fault=='wrong_new':s['numbers']['goals']=1
    elif fault=='duplicate':scopes.append(deepcopy(s))
    elif fault=='missing_matches':s['matches']=[]
    elif fault=='future':s['statistics_as_of']='2026-10-12';s['matches'][0]['date']='2026-10-12'
    elif fault=='date_mismatch':s['matches'][0]['date']='2026-10-04'
    else:s['career_binding']['verified_period']='2023—{{н.в.}}'
    assert close_national_card(snapshot(text),MAPPING,[],scopes,NOW)==([],False)

def test_date_and_changed_numbers_are_one_atomic_group():
    s=scope();s['numbers']={'appearances':7,'goals':1}
    operation={'id':'senior','type':'update_stats','group_id':'senior-only',
        'entity':{'name':'Германия','kind':'national_team','wikitext':TEAM,'wikidata_id':'Q1'},
        'target':{'structure':'career','field':'национальная сборная','period':'2024—{{н.в.}}'},
        'season':None,'competition':{'name':'Сборная','category':'all','scope':'national_all'},
        'expected':{'appearances':6,'goals':0},'new':s['numbers'],'as_of':'2026-10-05',
        'sources':[{'url':'https://www.sofascore.com/football/match/example','provenance':'sofascore','coverage_as_of':'2026-10-05'}]}
    ops,complete=close_national_card(snapshot(TEXT),MAPPING,[operation],[s],NOW)
    assert complete and len({o['group_id'] for o in ops})==1
    assert all(o['as_of']=='2026-10-11' and all(x['coverage_as_of']==o['as_of'] for x in o['sources']) for o in ops)
    article={'title':snapshot(TEXT).title,'base_revid':100,'player':MAPPING['wiki_name'],'operations':ops}
    plan=plan_article(snapshot(TEXT),article,'1.1')
    assert all(c.status=='ready' for c in plan.changes)
    assert '|7 (1)' in plan.preview and 'обновление данных о сборной = 05.10.2026' in plan.preview
    bad=replace(snapshot(TEXT),text=TEXT.replace('|6 (0)','|8 (0)'))
    blocked=plan_article(bad,article,'1.1')
    assert blocked.preview==bad.text and not blocked.diff

def test_real_orban_primary_youth_and_72_senior_caps_close_one_card():
    import gzip,json,hashlib
    from pathlib import Path
    from wiki_stats.sofascore_client import _SofascoreAccount
    from wiki_stats.sofascore_national_cycle import prepare_nationals
    from wiki_stats.dfb_youth_cycle import prepare_youth_nationals
    folder=Path(__file__).parent/'source-fixtures'
    data=json.loads(gzip.decompress((folder/'orban-national-source.json.gz').read_bytes()))
    youth=json.loads(gzip.decompress((folder/'dfb-orban-u21-roster.json.gz').read_bytes()))
    lookup={r['cache_key']:r['data'] for r in data['responses']}
    class API(_SofascoreAccount):
        def __init__(self):pass
        def national_statistics(self,pid):return data['national']
        def national_secondary_source(self,pid):return youth
        def request(self,path,params,**kwargs):
            from wiki_stats.errors import UpdateError
            key=hashlib.sha256(json.dumps([path,params],sort_keys=True).encode()).hexdigest()
            if key not in lookup:raise UpdateError('source_cache_missing','No network requests in test')
            return lookup[key]
    class Wiki:
        def get_page_identity(self,title):
            return {'title':title,'qid':'Q21' if '21' in title else 'Q172107'}
        def get_foreign_page_identity(self,title,language):return self.get_page_identity(title)
    team='[[Сборная Германии по футболу (до 21 года)|Германия (до 21)]]'
    senior='[[Сборная Венгрии по футболу|Венгрия]]'
    text=MINI.replace('Кальвин Браккельман','Вилли Орбан').replace('|2016—2017|'+U18+'|6 (0)',
        '|2014—2016|'+team+'|9 (3)\n|2018—{{н.в.}}|'+senior+'|60 (5)')
    text=text.replace('{{Футболист','{{Футболист\n| дата рождения = 03.11.1992',1)
    mapping={'api_id':136924,'qid':'Q2577379','wiki_name':'Вилли Орбан','birth_date':'1992-11-03','source_name':'Willi Orban'}
    snap=snapshot(text);api=API();wiki=Wiki()
    budget={'remaining':500}
    adult,adult_issues,adult_scopes=prepare_nationals(snap,mapping,data['history'],api,wiki,budget,NOW)
    juniors,issues,junior_scopes=prepare_youth_nationals(snap,mapping,api,wiki,NOW)
    assert not issues
    ops,complete=close_national_card(snap,mapping,adult+juniors,adult_scopes+junior_scopes,NOW)
    assert complete,(adult_issues,budget,[(s.get('career_binding'),s['statistics_as_of']) for s in adult_scopes+junior_scopes])
    assert ops[-1]['payload']['statistics_as_of']=='2026-10-05'
    article={'title':snap.title,'base_revid':100,'player':mapping['wiki_name'],'operations':ops}
    validate_package({'schema_version':'1.1','package_id':'orban-real-date','generated_at':NOW.isoformat(),'articles':[article]})
    plan=plan_article(snap,article,'1.1')
    assert all(c.status=='ready' for c in plan.changes)
    assert '|2014—2015|' in plan.preview and '|2 (0)' in plan.preview and '|72 (6)' in plan.preview
    assert '| дата рождения = 03.11.1992' in plan.preview
    assert 'обновление данных о сборной = 05.10.2026' in plan.preview
    replay=plan_article(replace(snap,text=plan.preview),article,'1.1')
    assert not replay.diff and replay.preview==plan.preview


@pytest.mark.parametrize('date_field',[None,'','01.01.2020','12.10.2026'])
def test_senior_match_date_updates_card_without_rechecking_youth(date_field):
    text=TEXT.replace('|2024—','|2016—2017|'+U18+'|6 (0)\n|2024—')
    if date_field is None:text=text.replace('| обновление данных о сборной = 04.10.2026\n','')
    else:text=text.replace('обновление данных о сборной = 04.10.2026','обновление данных о сборной = '+date_field)
    ops,complete=close_national_card(snapshot(text),MAPPING,[],[scope()],NOW)
    assert complete and ops[-1]['payload']['statistics_as_of']=='2026-10-05'
    pkg={'schema_version':'1.1','package_id':'senior-date-proof','generated_at':NOW.isoformat(),
         'articles':[{'title':snapshot(text).title,'base_revid':100,'player':MAPPING['wiki_name'],'operations':ops}]}
    validate_package(pkg)
    plan=plan_article(snapshot(text),pkg['articles'][0],'1.1')
    assert all(c.status=='ready' for c in plan.changes)
    assert '05.10.2026' in plan.preview and '|2016—2017|'+U18+'|6 (0)' in plan.preview
    assert close_national_card(snapshot(plan.preview),MAPPING,[],[scope()],NOW)==([],True)
    assert not plan_article(replace(snapshot(text),text=plan.preview),pkg['articles'][0],'1.1').diff


def test_an_unverified_other_senior_team_still_blocks_the_card_date():
    text=TEXT.replace('|2024—','|2018—2020|[[Сборная Франции по футболу|Франция]]|3 (0)\n|2024—')
    assert close_national_card(snapshot(text),MAPPING,[],[scope()],NOW)==([],False)


def test_atomic_card_date_preserves_the_national_table_date_and_hash():
    from tests.test_sbmatches_api_source import scope as real_scope,Wiki
    from wiki_stats.national_match_format import format_scope
    from wiki_stats.national_team_identity import NationalTeamIdentity
    from wiki_stats.sofascore_sbmatches import full_repair
    source=real_scope();wiki=Wiki();identity=NationalTeamIdentity(wiki)
    rows,_=format_scope(source,identity.resolve)
    team='[[Сборная Чехии по футболу|Чехия]]'
    text='{{Футболист|имя=Патрик Шик|национальная сборная={{спортивная карьера|2016—{{н.в.}}|'+team+'|56 (26)}}}}\n'
    text+='{{СбМатчи|игрок=Патрик Шик|сборная=Чехия\n'
    for key,value in rows[0].items():text+='|'+key+'1='+value+'\n'
    text+='}}'
    page=snapshot(text);mapping={'wiki_name':'Патрик Шик','api_id':280609}
    operation=full_repair(page,mapping,source,wiki)
    source['career_binding']={'period':'2016—{{н.в.}}','verified_period':'2016—{{н.в.}}',
       'team':team,'qid':operation['entity']['wikidata_id'],'numbers':source['numbers']}
    ops,complete=close_national_card(page,mapping,[operation],[source],datetime(2026,10,12,tzinfo=timezone.utc))
    assert complete
    article={'title':page.title,'base_revid':100,'player':'Патрик Шик','operations':ops}
    validate_package({'schema_version':'1.1','package_id':'atomic-national-table-date','generated_at':'2026-10-12T00:00:00Z','articles':[article]})
    plan=plan_article(page,article,'1.1')
    assert all(c.status=='ready' for c in plan.changes)
    assert 'обновлено=25-06-2026' in plan.preview
    assert '25.06.2026' in WikitextParser(plan.preview).infobox('Патрик Шик')['обновление данных о сборной'].text
    assert not plan_article(replace(page,text=plan.preview),article,'1.1').diff
