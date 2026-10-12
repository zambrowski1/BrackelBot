# SPDX-License-Identifier: MIT
"""Revision-bound repair of a sandbox fixture with a labelled verified sample."""
import hashlib,re
from datetime import date
import mwparserfromhell as mw
from .errors import UpdateError
from .sbmatches_source import profile_citation,merge_profile_citation,statistics_date
from .wikitext_parser import WikitextParser
from .sbmatches_editor import GENERAL, EXTRAS, read_matches, metric_field, validate_row


def repair(text,player,op):
    parser=WikitextParser(text);parser.infobox(player);selected=[]
    for t in parser.templates:
        if t.name.removeprefix('шаблон:')!='сбматчи':continue
        values=t.values();team=values.get('сборная');age=values.get('возраст')
        if team and team.text.strip()==op['entity']['wikitext'].strip() and (age.text.strip() if age else '')==(str(op['target']['age']) if op['target']['age'] else ''):selected.append(t)
    if len(selected)!=1:raise UpdateError('ambiguous_target','Нужен единственный СбМатчи с указанной сборной и возрастом')
    t=selected[0];values=t.values();payload=op['payload'];metric=metric_field(values)
    complete=payload.get('complete') is True
    profile=profile_citation(payload,op['as_of'])
    if (not complete and not payload['label'].startswith('Полигонная выборка:')
            or any(c in payload['label'] for c in '|{}<>\r\n')):
        raise UpdateError('invalid_input','Нужен явный полигонный заголовок либо подтверждённый полный список')
    if not complete and len(payload['matches'])>20:
        raise UpdateError('invalid_input','Выборка ограничена 20 матчами')
    if complete:
        totals=payload.get('totals') or {}
        if (type(totals.get('appearances')) is not int or type(totals.get('goals')) is not int
                or totals['appearances']!=len(payload['matches'])):
            raise UpdateError('source_scope_mismatch','Полный список должен совпасть с подтверждённым числом выступлений')
    if len(op['sources'])!=len(payload['matches']):raise UpdateError('source_scope_mismatch','Каждому матчу нужен отдельный источник')
    for key in values:
        if key not in GENERAL and not re.fullmatch(r'(матч|дата|соперник|счёт|голы|соревнование|источник|пенальти|пропущенные голы|место|передачи|минуты|примечание)[1-9][0-9]*',key):raise UpdateError('unsupported_structure','Неизвестный параметр СбМатчи: '+key)
    nl='\r\n' if '\r\n' in str(t.node) else '\n';lines=['{{СбМатчи']
    for key,value in values.items():
        if key in GENERAL and key not in {'заголовок','обновлено','источник'}:lines.append('|'+key+'='+value.text.strip())
    lines.extend(['|заголовок='+payload['label'],'|обновлено='+date.fromisoformat(statistics_date(op)).strftime('%d-%m-%Y')]);days=[];keys=set()
    old_source=values.get('источник')
    citation=old_source.text.strip() if old_source else ''
    if profile:citation=merge_profile_citation(citation,payload,op['as_of'])
    if citation:lines.append('|источник='+citation)
    for i,row in enumerate(payload['matches'],1):
        day=validate_row(row,metric);days.append(day);identity=(day,row['соперник'])
        if identity in keys or day>date.fromisoformat(statistics_date(op)):raise UpdateError('invalid_match','Повторный матч или дата за пределами источника')
        keys.add(identity)
        for value in row.values():
            code=mw.parse(value)
            if any(c in value for c in (';;','\n','\r','<','{{','}}')) or any(isinstance(n,mw.nodes.Text) and '|' in str(n) for n in code.nodes):raise UpdateError('invalid_match','Недопустимая разметка нового матча')
        source=op['sources'][i-1]['url']
        if any(c in source for c in '|{}<>[]\r\n'):raise UpdateError('invalid_input','Недопустимый URL источника')
        lines.append('|матч'+str(i)+'='+' ;; '.join(row.get(f,'') for f in ('дата','соперник','счёт',metric,'соревнование')))
        if not profile:lines.append('|источник'+str(i)+'=['+source+']')
        for field in EXTRAS:
            if row.get(field):lines.append('|'+field+str(i)+'='+row[field])
        if row.get('пенальти'):lines.append('|пенальти'+str(i)+'='+row['пенальти'])
    if days!=sorted(days):raise UpdateError('invalid_match','Нарушен порядок дат')
    if complete:
        summed=sum(int(row.get(metric,'').strip() or '0') for row in payload['matches'])
        expected=-totals['goals'] if metric=='пропущенные голы' else totals['goals']
        if summed!=expected:
            raise UpdateError('source_scope_mismatch','Голы полного списка не совпадают с подтверждённым итогом')
    replacement=nl.join(lines)+nl+'}}'
    if str(t.node)==replacement:read_matches(t);return text,'already_applied'
    if hashlib.sha256(str(t.node).encode('utf-8')).hexdigest()!=payload['expected_template_sha256']:raise UpdateError('old_value_mismatch','СбМатчи изменился после подготовки пакета')
    code=mw.parse(str(t.node))
    if code.filter_comments() or any(str(tag.tag).lower()=='ref' for tag in code.filter_tags()):raise UpdateError('integrity_error','Сноски и комментарии внутри СбМатчи требуют ручного переноса')
    result=text[:t.start]+replacement+text[t.start+len(str(t.node)):]
    read_matches(next(x for x in WikitextParser(result).templates if x.start==t.start))
    return result,'ready'
