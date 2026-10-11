# SPDX-License-Identifier: MIT
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from tests.stage23_helpers import MINI, U18, snapshot
from wiki_stats.dfb_youth_cycle import prepare_youth_nationals
from wiki_stats.errors import UpdateError
from wiki_stats.schema_validator import validate_package
from wiki_stats.sfv_youth_profile import parse_keeper_youth
from wiki_stats.structural_editor import execute_operation

NOW = datetime(2026,10,11,tzinfo=timezone.utc)
MAPPING = {'api_id':556866,'source_name':'Gregor Kobel','wiki_name':'Грегор Кобель','birth_date':'1997-12-06',
           'title':'Кобель, Грегор','qid':'Q36184031'}


def source():
    return json.loads((Path(__file__).parent/'source-fixtures/sfv-kobel-youth.json').read_text(encoding='utf-8'))


def test_real_complete_lists_prove_keeper_totals_and_exclude_unused_bench_rows():
    scopes = parse_keeper_youth(MAPPING,source(),NOW)
    assert [(s['age'],s['numbers']) for s in scopes] == [
        (17,{'appearances':3,'goals':-5}),(19,{'appearances':7,'goals':-10}),
        (20,{'appearances':2,'goals':-4}),(21,{'appearances':9,'goals':-13})]
    assert [sum(r['minutes'] for r in s['matches']) for s in scopes] == [240,630,180,810]
    assert all(r['date']!='2016-03-30' for r in scopes[1]['matches'])
    assert all(r['date']!='2016-11-14' for r in scopes[3]['matches'])


@pytest.mark.parametrize('fault', ['conflicting_birthday', 'conflicting_position', 'duplicate_birthday',
                                 'duplicate_position', 'match_before_birth'])
def test_contradictory_identity_and_impossible_chronology_are_rejected(fault):
    data = source()
    if fault == 'conflicting_birthday':
        data['identity']['text'] += '\nGeburtstag\t07.12.1997'
    elif fault == 'conflicting_position':
        data['identity']['text'] += '\nPosition\tAbwehr'
    elif fault == 'duplicate_birthday':
        data['identity']['text'] += '\nGeburtstag\t06.12.1997'
    elif fault == 'duplicate_position':
        data['identity']['text'] += '\nPosition\tTor'
    else:
        data['profiles'][1]['rows'][2][0] = '27.03.1990'
    with pytest.raises(UpdateError) as exc:
        parse_keeper_youth(MAPPING, data, NOW)
    assert exc.value.code == 'sfv_youth_unproven'


@pytest.mark.parametrize('fault',['player','pid','name','birthday','position','team','age','duplicate_age',
    'missing_pair','missing_match','duplicate_match','partial_minutes','substituted','red_card','scored',
    'wrong_total','wrong_minutes','wrong_role','wrong_country','future','non80_u17','unsupported_finals',
    'duplicate_summary','eligible_player','empty'])
def test_incomplete_or_partial_keeper_data_never_becomes_concessions(fault):
    data=source(); mapping=deepcopy(MAPPING); now=NOW; p=data['profiles'][1]
    if fault=='player':data['player_id']=136924
    elif fault=='pid':data['source_player_id']=1
    elif fault=='name':mapping['source_name']='Other Keeper'
    elif fault=='birthday':mapping['birth_date']='1997-12-07'
    elif fault=='position':data['identity']['text']=data['identity']['text'].replace('Position\tTor','Position\tAbwehr')
    elif fault=='team':p['url']=p['url'].replace('t-41644','t-41643')
    elif fault=='age':p['url']=p['url'].replace('/u-19/','/u-20/')
    elif fault=='duplicate_age':data['profiles'].append(deepcopy(p))
    elif fault=='missing_pair':p['rows'].pop()
    elif fault=='missing_match':del p['rows'][2:4]
    elif fault=='duplicate_match':p['rows'] += deepcopy(p['rows'][2:4])
    elif fault=='partial_minutes':p['rows'][3][1]="45'"
    elif fault=='substituted':p['summary'][-1][1]='1'
    elif fault=='red_card':p['summary'][4][1]='1'
    elif fault=='scored':p['summary'][2][1]='1'
    elif fault=='wrong_total':p['summary'][0][1]='9'
    elif fault=='wrong_minutes':p['summary'][1][1]="631'"
    elif fault=='wrong_role':p['rows'][3][2]='Verteidiger'
    elif fault=='wrong_country':p['rows'][2][1]='GER'
    elif fault=='future':p['rows'][2][0]='27.03.2027'
    elif fault=='non80_u17':data['profiles'][0]['rows'][1][1]="90'"
    elif fault=='unsupported_finals':p['rows'][3][0]='EM - Endrunde'
    elif fault=='duplicate_summary':p['summary'].append(deepcopy(p['summary'][0]))
    elif fault=='eligible_player':now=datetime(2018,1,1,tzinfo=timezone.utc)
    else:data['profiles']=[]
    with pytest.raises(UpdateError) as exc:parse_keeper_youth(mapping,data,now)
    assert exc.value.code=='sfv_youth_unproven'


