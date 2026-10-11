# SPDX-License-Identifier: MIT
"""Canonicalize a verified redirect and merge separate spells without new data."""
import hashlib
import mwparserfromhell as mw
from .wikitext_parser import WikitextParser, ValueRef
from .change_validator import apply_patches, references
from .models import Patch
from .errors import UpdateError

ALIASES={'клубнаястатистика/лига':'КлСтат/Лига'}


def normalize(text):
    parser=WikitextParser(text)
    headers=[t for t in parser.templates if t.name=='клстат']
    if not headers:return None
    if len(headers)!=1:raise UpdateError('ambiguous_target','Несколько таблиц КлСтат')
    header=headers[0];start=header.start
    refs=[]
    for ref in parser.templates:
        if ref.start<start:continue
        refs.append(ref)
        if ref.name=='клстат/итого':break
    if not refs or refs[-1].name!='клстат/итого':
        # An explicitly closed partial table has no full-career total.
        parser.club_table()
        return None
    clubs=[r.values()['1'].text.strip() for r in refs if r.name=='клстат/клуб']
    if not any(r.name in ALIASES for r in refs) and len(set(clubs))==len(clubs):return None
    for left,right in zip(refs,refs[1:]):
        if text[left.start+len(str(left.node)):right.start].strip():
            raise UpdateError('needs_review','Посторонний текст между строками КлСтат требует сохранения')
    end=refs[-1].start+len(str(refs[-1].node));raw=text[start:end]
    width=2*len(header.values());blocks=[];block=None;aliases=False
    for ref in refs[1:-1]:
        name=ref.name
        if name in ALIASES:
            name=ALIASES[name].casefold();aliases=True
        if name=='клстат/клуб':
            if block is not None:raise UpdateError('unsupported_structure','Не закрыт блок клуба')
            values=ref.values()
            block={'club':values['1'].text.strip(),'nodes':[ref],'league':None,'rows':[]}
            if '2' in values:block['league']=values['2'].text.strip()
        elif name=='клстат/лига':
            if block is None:raise UpdateError('unsupported_structure','Лига вне клуба')
            block['nodes'].append(ref);block['league']=ref.values()['1'].text.strip()
        elif name=='клстат/сезон':
            if block is None or block['league'] is None:raise UpdateError('unsupported_structure','Сезон вне лиги')
            block['nodes'].append(ref);block['rows'].append((block['league'],str(ref.node)))
        elif name=='клстат/итогозаклуб':
            if block is None:raise UpdateError('unsupported_structure','Итог вне клуба')
            block['nodes'].append(ref);block['total']=ref.values()
            # Validate each original spell's width and declared row counts.
            sample='\n'.join([str(header.node)]+[str(r.node) for r in block['nodes']]+['{{КлСтат/Итого|'+'|'.join(['0']*width)+'}}'])
            code=mw.parse(sample)
            for template in code.filter_templates():
                alias=ALIASES.get(str(template.name).strip().casefold())
                if alias:template.name=alias
            WikitextParser(str(code)).club_table()
            blocks.append(block);block=None
        else:raise UpdateError('unsupported_structure','Неизвестный узел КлСтат')
    if block is not None:raise UpdateError('unsupported_structure','Не закрыт последний клуб')
    if not aliases and len({b['club'] for b in blocks})==len(blocks):return None
    # Reordering referenced rows could change named-ref semantics. Refuse it.
    if mw.parse(raw).filter_comments():raise UpdateError('needs_review','Комментарии таблицы требуют сохранения по строкам')
    merged={}
    for block in blocks:
        values=[block['total'][str(i+2)].text.strip() for i in range(width)]
        if block['club'] in merged:
            old=merged[block['club']]
            if any(mw.parse(v).filter_tags() or mw.parse(v).filter_templates() for v in old['values']+values):
                raise UpdateError('needs_review','Примечания итогов отдельных периодов требуют сохранения')
            old['values']=[str(ValueRef(mw.parse(a),0).numeric(signed=True)[0].value+
                ValueRef(mw.parse(b),0).numeric(signed=True)[0].value) for a,b in zip(old['values'],values)]
            old['rows'].extend(block['rows'])
        else:merged[block['club']]={'rows':block['rows'][:],'values':values}
    lines=[str(header.node)]
    for club,data in merged.items():
        lines.append('{{КлСтат/Клуб|'+club+'|сезоны='+str(len(data['rows']))+'}}')
        i=0
        while i<len(data['rows']):
            j=i+1
            while j<len(data['rows']) and data['rows'][j][0]==data['rows'][i][0]:j+=1
            lines.append('{{КлСтат/Лига|'+data['rows'][i][0]+'|сезоны='+str(j-i)+'}}')
            lines.extend(row for _,row in data['rows'][i:j]);i=j
        lines.append('{{КлСтат/ИтогоЗаКлуб|Итого|'+'|'.join(data['values'])+'}}')
    lines.append(str(refs[-1].node))
    new='\n'.join(lines)
    if references(raw)!=references(new):raise UpdateError('needs_review','Перенос изменил бы порядок сносок')
    WikitextParser(new).club_table()
    return start,end,raw,new


def operation(text,player,day):
    change=normalize(text)
    if change is None:return None
    return {'id':'normalize-club-table','type':'normalize_club_table','group_id':'sofascore-reconciliation',
        'entity':{'name':player,'wikitext':'{{КлСтат}}'},'target':{'structure':'club_table'},'as_of':day,
        'sources':[{'url':'https://ru.wikipedia.org/wiki/Шаблон:КлубнаяСтатистика/Лига',
            'provenance':'user_provided','coverage_as_of':day,'note':'Проверенное перенаправление КлСтат/Лига; показатели периодов сохраняются и складываются'}],
        'payload':{'expected_sha256':hashlib.sha256(change[2].encode()).hexdigest()}}


def execute(text,player,op):
    if op['entity']['name']!=player:raise UpdateError('player_mismatch','Таблица другого игрока')
    change=normalize(text)
    if change is None:
        WikitextParser(text).club_table();return text,'already_applied'
    start,end,raw,new=change
    if hashlib.sha256(raw.encode()).hexdigest()!=op['payload']['expected_sha256']:
        raise UpdateError('old_value_mismatch','Таблица изменилась после подготовки переноса')
    if '\r\n' in text:new=new.replace('\n','\r\n')
    return apply_patches(text,[Patch(start,end,raw,new,'normalize_club_table')],structural=True),'ready'
