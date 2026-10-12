# SPDX-License-Identifier: MIT
"""Prepare complete national-list reconciliation and explicit sandbox samples."""
from datetime import datetime,timezone
import hashlib
import re
from .errors import UpdateError
from .sbmatches_source import without_generated_match_links,profile_url,has_profile_reference
from .wikitext_parser import WikitextParser
from .sbmatches_editor import read_matches,metric_field
from .sofascore_timeline import timeline_numbers
from .test_pages import bound_test_player

OPPONENTS={'Ireland':('Сборная Ирландии по футболу','Ирландия'),
           'Armenia':('Сборная Армении по футболу','Армения')}


def full_repair(snapshot,mapping,scope,wiki,*,goalkeeper=False):
    """Replace the bound draft's table only after verifying the complete scope."""
    if not scope or scope.get('complete') is not True:
        return None
    from .national_team_identity import NationalTeamIdentity
    from .national_match_format import format_scope
    resolver=NationalTeamIdentity(wiki)
    own=resolver.resolve(scope['team'])
    selected=[]
    for template in WikitextParser(snapshot.text).templates:
        if template.name!='сбматчи':continue
        values=template.values();team=values.get('сборная');age=values.get('возраст')
        if not team or age and age.text.strip():continue
        if resolver.text_identity(team.text.strip()).get('qid')==own['wikidata_id']:
            selected.append((template,team.text.strip()))
    if len(selected)!=1:return None
    template,team_text=selected[0]
    if metric_field(template.values())!=('пропущенные голы' if goalkeeper else 'голы'):
        raise UpdateError('goal_metric_mismatch','Столбец СбМатчи не совпадает с проверенной метрикой')
    if hasattr(wiki,'_api'):
        resolver.prime([verified['match'][side] for verified in scope['matches']
                        for side in ('home_team','away_team')])
    rows,sources=format_scope(scope,resolver.resolve,goalkeeper=goalkeeper)
    if not rows:return None
    from .sbmatches_reconcile import compose,sha,external_marker
    values,existing=read_matches(template)
    by_key={}
    from .sbmatches_editor import match_date
    for index,row in enumerate(existing,1):
        key=(match_date(row['дата']),resolver.text_identity(row['соперник'])['qid'])
        if key in by_key:raise UpdateError('duplicate_match','Повторный старый матч СбМатчи')
        by_key[key]=index
    old_indices=[]
    for verified,row in zip(scope['matches'],rows):
        game=verified['match']
        opponent=game['away_team' if game['home_team']['id']==scope['team']['id'] else 'home_team']
        key=(match_date(row['дата']),resolver.resolve(opponent)['wikidata_id'])
        old_indices.append(by_key.get(key,0))
    if set(i for i in old_indices if i)!=set(by_key.values()):
        raise UpdateError('national_existing_match_unmatched','API не сопоставил все старые матчи; таблица сохранена')
    marker=external_marker(snapshot.text,template)
    source_url=profile_url(snapshot,mapping)
    metric=metric_field(values)
    # A replay must not rewrite compact rows merely to switch their format.
    # Compare the fields that this operation can change, including provenance
    # and the date, before constructing a replacement template.
    date_field=marker.values().get('1') if marker else values.get('обновлено')
    date_current=bool(date_field and date_field.text.strip() and
                      match_date(date_field.text.strip()).isoformat()>=scope['statistics_as_of'])
    if date_current and old_indices==list(range(1,len(existing)+1)):
        overall=marker.values().get('2') if marker else values.get('источник')
        unchanged=bool(overall and has_profile_reference(overall.text,source_url))
        for i,(old,new,source) in enumerate(zip(existing,rows,sources),1):
            citation=values.get('источник'+str(i))
            if (old['счёт']!=new['счёт'] or old[metric]!=new[metric]
                    or new.get('пенальти') and new['пенальти']!=old.get('пенальти')
                    or citation and without_generated_match_links(citation.text)!=citation.text.strip()):
                unchanged=False;break
        if unchanged:return None
    payload={'expected_template_sha256':sha(str(template.node)),
             'replacement_sha256':'0'*64,'complete':True,'totals':scope['numbers'],
             'matches':rows,'old_indices':old_indices,'source_url':source_url,
             'source_access_date':datetime.now(timezone.utc).date().isoformat()}
    if marker:payload['external_updated_sha256']=sha(str(marker.node))
    operation={'id':'reconcile-full-sbmatches','type':'reconcile_sbmatches','group_id':'full-national-scope',
        'entity':{'name':own['name'],'kind':'national_team','age':None,'wikidata_id':own['wikidata_id'],
                  'page_title':own['page_title'],'wikitext':team_text},
        'target':{'structure':'sbmatches','age':None},'as_of':scope['statistics_as_of'],
        'sources':sources,'payload':payload}
    payload['replacement_sha256']=sha(compose(template,operation))
    if not marker and payload['replacement_sha256']==payload['expected_template_sha256']:
        return None
    return operation



