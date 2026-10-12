# SPDX-License-Identifier: MIT
"""Add country flags to every retained club header without rewriting its label."""
from collections import Counter
import mwparserfromhell as mw
from .errors import UpdateError
from .entity_resolver import reference_identity
from .wikitext_parser import WikitextParser
from .models import Patch
from .change_validator import apply_patches


def flagged_label(label,flag):
    if not flag or any(c in flag for c in '|{}[]<>\r\n'):
        raise UpdateError('flag_unverified','Неподдерживаемое название флага')
    code=mw.parse(label)
    markers=[]
    for template in list(code.filter_templates(recursive=False)):
        name=str(template.name).strip().casefold()
        if name.startswith('флаг ') or name in {'флаг','flagicon','флагификация'}:code.remove(template)
        elif name in {'аренда','фарм-клуб'}:
            if template.params:raise UpdateError('needs_review','Сложная отметка аренды или фарм-клуба требует проверки')
            markers.append(str(template));code.remove(template)
    prefix=''.join(markers)+' ' if markers else ''
    return '{{Флаг '+flag+'|20px}} '+prefix+str(code).strip()


def club_headers(text):
    parser=WikitextParser(text)
    headers=[t for t in parser.templates if t.name=='клстат']
    if not headers:return []
    if len(headers)!=1:raise UpdateError('ambiguous_target','Несколько таблиц КлСтат')
    parser.club_table()
    return [t for t in parser.templates if t.name=='клстат/клуб']


def prepare(text,player,day,resolver):
    changes=[];issues=[]
    labels=Counter(t.values()['1'].text.strip() for t in club_headers(text))
    for label,count in labels.items():
        try:
            if resolver is None or not hasattr(resolver,'client'):raise UpdateError('entity_unverified','Нет проверенного разрешения клубов')
            identity=reference_identity(resolver.client,label)
            entity={'name':identity['title'],'kind':'club','page_title':identity['title'],'wikidata_id':identity['qid']}
            resolved=resolver.resolve(entity)
            new=flagged_label(label,resolved.flag)
            if new!=label:changes.append({'expected_wikitext':label,'occurrences':count,'flag':resolved.flag,'entity':entity})
        except UpdateError as exc:issues.append({'code':'club_flag_unproven','club':label,'reason':exc.code,'message':str(exc)})
    if not changes:return None,issues
    op={'id':'normalize-club-flags','type':'normalize_club_flags','group_id':'club-flags',
        'entity':{'name':player,'wikitext':'{{КлСтат}}'},'target':{'structure':'club_table'},'as_of':day,
        'sources':[{'url':'https://www.wikidata.org/wiki/'+c['entity']['wikidata_id'],
           'provenance':'user_provided','coverage_as_of':day,'note':'Страна клуба и шаблон флага проверены через Википедию и Wikidata'} for c in changes],
        'payload':{'clubs':changes}}
    return op,issues


def execute(text,player,op,resolver):
    if op['entity']['name']!=player:raise UpdateError('player_mismatch','Таблица другого игрока')
    if resolver is None or not hasattr(resolver,'client'):raise UpdateError('entity_unverified','Нужна проверка страны клуба')
    parser=WikitextParser(text);parser.infobox(player)
    headers=club_headers(text);patches=[];claimed=set()
    for change in op['payload']['clubs']:
        if change['entity'].get('kind')!='club':raise UpdateError('entity_mismatch','Нужен футбольный клуб')
        old=change['expected_wikitext'];resolved=resolver.resolve(change['entity'])
        if resolved.flag!=change['flag']:raise UpdateError('flag_unverified','Страна клуба изменилась после подготовки diff')
        new=flagged_label(old,resolved.flag)
        matching=[t for t in headers if t.values()['1'].text.strip() in {old,new}]
        if len(matching)!=change['occurrences']:raise UpdateError('old_value_mismatch','Изменились клубы таблицы после подготовки diff')
        for t in matching:
            if t.start in claimed:raise UpdateError('patch_conflict','Повторный клуб в операции флагов')
            claimed.add(t.start);value=t.values()['1']
            identity=reference_identity(resolver.client,value.text)
            if identity.get('qid')!=resolved.qid:raise UpdateError('entity_mismatch','Флаг относится к другому клубу')
            if value.text.strip()!=new:
                raw=value.text;prefix=raw[:len(raw)-len(raw.lstrip())];suffix=raw[len(raw.rstrip()):]
                patches.append(Patch(value.start,value.start+len(raw),raw,prefix+new+suffix,'club_flag'))
    return apply_patches(text,patches,structural=True),'ready' if patches else 'already_applied'
