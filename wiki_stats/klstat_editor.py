# SPDX-License-Identifier: MIT
"""Explicit table edits. Missing provider data never authorizes deletion."""
import re
import mwparserfromhell as mw
from .errors import UpdateError
from .models import Patch
from .change_validator import apply_patches, arithmetic_warnings, references
from .structural_editor import table_layout, exact_patch, recompute_totals


def check_table(text):
    from .wikitext_parser import WikitextParser
    table = WikitextParser(text).club_table()
    return {'categories': table.categories, 'rows': [
        {'club': row.club, 'season': row.season, 'categories': {
            category: {'appearances': row.values[str(2+2*i)].numeric()[0].value,
                       'goals': row.values[str(3+2*i)].numeric(signed=True)[0].value}
            for i, category in enumerate(table.categories)}} for row in table.seasons],
        'warnings': arithmetic_warnings(text)}


def same_league_name(left, right):
    aliases=[{'Бундеслига','Чемпионат Германии по футболу'},
             {'Вторая Бундеслига'}, {'Третья лига','Третья лига Германии по футболу'},
             {'Региональная лига «Север»'}, {'Региональная лига «Запад»'}]
    def title(value):
        code=mw.parse(value.strip())
        if len(code.nodes)==1 and isinstance(code.nodes[0],mw.nodes.Wikilink):
            return str(code.nodes[0].title).strip()
        if len(code.nodes)==1 and isinstance(code.nodes[0],mw.nodes.Text):return value.strip()
        return None
    a,b=title(left),title(right)
    return left==right or a is not None and b is not None and any(a in names and b in names for names in aliases)


def numbers(table, payload):
    if payload.get('complete') is not True or set(payload['categories']) != set(table.categories):
        raise UpdateError('incomplete_statistics', 'Нужны подтверждённые данные по всем категориям таблицы')
    values = [None if payload['categories'][c] is None else payload['categories'][c][f] for c in table.categories for f in ('appearances','goals')]
    if all(v is None for v in values):
        raise UpdateError('incomplete_statistics','Нельзя создавать строку без единого показателя')
    if any((v is None and not payload.get('allow_unknown')) or (v is not None and (type(v) is not int or not (-10000 if i % 2 else 0) <= v <= 10000)) for i, v in enumerate(values)):
        raise UpdateError('incomplete_statistics', 'Неизвестные показатели нельзя заменять нулями')
    return values


def season_key(season):
    from .season_labels import normalize_table_season
    season = normalize_table_season(season or '')
    match = re.fullmatch(r'((?:19|20)[0-9]{2})(?:/([0-9]{2}))?(?: \(аренда\))?', season or '')
    if not match or match[2] and int(match[2]) != (int(match[1])+1) % 100:
        raise UpdateError('unknown_season', 'Нужен точный последовательный сезон YYYY или YYYY/YY')
    return int(match[1])


def refresh_confirmed_range(text):
    """Keep the exact draft coverage notice consistent with the actual rows."""
    from .wikitext_parser import WikitextParser
    pattern = r'В таблице представлены только подтверждённые сезоны ((?:19|20)\d{2})—((?:19|20)\d{2}) годов; полный итог карьеры не заявляется\.'
    matches = list(re.finditer(pattern, text))
    if len(matches) != 1:
        return text
    rows = WikitextParser(text).club_table().seasons
    if not rows:
        return text
    start = min(season_key(row.season) for row in rows)
    end = max(season_key(row.season) + int('/' in row.season) for row in rows)
    match = matches[0]
    return text[:match.start(1)] + str(start) + '—' + str(end) + text[match.end(2):]


def league_link(value):
    code = mw.parse(value)
    if len(code.nodes) != 1 or len(code.filter_wikilinks()) != 1 or code.filter_templates():
        raise UpdateError('unsupported_structure', 'Лига должна быть одной явной викиссылкой')
    return value


def clean_deletion(fragment):
    if references(fragment) or mw.parse(fragment).filter_comments():
        raise UpdateError('integrity_error', 'Удаляемая строка содержит сноски или комментарии; нужна ручная обработка')


