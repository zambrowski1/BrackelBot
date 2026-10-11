# SPDX-License-Identifier: MIT
"""Convert the recognized club wikitable to the documented KlStat templates.

No source numbers are invented. Every existing category cell and its notes are
retained; totals remain as supplied until a separate arithmetic operation.
"""
import hashlib
import re
import mwparserfromhell as mw
from .errors import UpdateError
from .models import Patch
from .wikitext_parser import WikitextParser, ValueRef
from .change_validator import apply_patches


def tables(text):
    offset=0
    for node in mw.parse(text).nodes:
        if isinstance(node,mw.nodes.Tag) and str(node.tag).casefold()=='table':
            rows=[r for r in node.contents.nodes if isinstance(r,mw.nodes.Tag) and str(r.tag).casefold()=='tr']
            if rows and 'Клуб' in str(rows[0]) and 'Сезон' in str(rows[0]) and 'Еврокубки' in str(rows[0]):
                yield offset,node,rows
        offset+=len(str(node))


def cells(row):
    result=[]
    for cell in row.contents.nodes:
        if isinstance(cell,mw.nodes.Text) and not str(cell).strip():continue
        if not isinstance(cell,mw.nodes.Tag) or str(cell.tag) not in {'td','th'}:
            raise UpdateError('unsupported_structure','Неизвестное оформление строки старой клубной таблицы')
        attrs={str(a.name).strip():str(a.value).strip('"\'') for a in cell.attributes}
        if set(attrs)-{'rowspan','colspan','align','style'}:
            raise UpdateError('unsupported_structure','Неизвестные атрибуты ячейки клубной таблицы')
        value=str(cell.contents).strip()
        span=int(attrs.get('colspan','1'))
        result.append((value,span,attrs))
    return result


def numeric_cells(values, width=10):
    expanded=[]
    for value,span,attrs in values:
        if 'rowspan' in attrs or span not in (1,2):
            raise UpdateError('unsupported_structure','Неизвестное объединение статистических ячеек')
        if span==2:
            if value not in {'—','–','-'}:
                raise UpdateError('unsupported_structure','Объединённая ячейка должна обозначать отсутствие выступлений')
            expanded.extend(['-','-'])
        else:
            parsed = mw.parse(value)
            for tag in reversed(parsed.filter_tags()):
                if str(tag.tag).casefold() in {'b','strong'}:
                    parsed.replace(tag, str(tag.contents))
            value = str(parsed).strip()
            ValueRef(mw.parse(value),0).numeric(signed=True)
            expanded.append(value)
    if len(expanded)!=width:
        raise UpdateError('unsupported_structure','Ширина строки не совпадает с заголовком таблицы')
    return expanded


