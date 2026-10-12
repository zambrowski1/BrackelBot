# SPDX-License-Identifier: MIT
"""Date the national card by confirmed matches; youth rows need not block seniors."""
from copy import deepcopy
from datetime import date,datetime
from .wikitext_parser import WikitextParser
from .national_team_identity import valid_qid
from .errors import UpdateError
import re
import mwparserfromhell as mw


def youth_row(text):
    code=mw.parse(text)
    names=[code.strip_code(),*[str(link.title) for link in code.filter_wikilinks()]]
    return any(re.search(r'\(\s*до\s+[0-9]{1,2}(?:\s+(?:лет|года))?\s*\)',name) for name in names)


def non_senior_row(text):
    if youth_row(text):return True
    code=mw.parse(text);links=code.filter_wikilinks()
    if links:
        return all(re.match(r'^олимпийская сборная .+ по футболу$',str(link.title).strip(),re.I) for link in links)
    return bool(re.search(r'\(\s*олимп\.?\s*\)\s*$',code.strip_code().strip(),re.I))


def close_national_card(snapshot,mapping,operations,scopes,now):
    try:
        parser=WikitextParser(snapshot.text)
        rows=parser.career('национальная сборная',mapping['wiki_name'])
        if not rows or not scopes:return operations,False
        covered=set();sources=[];days=[]
        for scope in scopes:
            binding=scope.get('career_binding')
            if scope.get('complete') is not True or not binding or not valid_qid(binding.get('qid')):
                return operations,False
            own=[i for i,(period,team,value) in enumerate(rows)
                 if period==binding['period'] and team==binding['team']]
            if len(own)!=1 or own[0] in covered:return operations,False
            covered.add(own[0]);period,team,value=rows[own[0]]
            numbers=dict(zip(('appearances','goals'),[n.value for n in value.numeric(pair=True)]))
            if numbers!=binding['numbers']:return operations,False
            relevant=[op for op in operations if op.get('target',{}).get('structure')=='career'
                and op['target'].get('field')=='национальная сборная'
                and op.get('entity',{}).get('wikitext')==team]
            projected=dict(numbers);projected_period=period
            for op in relevant:
                if op['target'].get('period')!=projected_period:return operations,False
                if op['type']=='update_career_period':projected_period=op['payload']['period']
                elif op['type']=='update_stats':
                    if op['expected']!=projected:return operations,False
                    projected=op['new']
                else:return operations,False
            if projected!=scope['numbers'] or projected_period!=binding['verified_period']:
                return operations,False
            day=date.fromisoformat(scope['statistics_as_of'])
            if day>now.date() or not scope.get('matches'):return operations,False
            if max(date.fromisoformat(g['date']) for g in scope['matches'])!=day:return operations,False
            days.append(day)
            urls=scope.get('sources') or [g['match'].get('link') for g in scope['matches']]
            for url in urls:
                if not isinstance(url,str) or not url.startswith('https://'):return operations,False
                provenance='sofascore' if url.startswith('https://www.sofascore.com/') else 'dfb' if url.startswith('https://datencenter.dfb.de/') else 'uefa' if url.startswith('https://www.uefa.com/') else 'sfv'
                sources.append({'url':url,'provenance':provenance,'coverage_as_of':now.date().isoformat(),
                    'note':'Полный итог этой сборной перепроверен; последний сыгранный матч '+day.isoformat()})
        if any(i not in covered and not non_senior_row(team) for i,(_,team,_) in enumerate(rows)):
            return operations,False
        old=parser.infobox(mapping['wiki_name']).get('обновление данных о сборной')
        old_value=old.text.strip() if old else None
        old_day=datetime.strptime(old_value,'%d.%m.%Y').date() if old_value else None
        latest=max(days)
        if old_day==latest and old_value==latest.strftime('%d.%m.%Y'):return operations,True
        linked=deepcopy(operations)
        for op in linked:
            previous=op['as_of']
            if op['type'] in {'create_sbmatches','repair_sbmatches','reconcile_sbmatches'}:
                op['payload'].setdefault('statistics_as_of',previous)
            op.update(group_id='complete-national-card',as_of=now.date().isoformat())
            for source in op['sources']:
                source['coverage_as_of']=op['as_of']
                source['note']=source.get('note','')+'; исходная граница данных '+previous
        linked.append({'id':'national-card-date','type':'update_date','group_id':'complete-national-card',
            'entity':{'name':'Статистика сборных','wikitext':binding['team'],
                      'kind':'national_team','wikidata_id':binding['qid']},
            'target':{'structure':'career','field':'национальная сборная'},
            'as_of':now.date().isoformat(),'sources':sources,
            'payload':{'field':'обновление данных о сборной','expected_value':old_value,
                       'statistics_as_of':latest.isoformat()}})
        return linked,True
    except (UpdateError,KeyError,TypeError,ValueError):return operations,False
