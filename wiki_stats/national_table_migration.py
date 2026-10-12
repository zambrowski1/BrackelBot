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
    caption_code=mw.parse(caption)
    for tag in list(caption_code.filter_tags()):
        if str(tag.tag).strip().casefold()=='ref':caption_code.remove(tag)
    caption=str(caption_code)
    if not re.match(r'^Матчи за сборную ',plain(caption),re.I):
        raise UpdateError('needs_review','Именной заголовок таблицы требует отдельной проверки игрока')
    box=WikitextParser(text).infobox(player)
    field=box.get('национальная сборная')
    candidates={}
    if field:
        for link in field.code.filter_wikilinks():
            title=str(link.title).strip()
            m=re.fullmatch(r'Сборная (.+?) по футболу(?: \(до (\d+) (?:лет|года)\))?',title)
            if not m:continue
            age=int(m[2]) if m[2] else None
            suffix=m[1]+(' до '+str(age)+' '+('года' if age==21 else 'лет') if age else '')
            if re.fullmatch(r'Матчи за сборную '+re.escape(suffix),plain(caption),re.I):
                candidates[title]=(str(link),age)
    if len(candidates)!=1:
        raise UpdateError('national_identity_mismatch','Заголовок таблицы не совпал с единственной сборной в карточке')
    title,(link,age)=next(iter(candidates.items()))
    return {'name':title,'kind':'national_team','wikitext':link,'age':age}


def parse_table(text,player):
    found=find_tables(text)
    if len(found)!=1:raise UpdateError('ambiguous_target','Нужна единственная распознаваемая таблица сборной')
    offset,node=found[0]
    if not isinstance(node,mw.nodes.Tag):raise UpdateError('needs_review','Этот формат таблицы требует ручного переноса')
    raw=str(node)
    if mw.parse(raw).filter_comments():raise UpdateError('needs_review','Комментарии внутри старой таблицы требуют ручного переноса')
    if re.match(r"\s*'''Итого:",text[offset+len(raw):]):
        raise UpdateError('needs_review','Отдельную строку итогов сначала нужно проверить вручную')
    parts=[n for n in node.contents.nodes if not isinstance(n,mw.nodes.Text) or str(n).strip()]
    if len(parts)<3 or any(not isinstance(n,mw.nodes.Tag) for n in parts):raise UpdateError('needs_review','Неизвестная структура таблицы сборной')
    caption=parts[0]
    if str(caption.tag).casefold()!='th':raise UpdateError('needs_review','Не найден заголовок таблицы')
    heading=str(caption.contents).strip()
    entity=team_identity(text,player,heading)
    header=parts[1]
    cells=list(header.contents.nodes) if str(header.tag).casefold()=='tr' else []
    cells=[c for c in cells if not isinstance(c,mw.nodes.Text) or str(c).strip()]
    labels=[plain(str(c.contents)).casefold() if isinstance(c,mw.nodes.Tag) and str(c.tag).casefold()=='th' else '' for c in cells]
    expected=[{'№','номер'},{'дата'},{'оппонент','соперник'},{'счёт','счет'},{'голы','пропущенные голы'},{'соревнование'}]
    if len(labels)!=6 or any(label not in allowed for label,allowed in zip(labels,expected)):
        raise UpdateError('needs_review','Неизвестные столбцы матчей сборной')
    metric=labels[4]
    rows=[]; sources=[]; previous=None; identities=set()
    for number,row in enumerate(parts[2:],1):
        if str(row.tag).casefold()!='tr':raise UpdateError('needs_review','Неизвестная строка таблицы')
        cells=[c for c in row.contents.nodes if not isinstance(c,mw.nodes.Text) or str(c).strip()]
        if len(cells)!=6 or any(not isinstance(c,mw.nodes.Tag) or str(c.tag).casefold()!='td' for c in cells):
            raise UpdateError('needs_review','Нужны шесть ячеек в каждой строке')
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
        if plain(values[0])!=str(number):raise UpdateError('invalid_match','Пропущены или повторены номера матчей')
        if len(rows)>=500:raise UpdateError('needs_review','Слишком большая таблица сборной')
        score=plain(values[3]);goals=plain(values[4])
        goals='0' if goals in {'','-','—','–'} else goals
        item={'дата':old_date(values[1]),'соперник':values[2],'счёт':score,metric:goals,'соревнование':values[5]}
        day=validate_row(item,metric)
        if previous and day<previous:raise UpdateError('invalid_match','Нарушен порядок матчей')
        identity=(day,item['соперник'])
        if identity in identities:raise UpdateError('invalid_match','Повторный матч в старой таблице')
        identities.add(identity)
        previous=day
        rows.append(item);sources.append(''.join(refs))
    if not rows:raise UpdateError('needs_review','Нет строк матчей')
    remaining=text[:offset]+text[offset+len(raw):]
    if possible_national_statistics(remaining,ignore_heading_only=True):
        raise UpdateError('ambiguous_target','Есть другая возможная статистика сборной; перенос отменён')
    nl='\r\n' if '\r\n' in raw else '\n'
    template=mw.nodes.Template('СбМатчи'+nl)
    def add(key,value):template.add(key,str(value)+nl,preserve_spacing=False)
    add('игрок',player);add('сборная',entity['wikitext'])
    if entity['age']:add('возраст',entity['age'])
    add('заголовок',heading);add('статистика',metric)
    add('свёрнуто','да' if 'collapsed' in str(node.attributes).casefold() else 'нет')
    for i,(item,source) in enumerate(zip(rows,sources),1):
        for key,value in item.items():add(key+str(i),value)
        if source:add('источник'+str(i),source)
    rendered=str(template)
    new=next(t for t in WikitextParser(rendered).templates if t.name=='сбматчи')
    _,converted=read_matches(new)
    normalized=[{**r,'пенальти':''} for r in rows]
    if converted!=normalized or references(raw)!=references(rendered):
        raise UpdateError('integrity_error','Перенос изменил матчи или источники')
    fingerprint=digest(json.dumps({'rows':rows,'sources':sources},ensure_ascii=False,sort_keys=True))
    return offset,raw,rendered,entity,len(rows),fingerprint