def convert(node,rows):
    first=cells(rows[0]); second=cells(rows[1])
    def label(value):
        code = mw.parse(value)
        for tag in reversed(code.filter_tags()):
            if str(tag.tag).casefold() in {'ref','br'}:code.remove(tag)
        return re.sub(r'\s+','',code.strip_code())
    labels=[{'Лиги':'Лига','Кубки':'Кубок','Всего':'Итого'}.get(label(v),label(v)) for v,_,_ in first]
    categories = first[2:-1]
    has_league = first[2][1] == 3
    if (labels[:2]!=['Клуб','Сезон'] or labels[-1]!='Итого' or labels[2]!='Лига'
            or not 1<=len(categories)<=5 or first[-1][1]!=2
            or any(span!=2 for _,span,_ in categories[1:])
            or first[2][1] not in (2,3)
            or len(second)!=2*(len(categories)+1)+int(has_league)):
        raise UpdateError('unsupported_structure','Заголовок старой таблицы не соответствует известным турнирам')
    names=[]
    for value,_,_ in categories:
        clean={'Кубки':'Кубок','Лиги':'Лига'}.get(label(value),label(value))
        if clean not in {'Лига','Кубок','КубокГермании','Кубокстраны','Кубоклиги','Еврокубки','Прочие'}:
            raise UpdateError('unsupported_structure','Заголовок: неизвестная категория старой таблицы')
        refs=''.join(str(t) for t in mw.parse(value).filter_tags() if str(t.tag).casefold()=='ref')
        names.append({'Лига':'Чемпионат','Кубокстраны':'Национальный кубок','КубокГермании':'Кубок Германии','Кубоклиги':'Кубок лиги'}.get(clean,clean)+refs)
    width=2*(len(categories)+1); split=width-2
    output=['{{КлСтат|'+'|'.join(names)+'}}']
    block=None; grand=False; blocks=[]; aggregate=None
    def flush():
        if not block or not block['rows'] or block['total'] is None:
            raise UpdateError('unsupported_structure','Не завершён блок клуба')
        output.append('{{КлСтат/Клуб|'+block['club']+'|сезоны='+str(len(block['rows']))+'}}')
        i=0
        while i<len(block['rows']):
            end=i+1
            while end<len(block['rows']) and block['rows'][end][1]==block['rows'][i][1]:end+=1
            output.append('{{КлСтат/Лига|'+block['rows'][i][1]+'|сезоны='+str(end-i)+'}}')
            for season,league,nums in block['rows'][i:end]:
                output.append('{{КлСтат/Сезон|'+season+'|'+'|'.join(nums[:split])+'|игры='+nums[split]+'|голы='+nums[split+1]+'}}')
            i=end
        output.append('{{КлСтат/ИтогоЗаКлуб||'+'|'.join(block['total'][:split])+'}}')
    for row in rows[2:]:
        values=cells(row)
        if not values:continue
        if values[0][0].startswith('Всего за «'):
            nums=numeric_cells(values[1:],width)
            name=mw.parse(values[0][0]).strip_code().strip()[len('Всего за «'):-1]
            spells=[b for b in blocks if mw.parse(b['club']).strip_code().strip()==name]
            if len(spells)<2 or any(sum(ValueRef(mw.parse(b['total'][i]),0).numeric(signed=True)[0].value for b in spells)
                    != ValueRef(mw.parse(nums[i]),0).numeric(signed=True)[0].value for i in range(width)):
                raise UpdateError('integrity_error','Сводный итог периодов клуба не равен их сумме')
            continue
        if 'Всего за карьеру' in mw.parse(values[0][0]).strip_code():
            if block is not None:raise UpdateError('unsupported_structure','Не закрыт последний клуб')
            aggregate=numeric_cells(values[1:],width);grand=True
            continue
        if grand:raise UpdateError('unsupported_structure','Строки после итога карьеры')
        if mw.parse(values[0][0]).strip_code().strip() in {'Итого','Всего'}:
            if block is None:raise UpdateError('unsupported_structure','Итог без клуба')
            block['total']=numeric_cells(values[1:],width);blocks.append(block);block=None;continue
        if 'rowspan' in values[0][2]:
            if block is not None:raise UpdateError('unsupported_structure','Пересекаются блоки клубов')
            block={'club':values.pop(0)[0],'rows':[],'total':None,'league':None,'league_remaining':0}
        if block is None or len(values)<3:
            raise UpdateError('unsupported_structure','Нет клуба или сезона')
        from .season_labels import normalize_table_season
        season=normalize_table_season(values.pop(0)[0])
        league='—'
        if has_league:
            if block['league_remaining']:
                league=block['league'];block['league_remaining']-=1
            else:
                league_cell=values.pop(0);league=league_cell[0]
                block['league']=league
                block['league_remaining']=int(league_cell[2].get('rowspan','1'))-1
        nums=numeric_cells(values,width);block['rows'].append((season,league,nums))
    if not grand:raise UpdateError('unsupported_structure','Нет итога карьеры')
    merged={}
    for item in blocks:
        key=mw.parse(item['club']).strip_code().strip()
        if key in merged:
            previous=merged[key]
            if any(mw.parse(v).filter_tags() or mw.parse(v).filter_templates() for v in previous['total']+item['total']):
                raise UpdateError('needs_review','Примечания итогов отдельных периодов клуба требуют сохранения')
            previous['rows'].extend(item['rows'])
            previous['total']=[str(ValueRef(mw.parse(a),0).numeric(signed=True)[0].value+
                ValueRef(mw.parse(b),0).numeric(signed=True)[0].value) for a,b in zip(previous['total'],item['total'])]
        else:merged[key]=item
    for block in merged.values():flush()
    output.append('{{КлСтат/Итого|'+'|'.join(aggregate[:split])+'}}')
    result='\n'.join(output)
    # Notes, named footnotes and comments are part of the data, not decoration.
    original=mw.parse(str(node));converted=mw.parse(result)
    for extract in (lambda c:[str(x) for x in c.filter_comments()],
                    lambda c:[str(x) for x in c.filter_templates() if str(x.name).strip().casefold()=='efn']):
        if extract(original)!=extract(converted):raise UpdateError('integrity_error','Потеряны примечания старой таблицы')
    return result


def migration_operation(text,player,day):
    if any(t.name=='клстат' for t in WikitextParser(text).templates):return None
    found=list(tables(text))
    if not found:return None
    if len(found)!=1:raise UpdateError('ambiguous_target','Несколько старых клубных таблиц')
    _,node,rows=found[0];converted=convert(node,rows)
    return {'id':'migrate-club-table','type':'migrate_club_table','group_id':'club-table-migration',
        'entity':{'name':player,'wikitext':'{{КлСтат}}'},'target':{'structure':'club_table'},'as_of':day,
        'sources':[{'url':'https://ru.wikipedia.org/wiki/Шаблон:КлСтат','provenance':'user_provided',
                    'coverage_as_of':day,'note':'Перенос существующих ячеек без добавления статистических данных'}],
        'payload':{'expected_sha256':hashlib.sha256(str(node).encode()).hexdigest()}}


def migrate(text,player,op):
    if op['entity']['name']!=player:raise UpdateError('player_mismatch','Таблица относится к другой статье')
    found=list(tables(text))
    if not found and any(t.name=='клстат' for t in WikitextParser(text).templates):
        WikitextParser(text).club_table();return text,'already_applied'
    if len(found)!=1:raise UpdateError('ambiguous_target','Нужна единственная старая клубная таблица')
    offset,node,rows=found[0];raw=str(node)
    if hashlib.sha256(raw.encode()).hexdigest()!=op['payload']['expected_sha256']:
        raise UpdateError('old_value_mismatch','Старая клубная таблица изменилась')
    converted=convert(node,rows)
    if '\r\n' in text:converted=converted.replace('\n','\r\n')
    result=apply_patches(text,[Patch(offset,offset+len(raw),raw,converted,'club_table_migration')],structural=True)
    WikitextParser(result).club_table()
    return result,'ready'
