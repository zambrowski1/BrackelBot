# SPDX-License-Identifier: MIT
"""Lossless, revision-bound conversion of one numbered national match table."""
from datetime import date
import hashlib
import json
import re
import mwparserfromhell as mw
from .errors import UpdateError
from .models import Patch
from .change_validator import apply_patches, references
from .native_national_table import find_tables, possible_national_statistics
from .sbmatches_editor import read_matches, validate_row
from .wikitext_parser import WikitextParser

MONTHS=('января','февраля','марта','апреля','мая','июня','июля','августа','сентября','октября','ноября','декабря')


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def plain(value):
    return re.sub(r'\s+',' ',mw.parse(value).strip_code()).strip()


def old_date(value):
    code=mw.parse(value)
    templates=code.filter_templates()
    if templates:
        if len(templates)!=1 or str(templates[0]).strip()!=value.strip() or str(templates[0].name).strip().casefold() not in {'date','дата'}:
            raise UpdateError('needs_review','Неизвестный формат даты старого матча')
        t=templates[0]
        try:result=date(int(str(t.get('3').value)),int(str(t.get('2').value)),int(str(t.get('1').value)))
        except (ValueError,KeyError):raise UpdateError('invalid_match','Неверная старая дата') from None
    else:
        text=plain(value)
        match=re.fullmatch(r'(\d{1,2}) ('+'|'.join(MONTHS)+r') (\d{4})(?: года)?',text)
        try:
            if match:result=date(int(match[3]),MONTHS.index(match[2])+1,int(match[1]))
            elif re.fullmatch(r'\d{4}-\d{2}-\d{2}',text):result=date.fromisoformat(text)
            else:
                from .sbmatches_editor import match_date
                result=match_date(text)
        except ValueError:raise UpdateError('invalid_match','Неверная старая дата') from None
    return result.isoformat()


def team_identity(text,player,caption):
    from .national_table_formats import caption_person
    suffix=caption_person(caption,player)
    box=WikitextParser(text).infobox(player)
    field=box.get('национальная сборная')
    candidates={}
    if field:
        for link in field.code.filter_wikilinks():
            title=str(link.title).strip()
            m=re.fullmatch(r'Сборная (.+?) по футболу(?: \(до (\d+) (?:лет|года)\))?',title)
            if not m:continue
            age=int(m[2]) if m[2] else None
            expected=m[1]+(' до '+str(age)+' '+('года' if age==21 else 'лет') if age else '')
            if suffix.casefold()==expected.casefold():
                candidates[title]=(str(link),age)
    if len(candidates)!=1:
        raise UpdateError('national_identity_mismatch','Заголовок таблицы не совпал с единственной сборной в карточке')
    title,(link,age)=next(iter(candidates.items()))
    return {'name':title,'kind':'national_team','wikitext':link,'age':age}


def other_blocks(text,player,entity):
    from .national_table_formats import classify,table_parts
    for table in mw.parse(text).filter_tags():
        if str(table.tag).casefold()!='table':continue
        kind=classify(table)
        if kind=='annual_summary':text=text.replace(str(table),'',1)
        elif kind=='match_list':
            try:
                caption,_,_=table_parts(table);other=team_identity(text,player,caption)
                if other['age']!=entity['age']:text=text.replace(str(table),'',1)
            except UpdateError:pass
    for template in WikitextParser(text).templates:
        if template.name!='сбматчи':continue
        values=template.values();age=values.get('возраст');team=values.get('сборная')
        if age and team and age.text.strip().isdigit() and int(age.text.strip())!=entity['age']:
            links=team.code.filter_wikilinks()
            box=WikitextParser(text).infobox(player).get('национальная сборная')
            if len(links)==1 and box and str(links[0].title) in {str(x.title) for x in box.code.filter_wikilinks()}:
                read_matches(template);text=text.replace(str(template.node),'',1)
    return possible_national_statistics(text,ignore_heading_only=True)


def old_summary(tail,rows,metric):
    nodes=list(mw.parse(tail).nodes);prefix='';bold=None;count=0
    for n in nodes:
        if isinstance(n,mw.nodes.Text) and not str(n).strip():prefix+=str(n);count+=1;continue
        if isinstance(n,mw.nodes.Tag) and str(n.tag).casefold()=='b' and plain(str(n.contents)).startswith('Итого:'):
            bold=n;count+=1
        break
    if bold is None:return '',[]
    span=prefix+str(bold)
    for n in nodes[count:]:
        if isinstance(n,mw.nodes.Tag) and str(n.tag).casefold()=='ref':span+=str(n)
        elif isinstance(n,mw.nodes.Text) and str(n).startswith('.'):
            span+='.';break
        else:break
    from .national_table_formats import visible
    nums=[int(x.replace('−','-')) for x in re.findall(r'[-−]?\d+',visible(str(bold.contents)))]
    won=sum(int(r['счёт'].split(':')[0])>int(r['счёт'].split(':')[1]) for r in rows)
    draw=sum(r['счёт'].split(':')[0]==r['счёт'].split(':')[1] for r in rows)
    expected=[len(rows),sum(int(r[metric]) for r in rows),won,draw,len(rows)-won-draw]
    if len(nums) not in {2,5} or nums!=expected[:len(nums)] or mw.parse(span).filter_comments():
        raise UpdateError('needs_review','Старый итог не совпал со строками таблицы')
    return span,references(span)