def operation(text,player,day):
    found=find_tables(text)
    if not found:return None
    if any(t.name=='сбматчи' for t in WikitextParser(text).templates):
        raise UpdateError('ambiguous_target','Есть и СбМатчи, и старая таблица; автоматическое удаление запрещено')
    _,raw,rendered,entity,count,rows_sha=parse_table(text,player)
    return {'id':'migrate-national-table','type':'migrate_sbmatches','group_id':'national-table-migration',
        'entity':entity,'target':{'structure':'sbmatches','age':entity['age']},'as_of':day,
        'sources':[{'url':'https://ru.wikipedia.org/wiki/Шаблон:СбМатчи','provenance':'user_provided','coverage_as_of':day,
                    'note':'Перенос сохранённых матчей статьи без обновления статистических данных'}],
        'payload':{'expected_native_sha256':digest(raw),'replacement_sha256':digest(rendered),'matches_sha256':rows_sha,'match_count':count}}


def migrate(text,player,op):
    parser=WikitextParser(text);parser.infobox(player)
    existing=[t for t in parser.templates if t.name=='сбматчи']
    if existing:
        if len(existing)==1 and not find_tables(text) and digest(str(existing[0].node))==op['payload']['replacement_sha256']:
            rest=text[:existing[0].start]+text[existing[0].start+len(str(existing[0].node)):]
            if not possible_national_statistics(rest,ignore_heading_only=True):
                read_matches(existing[0]);return text,'already_applied'
        raise UpdateError('ambiguous_target','Нельзя заменить или продублировать существующий СбМатчи')
    offset,raw,rendered,entity,count,rows_sha=parse_table(text,player)
    p=op['payload']
    if digest(raw)!=p['expected_native_sha256'] or digest(rendered)!=p['replacement_sha256'] or rows_sha!=p['matches_sha256'] or count!=p['match_count']:
        raise UpdateError('old_value_mismatch','Старая таблица изменилась после подготовки переноса')
    if entity['wikitext']!=op['entity']['wikitext'] or entity['age']!=op['target']['age']:
        raise UpdateError('national_identity_mismatch','Изменилась сборная или возрастная категория')
    return apply_patches(text,[Patch(offset,offset+len(raw),raw,rendered,'national_table_migration')],structural=True),'ready'


def prepare_migration(snapshot,player,day):
    from .schema_validator import validate_package
    from .change_planner import plan_article
    op=operation(snapshot.text,player,day)
    if op is None:raise UpdateError('migration_not_needed','Не найдена старая таблица для переноса')
    op['sources'][0]['url']='https://ru.wikipedia.org/w/index.php?oldid='+str(snapshot.revid)
    article={'title':snapshot.title,'base_revid':snapshot.revid,'player':player,
             'subject_scope':'football_statistics','operations':[op]}
    package={'schema_version':'1.1','package_id':'national-migration-'+str(snapshot.revid),
             'generated_at':day+'T00:00:00Z','articles':[article]}
    validate_package(package)
    return package,plan_article(snapshot,article,'1.1')