class Wiki:
    def get_page_identity(self,title):
        age=21 if title.startswith('Молодёжная') else int(title.split('(до ')[1].split()[0])
        canonical=f'Сборная Швейцарии по футболу (до {age} '+('года)' if age==21 else 'лет)')
        return {'title':canonical,'qid':'Q'+str(age)}
    def get_foreign_page_identity(self,title,lang):
        return {'title':title,'qid':'Q'+title.split('under-')[1].split()[0]}


def test_full_cycle_updates_wrong_keeper_rows_without_changing_plain_labels_or_dates():
    class API:
        def national_secondary_source(self,pid):return source()
    rows='\n'.join([
        '|2013—2014|{{Флаг Швейцарии|15px}} Швейцария (до 17)|3 (-5)',
        '|2015—2016|{{Флаг Швейцарии|15px}} Швейцария (до 19)|4 (-7)',
        '|2016|{{Флаг Швейцарии|15px}} Швейцария (до 20)|2 (-4)',
        '|2016—2018|{{Флаг Швейцарии|15px}} [[Молодёжная сборная Швейцарии по футболу|Швейцария (до 21)]]|9 (-11)'])
    text=MINI.replace('Кальвин Браккельман',MAPPING['wiki_name']).replace('|2016—2017|'+U18+'|6 (0)',rows)
    text=text.replace('{{Футболист','{{Футболист\n| позиция = [[Вратарь (футбол)|вратарь]]',1)
    snap=replace(snapshot(text),title='Участник:Zambrowski/testbot2',revid=155529405)
    ops,issues,scopes=prepare_youth_nationals(snap,MAPPING,API(),Wiki(),NOW,goalkeeper=True)
    assert not issues and len(scopes)==4 and len(ops)==2
    validate_package({'schema_version':'1.1','package_id':'sfv-real','generated_at':NOW.isoformat(),
                      'articles':[{'title':snap.title,'base_revid':snap.revid,'player':MAPPING['wiki_name'],'operations':ops}]})
    assert [o['new']['goals'] for o in ops]==[-10,-13]
    for op in ops:text,status=execute_operation(text,MAPPING['wiki_name'],op);assert status=='ready'
    assert '|7 (-10)' in text and '|9 (-13)' in text
    assert prepare_youth_nationals(replace(snap,text=text),MAPPING,API(),Wiki(),NOW,goalkeeper=True)[0]==[]
    assert 'Швейцария (до 19)' in text

@pytest.mark.parametrize('missing',[17,20,21])
def test_missing_wikipedia_youth_article_does_not_cancel_other_verified_keeper_rows(missing):
    class API:
        def national_secondary_source(self,pid):return source()
    class PartialWiki(Wiki):
        def get_page_identity(self,title):
            age=21 if title.startswith('Молодёжная') else int(title.split('(до ')[1].split()[0])
            if age==missing:raise UpdateError('entity_not_found','No separate Wikipedia page')
            return super().get_page_identity(title)
    rows='\n'.join([
        '|2013—2014|{{Флаг Швейцарии|15px}} Швейцария (до 17)|3 (-5)',
        '|2015—2016|{{Флаг Швейцарии|15px}} Швейцария (до 19)|4 (-7)',
        '|2016|{{Флаг Швейцарии|15px}} Швейцария (до 20)|2 (-4)',
        '|2016—2018|{{Флаг Швейцарии|15px}} [[Молодёжная сборная Швейцарии по футболу|Швейцария (до 21)]]|9 (-11)'])
    text=MINI.replace('Кальвин Браккельман',MAPPING['wiki_name']).replace('|2016—2017|'+U18+'|6 (0)',rows)
    text=text.replace('{{Футболист','{{Футболист\n| позиция = [[Вратарь (футбол)|вратарь]]',1)
    snap=snapshot(text)
    ops,issues,scopes=prepare_youth_nationals(snap,MAPPING,API(),PartialWiki(),NOW,goalkeeper=True)
    assert len(scopes)==4 and any(i.get('age')==missing and i['code']=='entity_not_found' for i in issues)
    assert [o['entity']['age'] for o in ops]==([19] if missing==21 else [19,21])
    assert not next(s for s in scopes if s['age']==missing).get('career_binding')
    for op in ops:text,status=execute_operation(text,MAPPING['wiki_name'],op);assert status=='ready'
    assert '|7 (-10)' in text
    assert ('|9 (-13)' in text)==(missing!=21)
    assert prepare_youth_nationals(snapshot(text),MAPPING,API(),PartialWiki(),NOW,goalkeeper=True)[0]==[]