def row_text(ref, season, values):
    from .table_numbers import display,sum_known,row_totals
    from .wikitext_parser import WikitextParser
    text = str(ref.node)
    params = WikitextParser(text).templates[0].values()
    # References and comments must not migrate from another season.
    if any(len(v.code.nodes) != 1 for v in params.values()):
        return '{{КлСтат/Сезон|'+season+'|'+'|'.join(map(display,values))+row_totals(values)+'}}'
    patches = [exact_patch(params['1'], season, 'season')]
    patches += [exact_patch(params[str(i+2)], display(n), 'stat') for i,n in enumerate(values)]
    for j,key in enumerate(('игры','голы')):
        if key in params:patches.append(exact_patch(params[key],display(sum_known(values[j::2])),'row_total'))
    result=apply_patches(text, patches, structural=True)
    if any(v is None for v in values):
        node=mw.parse(result).filter_templates()[0]
        for j,key in enumerate(('игры','голы')):node.add(key,display(sum_known(values[j::2])))
        result=str(node)
    return result


def insert_season(text, parser, op):
    table = parser.club_table()
    club, payload, season = op['entity']['wikitext'].strip(), op['payload'], op['season']
    key, values = season_key(season), numbers(table, payload)
    league_name = league_link(payload['league_wikitext'])
    layout = table_layout(parser).get(club)
    if not layout:
        raise UpdateError('target_not_found', 'Сначала добавьте блок клуба операцией add_table_club')
    existing = [(r,l) for r,l in layout['seasons'] if r.values()['1'].text.strip() == season]
    if existing:
        row, league = existing[0]
        actual = [row.values()[str(i+2)].numeric(signed=bool(i % 2))[0].value for i in range(len(values))]
        if actual == values and same_league_name(league.values()['1'].text.strip(),league_name):
            return text, 'already_applied'
        raise UpdateError('old_value_mismatch', 'Существующий сезон отличается числом или лигой')
    if arithmetic_warnings(text):
        raise UpdateError('totals_mismatch', 'Сначала исправьте исходные итоги явной операцией update_totals')
    before = 'insert_before_season' in payload
    anchor = payload['insert_before_season'] if before else payload['insert_after_season']
    matches = [(i,r,l) for i,(r,l) in enumerate(layout['seasons']) if r.values()['1'].text.strip() == anchor]
    if len(matches) != 1:
        raise UpdateError('target_not_found', 'Не найден единственный сезон-якорь')
    index, previous, league = matches[0]
    insert_index = index if before else index+1
    prior = layout['seasons'][:insert_index]
    following = layout['seasons'][insert_index:]
    if (prior and key <= season_key(prior[-1][0].values()['1'].text.strip())
        or following and key >= season_key(following[0][0].values()['1'].text.strip())):
        raise UpdateError('unknown_season', 'Новый сезон нарушает хронологию выбранных соседних строк')
    same_league = same_league_name(league.values()['1'].text.strip(), league_name)
    if not same_league and league.start==layout['club'].start:
        raise UpdateError('structure_change_required','Для смены лиги нужно развернуть общий параметр клуба в отдельные блоки лиг')
    if not same_league and ((before and prior and prior[-1][1].start == league.start)
                            or not before and following and following[0][1].start == league.start):
        raise UpdateError('unsupported_structure', 'Новая лига внутри существующего блока требует разделения этого блока')
    newline = '\r\n' if '\r\n' in text else '\n'
    addition = newline + row_text(previous, season, values)
    patches = []
    if same_league and league.start!=layout['club'].start:
        count = league.values()['сезоны'].numeric()[0]
        patches.append(count.patch(count.value+1,'league_seasons'))
    elif not same_league:
        addition = newline + '{{КлСтат/Лига|'+league_name+'|сезоны=1}}' + addition
    count = layout['club'].values()['сезоны'].numeric()[0]
    patches.append(count.patch(count.value+1,'club_seasons'))
    if before:
        end = previous.start if same_league else league.start
        addition = addition[len(newline):] + newline
    else:
        end = previous.start + len(str(previous.node))
        # A note following the anchor belongs to the existing season. Insert
        # after it, rather than moving it onto the newly added row.
        for node in mw.parse(text[end:]).nodes:
            if isinstance(node,mw.nodes.Comment) or isinstance(node,mw.nodes.Tag) and str(node.tag).casefold()=='ref':
                end+=len(str(node));continue
            if isinstance(node,mw.nodes.Text):
                prefix=str(node).split('\n',1)[0]
                if not prefix.strip():end+=len(prefix)
            break
    patches.append(Patch(end,end,'',addition,'new_season'))
    return refresh_confirmed_range(recompute_totals(apply_patches(text,patches,structural=True))), 'ready'


