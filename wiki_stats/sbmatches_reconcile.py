# SPDX-License-Identifier: MIT
"""Reconcile a complete API list without deleting existing matches or sources."""
from datetime import date
import hashlib
import re
import mwparserfromhell as mw
from .errors import UpdateError
from .wikitext_parser import WikitextParser
from .sbmatches_editor import read_matches,metric_field,validate_row,match_date,GENERAL,EXTRAS
from .change_validator import apply_patches,references
from .models import Patch
from .sbmatches_source import profile_citation,without_generated_match_links,merge_profile_citation,added_profile_references,has_profile_reference,statistics_date


def sha(text):return hashlib.sha256(text.encode('utf-8')).hexdigest()


def selected(text,player,op):
    parser=WikitextParser(text);parser.infobox(player)
    found=[]
    for t in parser.templates:
        if t.name!='сбматчи':continue
        values=t.values();team=values.get('сборная');age=values.get('возраст')
        if team and team.text.strip()==op['entity']['wikitext'].strip() and (age.text.strip() if age else '')==(str(op['target']['age']) if op['target']['age'] else ''):found.append(t)
    if len(found)!=1:raise UpdateError('ambiguous_target','Сборная и возраст не определяют единственный СбМатчи')
    return found[0]


def compose(template,op):
    values,old=read_matches(template);metric=metric_field(values);p=op['payload']
    rows=p['matches'];indices=p['old_indices'];totals=p['totals']
    profile=profile_citation(p,op['as_of'])
    if p.get('complete') is not True or len(rows)!=totals['appearances'] or len(rows)!=len(indices) or len(op['sources'])!=len(rows):
        raise UpdateError('national_aggregate_mismatch','Нужен полный список с итогом и источником каждого матча')
    used=[i for i in indices if i]
    if any(type(i) is not int or not 0<=i<=len(old) for i in indices) or len(set(used))!=len(used) or set(used)!=set(range(1,len(old)+1)):
        raise UpdateError('national_existing_match_unmatched','API не сопоставил все старые матчи; удаление запрещено')
    expected=-totals['goals'] if metric=='пропущенные голы' else totals['goals']
    for row in rows:validate_row(row,metric)
    if sum(int(r.get(metric,'0') or '0') for r in rows)!=expected:
        raise UpdateError('national_aggregate_mismatch','Голы списка не совпали с итогом API')
    nl='\r\n' if '\r\n' in str(template.node) else '\n'
    node=mw.nodes.Template('СбМатчи'+nl)
    def add(key,value):node.add(key,str(value)+nl,preserve_spacing=False)
    for key,value in values.items():
        if key in GENERAL and key not in {'обновлено','источники'}:add(key,value.text.strip())
    day=date.fromisoformat(statistics_date(op))
    if not p.get('external_updated_sha256'):
        old_date=values.get('обновлено')
        if old_date and old_date.text.strip():day=max(day,match_date(old_date.text.strip()))
        add('обновлено',day.strftime('%d-%m-%Y'))
        if profile:
            prior_source=values.get('источник')
            citation=prior_source.text.strip() if prior_source else ''
            citation=merge_profile_citation(citation,p,op['as_of'])
            if node.has('источник'):node.remove('источник')
            add('источник',citation)
    previous=None;identities=set()
    for i,(incoming,index,source) in enumerate(zip(rows,indices,op['sources']),1):
        new_day=validate_row(incoming,metric)
        if new_day>date.fromisoformat(statistics_date(op)) or previous and new_day<previous:
            raise UpdateError('invalid_match','Нарушен порядок или дата матча')
        previous=new_day
        row=dict(incoming)
        if index:
            prior=old[index-1]
            if match_date(prior['дата'])!=new_day:raise UpdateError('national_existing_match_unmatched','Дата старого матча не совпала')
            row['дата']=prior['дата'];row['соперник']=prior['соперник'];row['соревнование']=prior['соревнование']
            for key in EXTRAS:
                if prior.get(key) and not row.get(key):row[key]=prior[key]
            if prior.get('пенальти') and not row.get('пенальти'):row['пенальти']=prior['пенальти']
        identity=(new_day,row['соперник'])
        if identity in identities:raise UpdateError('duplicate_match','Повторный матч API')
        identities.add(identity);validate_row(row,metric)
        for key,value in row.items():
            if not index and (any(c in value for c in '\r\n') or any(isinstance(n,mw.nodes.Text) and '|' in str(n) for n in mw.parse(value).nodes)):
                raise UpdateError('invalid_match','Разделители в новом матче')
            if value:add(key+str(i),value)
        url=source['url']
        if not re.fullmatch(r'https://www\.sofascore\.com/[^\s|{}<>\[\]]+',url):raise UpdateError('invalid_input','Нужна ссылка Sofascore на матч')
        old_source=values.get('источник'+str(index)) if index else None
        citation=old_source.text.strip() if old_source else ''
        # Preserve editor citations; API evidence is not printed in each row.
        if profile:citation=without_generated_match_links(citation)
        if citation:add('источник'+str(i),citation)
    if 'источники' in values:add('источники',values['источники'].text.strip())
    rendered=str(node)
    raw=str(template.node)
    apply_patches(raw,[Patch(0,len(raw),raw,rendered,'national_source')],structural=True,
                  added_references=added_profile_references(raw,rendered,p,op['as_of']))
    old_comments=[str(c) for c in mw.parse(str(template.node)).filter_comments()]
    if old_comments!=[str(c) for c in mw.parse(rendered).filter_comments()]:raise UpdateError('integrity_error','Изменились комментарии СбМатчи')
    read_matches(next(t for t in WikitextParser(rendered).templates if t.name=='сбматчи'))
    return rendered


