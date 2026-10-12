# SPDX-License-Identifier: MIT
"""Locate an old numbered national-match table for guarded replacement."""
import re
import mwparserfromhell as mw
from .errors import UpdateError


def possible_national_statistics(text, *, ignore_heading_only=False):
    """Conservative duplicate guard, including unfamiliar templates/tables.

    The footballer's infobox and narrative international career are not
    match tables. Everything resembling a national statistics block is.
    """
    code = mw.parse(text)
    for template in code.filter_templates():
        name = str(template.name).strip().casefold().removeprefix('шаблон:')
        if name in {'футболист', 'спортивная карьера'}:
            continue
        if name == 'сбматчи' or ('сборн' in name or name.startswith('сб')) and any(x in name for x in ('матч','стат','игр')):
            return True
        raw = str(template).casefold()
        if name != 'футболист' and 'сборн' in raw and any(x in raw for x in ('счёт','счет','соперник','матч1','матчи','статистик')):
            return True
    for node in code.filter_tags():
        if str(node.tag).casefold() == 'table':
            raw = str(node).casefold()
            if 'сборн' in raw and any(x in raw for x in ('матч','соперник','счёт','счет','игры','голы')):
                return True
    for heading in code.filter_headings():
        title = str(heading.title).strip().casefold()
        if not ignore_heading_only and 'сборн' in title and any(x in title for x in ('статист','матчи','выступлен')):
            return True
    # A table/template may omit the team name and rely on its section heading.
    headings = list(re.finditer(r'(?m)^(={2,6})\s*([^=\r\n]+?)\s*\1\s*$', text))
    for i, heading in enumerate(headings):
        if 'сборн' not in heading[2].casefold():
            continue
        end = next((h.start() for h in headings[i+1:] if len(h[1]) <= len(heading[1])),len(text))
        body = mw.parse(text[heading.end():end])
        if any(str(t.tag).casefold() == 'table' for t in body.filter_tags()):
            return True
        if ignore_heading_only and 'карьер' in heading[2].casefold():
            continue
        for template in body.filter_templates():
            name = str(template.name).strip().casefold()
            if name in {'main','основная статья','см. также','флаг','флагификация','обновлено'} or name.startswith(('cite','флаг ')):
                continue
            if template.params:
                return True
    return False


def find_tables(text):
    offset=0
    result=[]
    for node in mw.parse(text).nodes:
        raw=str(node)
        if isinstance(node,mw.nodes.Tag) and str(node.tag).casefold()=='table':
            visible=node.contents.strip_code()
            if (re.search(r'Матчи[^\r\n]*за сборную',visible) and all(k in visible for k in ('Дата','Счёт','Соревнование'))):
                result.append((offset,node))
        if isinstance(node,mw.nodes.Template) and str(node.name).strip().casefold()=='автонумерация':
            if node.has('Название'):
                caption=str(node.get('Название').value).strip()
                if 'за сборную' in caption:result.append((offset,node))
        offset+=len(raw)
    return result


def replacement_block(text, gen):
    found=find_tables(text)
    if not found:return None
    if len(found)!=1:
        raise UpdateError('ambiguous_target','Несколько старых таблиц матчей сборной')
    offset,node=found[0]
    if isinstance(node,mw.nodes.Template):
        raise UpdateError('needs_review','Автонумерация содержит дополнительные столбцы; исходная таблица сохранена')
    first=str(node.contents).split('|-',1)[0]
    caption=re.search(r'Матчи[^\r\n]*?за сборную ([^\r\n]+)',mw.parse(first).strip_code())
    if not caption or caption[1].strip()!=gen:
        raise UpdateError('national_identity_mismatch','Старая таблица относится к другой сборной')
    if node.contents.filter_comments() or any(str(t.tag).casefold()=='ref' for t in node.contents.filter_tags()):
        raise UpdateError('needs_review','Примечания старой таблицы требуют сохранения по строкам')
    before=text[:offset]
    date=re.search(r'\{\{[Оо]бновлено\|\d{4}-\d{2}-\d{2}\}\}\s*$',before)
    start=date.start() if date else offset
    end=offset+len(str(node))
    summary=re.match(r"\s*'''Итого:[^\r\n]*'''",text[end:])
    if summary:
        value=summary[0]
        if mw.parse(value).filter_templates() or any(str(t.tag).casefold()=='ref' for t in mw.parse(value).filter_tags()):
            raise UpdateError('needs_review','Примечания итога старой таблицы требуют сохранения')
        end+=summary.end()
    return start,end,text[start:end]
