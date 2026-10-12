# SPDX-License-Identifier: MIT
"""Recognize the observed layouts without confusing goals with appearances."""
import re
import mwparserfromhell as mw
from .errors import UpdateError


def visible(value):
    code=mw.parse(value)
    for tag in list(code.filter_tags()):
        if str(tag.tag).strip().casefold()=='ref':code.remove(tag)
    for template in list(code.filter_templates()):
        if str(template.name).strip().casefold() in {'comment','комментарий','abbr','abbreviation'} and template.has('1'):
            code.replace(template,str(template.get('1').value))
    return re.sub(r'\s+',' ',code.strip_code()).strip()


def table_parts(node):
    parts=[n for n in node.contents.nodes if not isinstance(n,mw.nodes.Text) or str(n).strip()]
    caption=''
    if parts and isinstance(parts[0],mw.nodes.Tag):
        first=parts[0];tag=str(first.tag).casefold()
        if tag=='td' and str(first).lstrip().startswith('|+'):
            caption=str(first.contents).strip().removeprefix('+').strip();parts=parts[1:]
        elif tag in {'caption','th'}:
            caption=str(first.contents).strip();parts=parts[1:]
        elif tag=='tr':
            cells=[n for n in first.contents.nodes if not isinstance(n,mw.nodes.Text) or str(n).strip()]
            if len(cells)==1 and isinstance(cells[0],mw.nodes.Tag) and str(cells[0].tag).casefold()=='th':
                caption=str(cells[0].contents).strip();parts=parts[1:]
    if not parts or not isinstance(parts[0],mw.nodes.Tag) or str(parts[0].tag).casefold()!='tr':
        raise UpdateError('needs_review','Не найдена строка заголовков таблицы')
    headers=[n for n in parts[0].contents.nodes if not isinstance(n,mw.nodes.Text) or str(n).strip()]
    if any(not isinstance(n,mw.nodes.Tag) or str(n.tag).casefold()!='th' for n in headers):
        raise UpdateError('needs_review','Неизвестные заголовки таблицы')
    return caption,headers,parts[1:]


def classify(node,context=''):
    try:caption,headers,_=table_parts(node)
    except UpdateError:return 'unknown'
    labels=[visible(str(h.contents)).casefold().replace('ё','е') for h in headers]
    name=visible(caption).casefold()
    if name.startswith('голы ') or re.search(r'голы за (?:национальную )?сборную\s*[^\n]*$',context.strip(),re.I) or any(x in label for label in labels for x in ['счет после','итоговый счет']):
        return 'goals_list'
    if any(x in labels for x in ['год','сезон']) and not any('дата'==x or 'счет'==x for x in labels) and any(x in labels for x in ['игры','матчи']) and any('гол' in x or x in {'гп','мп'} for x in labels):
        return 'annual_summary'
    if 'дата' in labels and any(x in labels for x in ['соперник','оппонент','противник']) and any('счет' in x for x in labels):
        # A goals catalogue often puts a score after each goal in "Голы".
        if not caption and 'голы' in labels and labels.index('голы')<next((i for i,x in enumerate(labels) if 'счет' in x),999):return 'goals_list'
        return 'match_list'
    return 'unknown'


def columns(headers):
    result=[]
    for header in headers:
        name=visible(str(header.contents)).casefold().replace('ё','е')
        tooltip=str(header.contents).casefold()
        if name in {'№','#','номер','n'}:field='index'
        elif name=='дата':field='дата'
        elif name in {'соперник','оппонент','противник'}:field='соперник'
        elif name=='счет':field='счёт'
        elif name=='голы' or name.startswith('голы '):field='голы'
        elif name in {'пропущено голов','пропущенные голы','пр. голы','пропущено мячей'} or name in {'гп','мп'} and 'пропущ' in tooltip:field='пропущенные голы'
        elif name in {'соревнование','турнир'}:field='соревнование'
        elif name in {'место','место проведения','стадион'}:field='место'
        elif name in {'передачи','голевые передачи'}:field='передачи'
        elif name in {'минуты','мин.'}:field='минуты'
        elif name in {'прим.','примечание','примечания'}:field='примечание'
        else:raise UpdateError('needs_review','Неподдерживаемый столбец: '+name)
        if field in result:raise UpdateError('needs_review','Повторный столбец: '+field)
        result.append(field)
    if not {'index','дата','соперник','счёт','соревнование'}<=set(result) or sum(f in result for f in ['голы','пропущенные голы'])!=1:
        raise UpdateError('needs_review','Таблица не содержит однозначный список матчей')
    return result


def name_forms(word):
    word=word.casefold().replace('ё','е').replace('’',"'").replace('ʼ',"'")
    forms={word}
    irregular={'петр':'петра','лев':'льва','павел':'павла'}
    if word in irregular:forms.add(irregular[word])
    if word.endswith(('ь','й')):forms.add(word[:-1]+'я')
    elif word.endswith('ия'):forms.add(word[:-1]+'и')
    elif word.endswith('а'):forms.update({word[:-1]+'ы',word[:-1]+'и'})
    elif word.endswith('я'):forms.add(word[:-1]+'и')
    elif word and word[-1] not in 'аеёиоуыэюя':forms.add(word+'а')
    return forms


def caption_person(caption,player):
    # Only explicit, narrowly defined name forms; no fuzzy deletion decisions.
    match=re.fullmatch(r'Матчи(?: и (?:пропущенные )?голы)?(?: (.+?))? за (?:национальную )?сборную (.+)',visible(caption),re.I)
    if not match:raise UpdateError('needs_review','Не подтверждён заголовок списка матчей')
    person=match[1]
    if person:
        words=player.split();forms=[name_forms(w) for w in words]
        tokens=[w.casefold().replace('ё','е').replace('’',"'").replace('ʼ',"'") for w in person.split()]
        if not tokens or any(not any(w in group for group in forms) for w in tokens) or not any(w in forms[-1] for w in tokens):
            raise UpdateError('player_mismatch','Именной заголовок относится к другому футболисту')
    return match[2]


def score_and_penalties(value):
    text=visible(value)
    # These are score qualifiers, not another result. The caller preserves
    # the extra-time annotation as a note, including its existing wikilink.
    text=re.sub(r'\s*\((?:д\.в\.|доп\. вр\.|дополнительное время)\)$','',text,flags=re.I)
    before=re.fullmatch(r'(\d+)\s*:\s*(\d+)\s*\(\s*(?:п\.|пен\.?|по пенальти|пенальти)\s*(\d+)\s*:\s*(\d+)\s*\)',text)
    if before:return before[1]+':'+before[2],before[3]+':'+before[4]
    match=re.fullmatch(r'(\d+)\s*:\s*(\d+)(?:\s*\(?\s*(\d+)\s*:\s*(\d+)\s*(?:пен\.?|по пенальти|пенальти)\s*\)?)?',text)
    if not match:raise UpdateError('needs_review','Неизвестная запись счёта или серии пенальти')
    return match[1]+':'+match[2],(match[3]+':'+match[4] if match[3] else '')


def extra_time_note(value):
    match=re.search(r'\(([^\n]+)\)\s*$',value)
    if match and visible(match[1]).casefold() in {'д.в.','доп. вр.','дополнительное время'}:
        return match[1]
    return ''
