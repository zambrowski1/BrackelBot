# SPDX-License-Identifier: MIT
"""Revision-bound movement of the complete statistics section."""
import hashlib
import re
import mwparserfromhell as mw
from .errors import UpdateError
from .change_validator import apply_patches
from .models import Patch


def normalized(text):
    # Remove only our own obsolete notice, never arbitrary article prose.
    notice = re.search(r"(?m)^''В таблице представлены только подтверждённые сезоны \d{4}—\d{4} годов; полный итог карьеры не заявляется\.''\r?\n", text)
    if notice:
        end = re.search(r'(?m)^==[^=]',text[notice.end():])
        limit = notice.end()+end.start() if end else len(text)
        block = text[notice.end():limit]
        offset = 0
        header = False
        for node in mw.parse(block).nodes:
            if isinstance(node,mw.nodes.Template):
                name = str(node.name).strip().casefold()
                if name == 'клстат':header=True
                if name == 'клстат/итого' and header:
                    point = notice.end()+offset
                    newline='\r\n' if '\r\n' in text else '\n'
                    text=text[:point]+'|}'+newline+'{{комментарии}}'+text[point+len(str(node)):]
                    break
            offset+=len(str(node))
    result = re.sub(r"(?m)^''В таблице представлены только подтверждённые сезоны \d{4}—\d{4} годов; полный итог карьеры не заявляется\.''\r?\n", '', text)
    # Old articles often have a standalone national-statistics section.
    # Keep its contents intact and nest it under the shared statistics title.
    common = re.findall(r'(?mi)^==\s*Статистика выступлений\s*==[ \t]*\r?$',result)
    national = list(re.finditer(r'(?mi)^==\s*(?:Статистика выступлений за сборную|Матчи за сборную|Выступления за сборную)\s*==[ \t]*\r?$',result))
    if not common and len(national)==1:
        h=national[0]
        newline='\r\n' if '\r\n' in result else '\n'
        result=result[:h.start()]+'== Статистика выступлений =='+newline+newline+'=== Сборная ==='+result[h.end():]
    headings = list(re.finditer(r'(?m)^==\s*([^=\r\n]+?)\s*==[ \t]*\r?$', result))
    stats = [h for h in headings if h[1].strip().casefold() == 'статистика выступлений']
    awards = [h for h in headings if h[1].strip().casefold() in {'достижения','награды'}]
    if len(stats) > 1:
        raise UpdateError('ambiguous_target', 'Несколько разделов статистики выступлений')
    if stats and awards and stats[0].start() > awards[0].start():
        start = stats[0].start()
        end = next((h.start() for h in headings if h.start() > start), len(result))
        point = awards[0].start()
        result = result[:point] + result[start:end] + result[point:start] + result[end:]
    return result


def operation(text, player, day):
    if normalized(text) == text:
        return None
    return {'id':'normalize-statistics-layout','type':'normalize_statistics_layout',
        'group_id':'sofascore-reconciliation','entity':{'name':player,'wikitext':'{{Футболист}}'},
        'target':{'structure':'club_table'},'as_of':day,
        'sources':[{'url':'https://ru.wikipedia.org/wiki/Википедия:Оформление_статей',
            'provenance':'user_provided','coverage_as_of':day,'note':'Порядок разделов по заданию владельца; статистические значения не меняются'}],
        'payload':{'expected_sha256':hashlib.sha256(text.encode()).hexdigest()}}


def execute(text, player, op):
    if op['entity']['name'] != player:
        raise UpdateError('player_mismatch','Статья другого игрока')
    new = normalized(text)
    if new == text:
        return text,'already_applied'
    if hashlib.sha256(text.encode()).hexdigest() != op['payload']['expected_sha256']:
        raise UpdateError('old_value_mismatch','Статья изменилась после подготовки порядка разделов')
    return apply_patches(text,[Patch(0,len(text),text,new,'statistics_layout')],structural=True),'ready'