def parse_table(text,player,age=None):
    found=find_tables(text)
    if len(found)>1:
        selected=[]
        from .national_table_formats import table_parts
        for offset,candidate in found:
            if not isinstance(candidate,mw.nodes.Tag):continue
            try:
                caption,_,_=table_parts(candidate);identity=team_identity(text,player,caption)
                if identity['age']==age:selected.append((offset,candidate))
            except UpdateError:pass
        found=selected
    if len(found)!=1:raise UpdateError('ambiguous_target','Нужна единственная распознаваемая таблица сборной')
    offset,node=found[0]
    if not isinstance(node,mw.nodes.Tag):raise UpdateError('needs_review','Этот формат таблицы требует ручного переноса')
    raw=str(node)
    if mw.parse(raw).filter_comments():raise UpdateError('needs_review','Комментарии внутри старой таблицы требуют ручного переноса')
    from .national_table_formats import table_parts,columns
    heading,headers,parts=table_parts(node)
    if not heading:raise UpdateError('needs_review','Нужен явный заголовок таблицы с именем сборной')
    entity=team_identity(text,player,heading)
    fields=columns(headers)
    metric='голы' if 'голы' in fields else 'пропущенные голы'
    heading+=''.join(ref for header in headers for ref in references(str(header)))
    rows=[]; sources=[]; previous=None; identities=set(); unordered=False
    for number,row in enumerate(parts,1):
        if str(row.tag).casefold()!='tr':raise UpdateError('needs_review','Неизвестная строка таблицы')
        cells=[c for c in row.contents.nodes if not isinstance(c,mw.nodes.Text) or str(c).strip()]
        if len(cells)!=len(fields) or any(not isinstance(c,mw.nodes.Tag) or str(c.tag).casefold() not in {'td','th'} for c in cells):
            raise UpdateError('needs_review','Число ячеек строки не совпало с заголовками')
        values=[]; refs=[]
        for cell in cells:
            if any(str(a.name).strip().casefold() in {'colspan','rowspan'} for a in cell.attributes):
                raise UpdateError('needs_review','Объединённые ячейки не переносятся автоматически')
            code=mw.parse(str(cell.contents).strip())
            if any(str(t.tag).strip().casefold() in {'table','tr','td','th'} for t in code.filter_tags()):
                raise UpdateError('needs_review','Вложенная таблица требует ручного переноса')
            for tag in list(code.filter_tags()):
                if str(tag.tag).strip().casefold()=='ref':refs.append(str(tag));code.remove(tag)
            values.append(str(code).strip())
        data=dict(zip(fields,values))
        if plain(data['index'])!=str(number):raise UpdateError('invalid_match','Пропущены или повторены номера матчей')
        if len(rows)>=500:raise UpdateError('needs_review','Слишком большая таблица сборной')
        from .national_table_formats import score_and_penalties,extra_time_note
        score,penalties=score_and_penalties(data['счёт'])
        goals=plain(data[metric]);goals='0' if goals in {'','-','—','–'} else goals
        if mw.parse(data[metric]).filter_templates():raise UpdateError('needs_review','Минуты голов требуют отдельного сохранения')
        item={'дата':old_date(data['дата']),'соперник':data['соперник'],'счёт':score,metric:goals,'соревнование':data['соревнование']}
        for extra in ('место','передачи','минуты','примечание'):
            if data.get(extra):item[extra]=data[extra]
        note=extra_time_note(data['счёт'])
        if note:item['примечание']=(item.get('примечание','')+'; ' if item.get('примечание') else '')+note
        if penalties:item['пенальти']=penalties
        day=validate_row(item,metric)
        if previous and day<previous:unordered=True
        identity=(day,item['соперник'])
        if identity in identities:raise UpdateError('invalid_match','Повторный матч в старой таблице')
        identities.add(identity)
        previous=day
        rows.append(item);sources.append(''.join(refs))
    if not rows:raise UpdateError('needs_review','Нет строк матчей')
    summary,summary_refs=old_summary(text[offset+len(raw):],rows,metric)
    raw+=summary
    remaining=text[:offset]+text[offset+len(raw):]
    if other_blocks(remaining,player,entity):
        raise UpdateError('ambiguous_target','Есть другая возможная статистика сборной; перенос отменён')
    nl='\r\n' if '\r\n' in raw else '\n'
    template=mw.nodes.Template('СбМатчи'+nl)
    def add(key,value):template.add(key,str(value)+nl,preserve_spacing=False)
    add('игрок',player);add('сборная',entity['wikitext'])
    if entity['age']:add('возраст',entity['age'])
    add('заголовок',heading);add('статистика',metric)
    if unordered:add('порядок','исходный')
    add('свёрнуто','да' if 'collapsed' in str(node.attributes).casefold() else 'нет')
    for i,(item,source) in enumerate(zip(rows,sources),1):
        for key,value in item.items():add(key+str(i),value)
        if source:add('источник'+str(i),source)
    if summary_refs:add('источники',''.join(summary_refs))
    rendered=str(template)
    new=next(t for t in WikitextParser(rendered).templates if t.name=='сбматчи')
    _,converted=read_matches(new)
    normalized=[{**r,'пенальти':r.get('пенальти','')} for r in rows]
    if converted!=normalized or references(raw)!=references(rendered):
        raise UpdateError('integrity_error','Перенос изменил матчи или источники')
    fingerprint=digest(json.dumps({'rows':rows,'sources':sources},ensure_ascii=False,sort_keys=True))
    return offset,raw,rendered,entity,len(rows),fingerprint


