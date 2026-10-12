# SPDX-License-Identifier: MIT
"""Insert a full national match list without replacing existing article text."""
from copy import deepcopy
import hashlib,re
from .errors import UpdateError
from .wikitext_parser import WikitextParser
from .sbmatches_repair import repair
from .change_validator import apply_patches
from .models import Patch
from .sbmatches_source import added_profile_references

def create(text,player,op):
    parser=WikitextParser(text);parser.infobox(player)
    payload=op['payload'];metric=payload.get('metric')
    if payload.get('complete') is not True or metric not in {'голы','пропущенные голы'}:
        raise UpdateError('source_scope_mismatch','Новый СбМатчи требует полного списка и явной метрики')
    if any(c in player for c in '|{}<>\r\n'):
        raise UpdateError('invalid_input','Недопустимое имя игрока')
    seed='{{СбМатчи\n|игрок='+player+'\n|сборная='+op['entity']['wikitext']+'\n|статистика='+metric+'\n}}'
    template_op=deepcopy(op)
    template_op['payload']['expected_template_sha256']=hashlib.sha256(seed.encode()).hexdigest()
    rendered,_=repair('{{Футболист|имя='+player+'}}\n'+seed,player,template_op)
    table=str(next(t.node for t in WikitextParser(rendered).templates if t.name=='сбматчи'))
    existing=[t for t in parser.templates if t.name=='сбматчи']
    if existing:
        if len(existing)==1 and str(existing[0].node)==table:return text,'already_applied'
        raise UpdateError('old_value_mismatch','В статье уже есть СбМатчи; создание не заменяет существующую таблицу')
    from .native_national_table import find_tables, replacement_block, possible_national_statistics
    if possible_national_statistics(text):
        raise UpdateError('possible_national_statistics','Возможна существующая статистика сборной — нужна ручная проверка')
    native=find_tables(text)
    if native:
        if not payload.get('expected_native_sha256') or not payload.get('native_team_gen'):
            raise UpdateError('needs_review','В статье уже есть обычная таблица матчей сборной; нужен проверенный перенос')
        block=replacement_block(text,payload['native_team_gen'])
        if hashlib.sha256(block[2].encode()).hexdigest()!=payload['expected_native_sha256']:
            raise UpdateError('old_value_mismatch','Старая таблица матчей сборной изменилась')
        result=apply_patches(text,[Patch(block[0],block[1],block[2],table,'migrate_sbmatches')],structural=True,
                             added_references=added_profile_references(block[2],table,payload,op['as_of']))
        return result,'ready'
    if payload.get('expected_native_sha256'):
        raise UpdateError('old_value_mismatch','Исчезла старая таблица матчей сборной')
    # Prefer an existing national-statistics section. Otherwise insert a
    # dedicated section before references/navigation/categories.
    headings=list(re.finditer(r'(?m)^(={2,6})\s*([^=\r\n]+?)\s*\1\s*$',text))
    selected=[h for h in headings if h[2].strip().casefold() in {'сборная','матчи за сборную','выступления за сборную','статистика выступлений за сборную'}]
    if len(selected)>1:raise UpdateError('ambiguous_target','Несколько разделов статистики сборной')
    if selected:
        h=selected[0]
        end=next((x.start() for x in headings if x.start()>h.start() and len(x[1])<=len(h[1])),len(text))
        prefix=''
    else:
        statistics=[h for h in headings if len(h[1])==2 and h[2].strip().casefold()=='статистика выступлений']
        stops=[h.start() for h in headings if h[2].strip().casefold() in {'достижения','награды','примечания','источники','ссылки','литература'}]
        stops.extend(m.start() for m in re.finditer(r'(?mi)^(?:\[\[Категория:|\{\{(?:Примечания|Навигация))',text))
        if len(statistics)>1:raise UpdateError('ambiguous_target','Несколько разделов статистики выступлений')
        if statistics:
            h=statistics[0]
            end=next((x.start() for x in headings if x.start()>h.start() and len(x[1])<=2),len(text))
            prefix='=== Сборная ===\n'
        else:
            end=min(stops,default=len(text));prefix='== Статистика выступлений ==\n\n=== Сборная ===\n'
    result=apply_patches(text,[Patch(end,end,'','\n'+prefix+table+'\n\n','create_sbmatches')],structural=True,
                         added_references=added_profile_references('',table,payload,op['as_of']))
    return result,'ready'
