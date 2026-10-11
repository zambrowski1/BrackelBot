# SPDX-License-Identifier: MIT
"""Explicit source-bound repairs of duplicated rows; no inferred unknown zeros."""
import mwparserfromhell as mw
from .errors import UpdateError
from .models import Patch
from .wikitext_parser import WikitextParser
from .change_validator import apply_patches
from .structural_editor import career_rows, table_layout, recompute_totals


def notes(value):
    code=mw.parse(value)
    return bool(code.filter_comments() or any(str(t.tag).strip().casefold()=='ref' for t in code.filter_tags()))


def stats(value):
    return dict(zip(('appearances','goals'),[n.value for n in value.numeric(pair=True)]))


def repair_duplicates(text, player, op):
    parser=WikitextParser(text);parser.infobox(player)
    if op['type']=='repair_career_duplicates':
        ref,rows=career_rows(parser,player,op['target']['field'])
        if any(p.showkey for p in ref.node.params):
            raise UpdateError('unsupported_structure','Дубликаты карьеры требуют позиционных параметров')
        found=[r for r in rows if r[0].text.strip()==op['target']['period'] and r[1].text.strip()==op['entity']['wikitext'].strip()]
        wanted=op['payload']['stats']
        if len(found)==1 and stats(found[0][2])==wanted:return text,'already_applied'
        if [stats(r[2]) for r in found]!=op['payload']['expected_stats']:
            raise UpdateError('old_value_mismatch','Изменились дублирующиеся строки карьеры')
        if len(found)<2:
            raise UpdateError('ambiguous_target','Нет подтверждённого набора дубликатов')
        patches=[n.patch(wanted[k],k) for k,n in zip(('appearances','goals'),found[0][2].numeric(pair=True)) if n.value!=wanted[k]]
        for row in found[1:]:
            start=row[0].start-1;end=row[2].start+len(row[2].text)
            if text[start]!='|' or notes(text[start:end]):
                raise UpdateError('needs_review','В удаляемой строке есть сноска или комментарий')
            patches.append(Patch(start,end,text[start:end],'','duplicate_career'))
        result=apply_patches(text,patches,structural=True)
        WikitextParser(result).career(op['target']['field'],player)
        return result,'ready'
    table=parser.club_table(allow_duplicates=True)
    layout=table_layout(parser,allow_duplicates=True).get(op['entity']['wikitext'].strip())
    if layout is None:raise UpdateError('target_not_found','Не найден клуб дублирующегося сезона')
    found=[(r,league) for r,league in layout['seasons'] if r.values()['1'].text.strip()==op['season']]
    categories=table.categories;wanted=op['payload']['categories']
    if not set(wanted)<=set(categories):raise UpdateError('invalid_target','Неизвестная графа дублирующегося сезона')
    def row_stats(ref):
        values=ref.values()
        return {c:{'appearances':values[str(2+2*i)].numeric()[0].value,
                   'goals':values[str(3+2*i)].numeric(signed=True)[0].value} for i,c in enumerate(categories)}
    if len(found)==1 and all(row_stats(found[0][0])[c]==v for c,v in wanted.items()):return text,'already_applied'
    old=[row_stats(r) for r,_ in found]
    if old!=op['payload']['expected_rows'] or len(found)<2:
        raise UpdateError('old_value_mismatch','Изменились дублирующиеся сезоны')
    if len({league.start for _,league in found})!=1:
        raise UpdateError('needs_review','Дубликаты относятся к разным блокам лиг')
    for c in set(categories)-set(wanted):
        if any(row[c]!=old[0][c] for row in old):
            raise UpdateError('incomplete_statistics','Разные неподтверждённые значения нельзя объединить')
    patches=[];first=found[0][0].values()
    for i,c in enumerate(categories):
        for j,k in enumerate(('appearances','goals')):
            number=first[str(2+2*i+j)].numeric(signed=bool(j))[0]
            new=wanted.get(c,old[0][c])[k]
            if number.value!=new:patches.append(number.patch(new,k))
    for row,_ in found[1:]:
        raw=str(row.node)
        if notes(raw):raise UpdateError('needs_review','Дубликат содержит сноску или комментарий')
        patches.append(Patch(row.start,row.start+len(raw),raw,'','duplicate_season'))
    removed=len(found)-1
    for header in {h.start:h for h in (layout['club'],found[0][1])}.values():
        number=header.values()['сезоны'].numeric()[0]
        if number.value<=removed:raise UpdateError('unsupported_structure','Неверное число сезонов')
        patches.append(number.patch(number.value-removed,'season_count'))
    result=apply_patches(text,patches,structural=True)
    WikitextParser(result).club_table()
    return recompute_totals(result),'ready'