def operation(text,player,day,age=None):
    found=find_tables(text)
    if not found:return None
    _,raw,rendered,entity,count,rows_sha=parse_table(text,player,age)
    return {'id':'migrate-national-table','type':'migrate_sbmatches','group_id':'national-table-migration',
        'entity':entity,'target':{'structure':'sbmatches','age':entity['age']},'as_of':day,
        'sources':[{'url':'https://ru.wikipedia.org/wiki/Шаблон:СбМатчи','provenance':'user_provided','coverage_as_of':day,
                    'note':'Перенос сохранённых матчей статьи без обновления статистических данных'}],
        'payload':{'expected_native_sha256':digest(raw),'replacement_sha256':digest(rendered),'matches_sha256':rows_sha,'match_count':count}}


def migrate(text,player,op):
    parser=WikitextParser(text);parser.infobox(player)
    existing=[t for t in parser.templates if t.name=='сбматчи' and t.values().get('сборная') and t.values()['сборная'].text.strip()==op['entity']['wikitext'].strip() and (t.values().get('возраст').text.strip() if t.values().get('возраст') else '')==(str(op['target']['age']) if op['target']['age'] else '')]
    if existing:
        if len(existing)==1 and digest(str(existing[0].node))==op['payload']['replacement_sha256']:
            rest=text[:existing[0].start]+text[existing[0].start+len(str(existing[0].node)):]
            if not other_blocks(rest,player,op['entity']):
                read_matches(existing[0]);return text,'already_applied'
        raise UpdateError('ambiguous_target','Нельзя заменить или продублировать существующий СбМатчи')
    offset,raw,rendered,entity,count,rows_sha=parse_table(text,player,op['target']['age'])
    p=op['payload']
    if digest(raw)!=p['expected_native_sha256'] or digest(rendered)!=p['replacement_sha256'] or rows_sha!=p['matches_sha256'] or count!=p['match_count']:
        raise UpdateError('old_value_mismatch','Старая таблица изменилась после подготовки переноса')
    if entity['wikitext']!=op['entity']['wikitext'] or entity['age']!=op['target']['age']:
        raise UpdateError('national_identity_mismatch','Изменилась сборная или возрастная категория')
    return apply_patches(text,[Patch(offset,offset+len(raw),raw,rendered,'national_table_migration')],structural=True),'ready'


def prepare_migration(snapshot,player,day,age=None):
    from .schema_validator import validate_package
    from .change_planner import plan_article
    op=operation(snapshot.text,player,day,age)
    if op is None:raise UpdateError('migration_not_needed','Не найдена старая таблица для переноса')
    op['sources'][0]['url']='https://ru.wikipedia.org/w/index.php?oldid='+str(snapshot.revid)
    article={'title':snapshot.title,'base_revid':snapshot.revid,'player':player,
             'subject_scope':'football_statistics','operations':[op]}
    package={'schema_version':'1.1','package_id':'national-migration-'+str(snapshot.revid),
             'generated_at':day+'T00:00:00Z','articles':[article]}
    validate_package(package)
    return package,plan_article(snapshot,article,'1.1')
