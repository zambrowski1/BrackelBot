# SPDX-License-Identifier: MIT
"""Bind the date of the whole national card to all verified career rows."""
from copy import deepcopy
from datetime import date,datetime
from .wikitext_parser import WikitextParser
from .national_team_identity import valid_qid
from .errors import UpdateError


def close_national_card(snapshot,mapping,operations,scopes,now):
    try:
        parser=WikitextParser(snapshot.text)
        rows=parser.career('национальная сборная',mapping['wiki_name'])
        if not rows or len(rows)!=len(scopes):return operations,False
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
        old=parser.infobox(mapping['wiki_name']).get('обновление данных о сборной')
        if old is None:return operations,False
        old_day=datetime.strptime(old.text.strip(),'%d.%m.%Y').date()
        latest=max(days)
        # A newer existing date cannot be justified by older cached matches.
        if old_day>latest:return operations,False
        if old_day==latest:return operations,True
        linked=deepcopy(operations)
        for op in linked:
            previous=op['as_of']
            op.update(group_id='complete-national-card',as_of=now.date().isoformat())
            for source in op['sources']:
                source['coverage_as_of']=op['as_of']
                source['note']=source.get('note','')+'; исходная граница данных '+previous
        linked.append({'id':'national-card-date','type':'update_date','group_id':'complete-national-card',
            'entity':{'name':'Статистика сборных','wikitext':binding['team'],
                      'kind':'national_team','wikidata_id':binding['qid']},
            'target':{'structure':'career','field':'национальная сборная'},
            'as_of':now.date().isoformat(),'sources':sources,
            'payload':{'field':'обновление данных о сборной','expected_value':old.text.strip(),
                       'statistics_as_of':latest.isoformat()}})
        return linked,True
    except (UpdateError,KeyError,TypeError,ValueError):return operations,False
