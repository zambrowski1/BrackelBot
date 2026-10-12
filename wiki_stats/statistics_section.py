# SPDX-License-Identifier: MIT
"""Create a source-covered club table; existing statistics require review."""
import re
from datetime import date
from types import SimpleNamespace
import mwparserfromhell as mw
from mwparserfromhell.nodes import Heading, Wikilink, Tag
from .errors import UpdateError
from .change_validator import arithmetic_warnings, references
from .klstat_editor import numbers, season_key, league_link


def source_reference(payload, as_of):
    source=payload.get('source_url')
    if not source:return ''
    if not re.fullmatch(r'https://www\.sofascore\.com/football/player/(?:[a-z0-9-]+/)?[1-9][0-9]*',source):
        raise UpdateError('invalid_input','Нужна ссылка на профиль футболиста Sofascore')
    return '<ref>{{Cite web|url='+source+'|title=Статистика выступлений футболиста|website=Sofascore|lang=en|access-date='+as_of+'}}</ref>'


def render_section(payload, resolver, as_of):
    from .table_numbers import display,sum_known,row_totals,club_total
    if payload.get('allow_unknown') and payload['full_career']:
        raise UpdateError('incomplete_statistics','Таблица с неизвестными ячейками не заявляет полный итог карьеры')
    categories=payload['categories']
    if not 1 <= len(categories) <= 5 or len(set(categories)) != len(categories):
        raise UpdateError('incomplete_statistics','Нужны 1–5 разных категорий соревнований')
    # Labels may be plain text or a single wikilink, never templates or markup.
    for category in categories:
        code=mw.parse(category)
        if (not category.strip() or any(not isinstance(n,(mw.nodes.Text,Wikilink)) for n in code.nodes)
            or '\n' in category or any(c in code.strip_code() for c in '|{}')):
            raise UpdateError('unsupported_structure','Неподдерживаемый заголовок категории')
    if resolver is None:
        raise UpdateError('entity_unverified','Нужны проверенные ссылки клубов и лиг')
    day=date.fromisoformat(payload['statistics_as_of']) if payload['statistics_as_of'] else None
    if day and day > date.fromisoformat(as_of):
        raise UpdateError('source_coverage_mismatch','Дата статистики позднее даты охвата источников')
    lines=['== Статистика выступлений ==','', '=== Клубная статистика ===']
    if day:lines.append('{{обновлено|'+day.isoformat()+'}}')
    citation=source_reference(payload,as_of)
    if citation:
        if day:lines[-1]='{{обновлено|'+day.isoformat()+'|'+citation+'}}'
        else:lines.append('Данные Sofascore'+citation+'.')
    notes=payload.get('header_notes',{})
    if set(notes)-set(categories):
        raise UpdateError('invalid_input','Пояснение относится к отсутствующей колонке')
    headings=[]
    for category in categories:
        links=notes.get(category,[])
        if not isinstance(links,list) or len(set(links))!=len(links):
            raise UpdateError('invalid_input','Пояснения должны быть списком без повторов')
        for link in links:
            code=mw.parse(link)
            if not link.strip() or '\n' in link or any(not isinstance(n,(mw.nodes.Text,Wikilink)) for n in code.nodes) or any(c in code.strip_code() for c in '|{}<>'):
                raise UpdateError('unsupported_structure','В пояснении допускаются только текст и ссылки')
        headings.append(category+('{{efn|'+', '.join(links)+'}}' if links else ''))
    body=['{{КлСтат|'+'|'.join(headings)+'}}']
    grand=[0]*(2*len(categories)); seen=set(); years=[]; checked_leagues=set()
    for club in payload['clubs']:
        entity=resolver.resolve(club['entity'])
        name=club['entity']['wikitext'].strip()
        if name.replace('«','').replace('»','') != entity.wikitext:
            raise UpdateError('entity_mismatch','Ссылка блока не совпала с проверенным клубом')
        if name in seen or entity.qid in seen:
            raise UpdateError('ambiguous_target','Повторный клуб требует объединения сезонов в один блок')
        seen.update([name,entity.qid])
        rows=club['rows']
        if not rows:
            raise UpdateError('incomplete_statistics','Нельзя создавать пустой блок клуба')
        club_index=len(body)
        body.append('{{КлСтат/Клуб|'+name+'|сезоны='+str(len(rows))+'}}')
        total=[0]*len(grand); last=None; used=set(); parsed=[]
        for row in rows:
            year=season_key(row['season']); years.append(year)
            if row['season'] in used or last is not None and year < last:
                raise UpdateError('unknown_season','Сезоны клуба должны идти по порядку без повторов')
            used.add(row['season']);last=year
            league=league_link(row['league_wikitext'])
            title=str(mw.parse(league).filter_wikilinks()[0].title).strip()
            if title not in checked_leagues:
                resolver.client.get_page_identity(title)
                checked_leagues.add(title)
            values=numbers(SimpleNamespace(categories=categories),{'categories':row['categories'],'complete':True,'allow_unknown':payload.get('allow_unknown',False),'unknown_as_zero':payload.get('unknown_as_zero',False)})
            parsed.append((league,row['season'],values))
            total=[sum_known([a,b]) for a,b in zip(total,values)]
        single_league=len({r[0] for r in parsed})==1
        if single_league:body[club_index]='{{КлСтат/Клуб|'+name+'|'+parsed[0][0]+'|сезоны='+str(len(rows))+'}}'
        i=0
        while i<len(parsed):
            end=i+1
            while end<len(parsed) and parsed[end][0]==parsed[i][0]: end+=1
            if not single_league:body.append('{{КлСтат/Лига|'+parsed[i][0]+'|сезоны='+str(end-i)+'}}')
            for league,season,values in parsed[i:end]:
                body.append('{{КлСтат/Сезон|'+season+'|'+'|'.join(map(display,values))+row_totals(values)+'}}')
            i=end
        body.append(club_total(total))
        grand=[sum_known([a,b]) for a,b in zip(grand,total)]
    if not years:
        raise UpdateError('incomplete_statistics','Нет подтверждённых сезонов')
    # A bounded history omits the career row by default. The all-seasons
    # display option sums the shown numbers, retaining gaps in the report.
    show_total=payload['full_career'] or payload.get('show_career_total',False)
    if any(value is None for value in grand):
        # Native КлСтат/Итого does not forward explicit total overrides;
        # passing an em dash there triggers #expr instead of a valid sum.
        show_total = False
    body.append('{{КлСтат/Итого|'+'|'.join(map(str,grand))+'}}' if show_total else '|}')
    if notes or not show_total:body.append('{{комментарии}}')
    return '\n'.join(lines+body)+'\n\n'