def edit_table(text, parser, op, resolver):
    table = parser.club_table()
    layouts = table_layout(parser)
    club, payload = op['entity']['wikitext'].strip(), op['payload']
    layout = layouts.get(club)
    kind = op['type']
    if kind == 'add_table_club':
        season_key(op['season'])
        values = numbers(table,payload)
        league = league_link(payload['league_wikitext'])
        if layout:
            if len(layout['seasons']) != 1:
                raise UpdateError('old_value_mismatch','Такой блок клуба уже существует')
            return insert_season(text,parser,{**op,'type':'add_season','payload':{**payload,'insert_after_season':op['season']}})
        if arithmetic_warnings(text):
            raise UpdateError('totals_mismatch','Сначала исправьте исходные итоги')
        if resolver is None:
            raise UpdateError('entity_unverified','Нужна проверка ссылки нового клуба')
        entity = resolver.resolve(op['entity'])
        if club.replace('«','').replace('»','') != entity.wikitext:
            raise UpdateError('entity_mismatch','Викиссылка блока не совпала с проверенным клубом')
        before = 'insert_before_club' in payload
        previous = layouts.get(payload['insert_before_club'] if before else payload['insert_after_club'])
        if previous is None:
            raise UpdateError('target_not_found','Не найден блок клуба-якоря')
        newline = '\r\n' if '\r\n' in text else '\n'
        from .table_numbers import display,row_totals,club_total
        stats = '|'.join(map(display,values))
        body = newline.join([
            '{{КлСтат/Клуб|'+club+'|'+league+'|сезоны=1}}',
            '{{КлСтат/Сезон|'+op['season']+'|'+stats+row_totals(values)+'}}',
            club_total(values)])
        addition = body+newline if before else newline+body
        end = previous['club'].start if before else previous['total'].start+len(str(previous['total'].node))
        return refresh_confirmed_range(recompute_totals(apply_patches(text,[Patch(end,end,'',addition,'new_table_club')],structural=True))), 'ready'
    if not layout:
        return text, 'already_applied'
    if arithmetic_warnings(text):
        raise UpdateError('totals_mismatch','Сначала исправьте исходные итоги')
    if kind == 'remove_season':
        matches = [(r,l) for r,l in layout['seasons'] if r.values()['1'].text.strip() == op['season']]
        if not matches:
            return text,'already_applied'
        row, league = matches[0]
        fragment = str(row.node)
        if fragment != payload['expected_wikitext']:
            raise UpdateError('old_value_mismatch','Удаляемая строка изменилась')
        if len(layout['seasons']) == 1:
            raise UpdateError('last_season','Последний сезон удаляется вместе с блоком через remove_table_club')
        clean_deletion(fragment)
        patches = [Patch(row.start,row.start+len(fragment),fragment,'','remove_season')]
        count = layout['club'].values()['сезоны'].numeric()[0]
        patches.append(count.patch(count.value-1,'club_seasons'))
        count = league.values()['сезоны'].numeric()[0]
        if league.start==layout['club'].start:
            pass
        elif count.value == 1:
            clean_deletion(str(league.node))
            patches.append(Patch(league.start,league.start+len(str(league.node)),str(league.node),'','remove_league'))
        else:
            patches.append(count.patch(count.value-1,'league_seasons'))
    else:
        start = layout['club'].start
        end = layout['total'].start+len(str(layout['total'].node))
        fragment = text[start:end]
        if fragment != payload['expected_wikitext']:
            raise UpdateError('old_value_mismatch','Удаляемый блок изменился')
        clean_deletion(fragment)
        if len(layouts) == 1:
            raise UpdateError('last_club','Удаление всей таблицы требует отдельного ручного рассмотрения')
        patches = [Patch(start,end,fragment,'','remove_table_club')]
    return refresh_confirmed_range(recompute_totals(apply_patches(text,patches,structural=True))), 'ready'