def external_marker(text,template):
    markers=[t for t in WikitextParser(text).templates if t.name=='обновлено' and t.start<template.start
             and not text[t.start+len(str(t.node)):template.start].strip()]
    # A shared date is not silently changed for other age categories.
    if len(markers)==1 and sum(t.name=='сбматчи' for t in WikitextParser(text).templates)==1:return markers[0]
    return None


def reconcile(text,player,op):
    template=selected(text,player,op);p=op['payload'];raw=str(template.node)
    if sha(raw)==p['replacement_sha256']:
        read_matches(template)
        marker=external_marker(text,template) if p.get('external_updated_sha256') else None
        marker_ready=not p.get('external_updated_sha256') or bool(
            marker and marker.values().get('1') and
            match_date(marker.values()['1'].text.strip())>=date.fromisoformat(statistics_date(op)) and
            (not p.get('source_url') or marker.values().get('2') and has_profile_reference(marker.values()['2'].text,p['source_url'])))
        if marker_ready:return text,'already_applied'
        # The table can be unchanged while its external date/source needs updating.
        if sha(raw)!=p['expected_template_sha256']:
            raise UpdateError('old_value_mismatch','Шаблон обновлён отдельно от его даты или источника')
    if sha(raw)!=p['expected_template_sha256']:raise UpdateError('old_value_mismatch','СбМатчи изменился после подготовки diff')
    rendered=compose(template,op)
    if sha(rendered)!=p['replacement_sha256']:raise UpdateError('old_value_mismatch','Изменились ожидаемые данные обновления')
    patches=[Patch(template.start,template.start+len(raw),raw,rendered,'reconcile_sbmatches')]
    if p.get('external_updated_sha256'):
        marker=external_marker(text,template)
        if marker is None or sha(str(marker.node))!=p['external_updated_sha256']:raise UpdateError('old_value_mismatch','Изменилась внешняя дата таблицы')
        updated=mw.parse(str(marker.node)).filter_templates()[0]
        prior=marker.values().get('1')
        if not prior:raise UpdateError('invalid_input','Нет даты в Обновлено')
        value=max(match_date(prior.text.strip()),date.fromisoformat(statistics_date(op)))
        updated.add('1',value.isoformat(),preserve_spacing=False)
        profile=profile_citation(p,op['as_of'])
        if profile:
            extra=str(updated.get('2').value).strip() if updated.has('2') else ''
            updated.add('2',merge_profile_citation(extra,p,op['as_of']),preserve_spacing=False)
        patches.append(Patch(marker.start,marker.start+len(str(marker.node)),str(marker.node),str(updated),'national_date'))
    allowed=[]
    for patch in patches:allowed.extend(added_profile_references(patch.before,patch.after,p,op['as_of']))
    return apply_patches(text,patches,structural=True,added_references=allowed),'ready'