def create_section(text, parser, op, resolver):
    section=render_section(op['payload'],resolver,op['as_of'])
    newline='\r\n' if '\r\n' in text else '\n'
    section=section.replace('\n',newline)
    if section in text:
        if arithmetic_warnings(text):
            raise UpdateError('totals_mismatch','Существующая таблица не прошла проверку')
        return text,'already_applied'
    if any(t.name.startswith('клстат') for t in parser.templates):
        raise UpdateError('statistics_exists','КлСтат уже существует; используйте сравнение и изменение строк')
    from .native_national_table import find_tables
    national_tables={start for start,_ in find_tables(text)}
    has_national = bool(national_tables) or any(t.name=='сбматчи' for t in parser.templates)
    common_section=None
    national_titles={'сборная','матчи за сборную','выступления за сборную','статистика выступлений за сборную'}
    footer=None; offset=0; allowed_raw=[]
    for node in parser.code.nodes:
        value=str(node)
        if isinstance(node,Heading):
            title=str(node.title).strip().casefold()
            if node.level==2 and title=='статистика выступлений' and has_national:
                if common_section is not None:
                    raise UpdateError('ambiguous_target','Несколько разделов статистики выступлений')
                common_section=offset+len(value)
            elif ('статист' in title or 'выступлен' in title) and title not in national_titles:
                raise UpdateError('statistics_exists','В статье уже есть возможный раздел статистики; нужна проверка')
            if node.level==2 and title in national_titles and footer is None:footer=offset
            if node.level==2 and title in {'достижения','награды','примечания','ссылки','литература','источники','см. также'} and footer is None:
                footer=offset
        if isinstance(node,Tag) and str(node.tag).strip().casefold()=='table':
            if offset in national_tables:allowed_raw.append(value)
            else:raise UpdateError('statistics_exists','Найдена обычная таблица с возможной статистикой')
        if isinstance(node,Wikilink) and str(node.title).strip().casefold().startswith('категория:') and footer is None:
            footer=offset
        offset+=len(value)
    other_text=text
    for raw in allowed_raw:other_text=other_text.replace(raw,'',1)
    if '{|' in other_text:
        raise UpdateError('statistics_exists','Найдена обычная викитаблица; создание раздела требует проверки')
    if common_section is not None:
        point=common_section
        section=section.split(newline,1)[1].lstrip('\r\n')
    else:
        point=footer if footer is not None else len(text)
    prefix='' if text[:point].endswith(newline+newline) or point==0 else newline+newline
    # This operation only inserts a validated section. Existing text and refs
    # stay byte-for-byte intact; the new source citation belongs to this section.
    result=text[:point]+prefix+section+text[point:]
    if references(result)!=references(text[:point])+references(section)+references(text[point:]):
        raise UpdateError('integrity_error','Изменились существующие сноски')
    warnings=arithmetic_warnings(result)
    if warnings: raise UpdateError('totals_mismatch','Новая таблица не прошла проверку')
    return result,'ready'