def create_missing(snapshot,mapping,scope,wiki,*,goalkeeper=False):
    """Create a new table only from a complete, independently checked scope."""
    if not scope or scope.get('complete') is not True:return None
    if any(t.name=='сбматчи' for t in WikitextParser(snapshot.text).templates):return None
    from .native_national_table import possible_national_statistics
    if possible_national_statistics(snapshot.text):
        raise UpdateError('possible_national_statistics','Возможна существующая статистика сборной — нужна ручная проверка')
    from .national_team_identity import NationalTeamIdentity
    from .national_match_format import format_scope
    resolver=NationalTeamIdentity(wiki)
    own=resolver.resolve(scope['team'])
    if hasattr(wiki,'_api'):
        resolver.prime([v['match'][side] for v in scope['matches'] for side in ('home_team','away_team')])
    rows,sources=format_scope(scope,resolver.resolve,goalkeeper=goalkeeper)
    if not rows:return None
    operation = {'id':'create-full-sbmatches','type':'create_sbmatches','group_id':'full-national-scope',
        'entity':{'name':own['name'],'kind':'national_team','age':None,'page_title':own['page_title'],
            'wikidata_id':own['wikidata_id'],'wikitext':'[['+own['page_title']+'|'+own['name']+']]'},
        'target':{'structure':'sbmatches','age':None},'as_of':scope['statistics_as_of'],
        'sources':sources,'payload':{'label':mapping['wiki_name']+': матчи за сборную '+own['gen'],
            'complete':True,'totals':scope['numbers'],'matches':rows,
            'metric':'пропущенные голы' if goalkeeper else 'голы',
            'source_url':profile_url(snapshot,mapping),
            'source_access_date':datetime.now(timezone.utc).date().isoformat()}}
    from .native_national_table import replacement_block
    block=replacement_block(snapshot.text,own['gen'])
    if block:
        operation['payload']['expected_native_sha256']=hashlib.sha256(block[2].encode()).hexdigest()
        operation['payload']['native_team_gen']=own['gen']
    return operation


def sample_repair(snapshot,mapping,history,api,wiki,now):
    if not bound_test_player(snapshot.title,mapping) or mapping['api_id']!=136924:return None
    if '<!-- Вымышленные строки с намеренными ошибками. -->' not in snapshot.text:return None
    tables=[t for t in WikitextParser(snapshot.text).templates if t.name=='сбматчи']
    if len(tables)!=1:return None
    t=tables[0]
    try:read_matches(t);return None
    except UpdateError:pass
    values=t.values();team=values.get('сборная')
    if not team or team.text.strip()!='[[Сборная Венгрии по футболу|Венгрию]]':return None
    candidates=[]
    for row in history.get('matches',[]):
        team_id=(row.get('player') or {}).get('team_id');teams=[row.get(k) or {} for k in ('home_team','away_team')]
        own=[x for x in teams if x.get('id')==team_id];other=[x for x in teams if x.get('id')!=team_id]
        if len(own)!=1 or len(other)!=1 or own[0].get('name')!='Hungary' or any(x.get('is_national') is not True or (x.get('sport') or {}).get('slug')!='football' for x in teams):continue
        if other[0].get('name') not in OPPONENTS or (row.get('tournament') or {}).get('id')!=11:continue
        if not re.fullmatch(r'20\d{2}',str((row.get('season') or {}).get('year',''))):continue
        try:day=datetime.fromisoformat(row['start_time'].replace('Z','+00:00'))
        except (KeyError,TypeError,ValueError):continue
        if day.tzinfo is None or day>now or day.year!=now.year-1 or row.get('is_finished') is not True:continue
        candidates.append((row,other[0],day))
    rows=[];sources=[]
    for row,other,day in sorted(candidates,key=lambda x:x[2],reverse=True)[:6]:
        try:
            nums=timeline_numbers(row,mapping['api_id'],row['player']['team_id'],api.match_lineups(row['id']),api.match_incidents(row['id']))
            if nums is None or nums['appearances']!=1:continue
            title,label=OPPONENTS[other['name']];identity=wiki.get_page_identity(title)
            if identity.get('title')!=title or not identity.get('qid'):continue
            home=row['home_team']['id']==row['player']['team_id']
            a,b=row['home_score']['current'],row['away_score']['current']
            rows.append({'дата':day.strftime('%d-%m-%Y'),'соперник':'[['+title+'|'+label+']]',
                'счёт':str(a if home else b)+':'+str(b if home else a),'голы':str(nums['goals']),
                'соревнование':'Отборочный турнир чемпионата мира — '+row['season']['year']})
            sources.append({'url':row.get('link') or 'https://www.sofascore.com/football/match/'+str(row['id']),
                'provenance':'sofascore','coverage_as_of':now.date().isoformat(),
                'note':'Подтверждённый состав и полная лента событий; полигонная выборка, не полная статистика сборной'})
            if len(rows)==2:break
        except UpdateError as exc:
            if exc.code in {'quota_exhausted','api_rate_limited','api_authentication'}:break
    if len(rows)!=2:return None
    paired=sorted(zip(rows,sources),key=lambda x:datetime.strptime(x[0]['дата'],'%d-%m-%Y'))
    return {'id':'repair-sandbox-sbmatches','type':'repair_sbmatches','group_id':'sandbox-sbmatches-repair',
        'entity':{'name':'Венгрия','kind':'national_team','age':None,'wikitext':team.text.strip()},
        'target':{'structure':'sbmatches','age':None},'as_of':now.date().isoformat(),
        'sources':[s for _,s in paired], 'payload':{'expected_template_sha256':hashlib.sha256(str(t.node).encode()).hexdigest(),
            'label':'Полигонная выборка: два подтверждённых матча Вилли Орбана за Венгрию',
            'matches':[r for r,_ in paired],
            'source_url':profile_url(snapshot,mapping),
            'source_access_date':datetime.now(timezone.utc).date().isoformat()}}
