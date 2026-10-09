# SPDX-License-Identifier: MIT
import re
from datetime import date
from .errors import UpdateError
from .models import Patch
from .wikitext_parser import WikitextParser, only_template
from .statistics_mapper import map_statistics
from .change_validator import apply_patches, arithmetic_warnings


def exact_patch(value, new, field):
    text = value.text
    start = len(text)-len(text.lstrip())
    end = len(text.rstrip())
    end = max(start,end)
    return Patch(value.start+start,value.start+end,text[start:end],new,field)


def is_active_period(period):
    return bool(re.search(r'\{\{(?:н\.в\.|нв)\}\}', period, re.IGNORECASE))


def validate_period(period):
    match = re.fullmatch(r'((?:19|20)[0-9]{2})(?:([—–-])((?:19|20)[0-9]{2}|\{\{(?:н\.в\.|нв)\}\}))?', period, re.IGNORECASE)
    if not match:
        raise UpdateError('unknown_period','Нужны подтверждённые годы: YYYY, YYYY—YYYY или YYYY—{{н.в.}}')
    if match[3] and match[3].isdigit() and int(match[3]) < int(match[1]):
        raise UpdateError('unknown_period','Конечный год раньше начального')
    return int(match[1])


def career_template(parser, player, field):
    box = parser.infobox(player)
    if field not in box:
        raise UpdateError('unsupported_structure','В карточке отсутствует поле карьеры; его создание требует ручного рассмотрения')
    return only_template(box[field],'спортивная карьера')


def career_rows(parser, player, field):
    ref = career_template(parser,player,field)
    values = ref.values()
    # Existing validation covers triples and rejects duplicate positional keys.
    parser.career(field,player)
    return ref, [(values[str(i)],values[str(i+1)],values[str(i+2)]) for i in range(1,len(values)+1,3)]


def select_career(parser, player, op, new_period=None):
    _, rows = career_rows(parser,player,op['target']['field'])
    team = op['entity']['wikitext'].strip()
    period = op['target']['period'].strip()
    matches = [r for r in rows if r[0].text.strip()==period and r[1].text.strip()==team]
    if not matches and new_period:
        matches = [r for r in rows if r[0].text.strip()==new_period and r[1].text.strip()==team]
    if len(matches)!=1:
        raise UpdateError('ambiguous_target' if matches else 'target_not_found','Не найдена единственная строка карьеры по периоду и команде')
    return matches[0]


def recompute_totals(text):
    parser = WikitextParser(text)
    table = parser.club_table()
    width = len(table.categories)*2
    sums = {club:[0]*width for club in table.totals}
    for row in table.seasons:
        for i in range(width):
            sums[row.club][i] += row.values[str(i+2)].numeric()[0].value
    patches = []
    for club, values in table.totals.items():
        for i,value in enumerate(values):
            number = value.numeric()[0]
            if number.value != sums[club][i]:
                patches.append(number.patch(sums[club][i],'club_total'))
    for i,value in enumerate(table.grand_total):
        total = sum(v[i] for v in sums.values())
        number = value.numeric()[0]
        if number.value != total:
            patches.append(number.patch(total,'career_total'))
    return apply_patches(text,patches)


def append_career(text, parser, player, op, resolver):
    if resolver is None:
        raise UpdateError('entity_unverified','Нет проверенного разрешения клуба/сборной')
    entity = resolver.resolve(op['entity'])
    field = 'клубы' if op['type']=='add_club' else 'национальная сборная'
    if op['target'].get('field',field)!=field:
        raise UpdateError('invalid_target','Операция направлена не в то поле карьеры')
    payload = op['payload']
    period = payload['period']
    start = validate_period(period)
    role = payload.get('role','primary')
    if getattr(entity,'team_variant','primary')=='reserve' and role!='reserve':
        raise UpdateError('entity_mismatch','Дубль нельзя добавить как основную команду; нужен role=reserve')
    marker = {'primary':'','loan':'{{аренда}}','reserve':'{{фарм-клуб}}'}[role]
    team = marker+entity.wikitext
    appearances,goals = payload['stats']['appearances'],payload['stats']['goals']
    box = parser.infobox(player)
    if field not in box or not box[field].text.strip():
        if field != 'национальная сборная':
            raise UpdateError('unsupported_structure','Создание клубной карьеры требует ручного рассмотрения')
        newline = '\r\n' if '\r\n' in text else '\n'
        new_value = '{{спортивная карьера'+newline+f'  |{period}|{team}|{appearances} ({goals})'+newline+' }}'
        if field in box:
            value = box[field]
            leading = len(value.text.split('\n',1)[0].split('\r',1)[0])
            point = value.start+leading
            patch = Patch(point,point,'',new_value,'new_national_career')
        else:
            infobox = next(t for t in parser.templates if t.name=='футболист')
            point = infobox.start+len(str(infobox.node))-2
            prefix = '' if text[point-1:point]=='\n' else newline
            patch = Patch(point,point,'',prefix+'| '+field+' = '+new_value+newline,'new_national_career')
        result = apply_patches(text,[patch],structural=True)
        WikitextParser(result).career(field,player)
        return result,'ready'
    ref, rows = career_rows(parser,player,field)
    matches = [r for r in rows if r[0].text.strip()==period and r[1].text.strip()==team]
    if matches:
        if len(matches)!=1:
            raise UpdateError('ambiguous_target','Повторные строки нового клуба/сборной')
        nums = matches[0][2].numeric(pair=True)
        if [n.value for n in nums] == [appearances,goals]:
            return text,'already_applied'
        raise UpdateError('old_value_mismatch','Такая строка уже существует с другими показателями')
    if rows:
        last = rows[-1][0].text.strip()
        if re.match(r'^(19|20)[0-9]{2}',last) and start < int(last[:4]):
            raise UpdateError('unsupported_structure','Добавление исторической строки в середину карьеры требует ручного рассмотрения')
    if op['type']=='add_club' and role=='primary':
        previous = payload.get('previous')
        if not previous:
            raise UpdateError('unknown_period','Для трансфера нужна явно выбранная завершённая строка предыдущего клуба')
        selected = [r for r in rows if r[0].text.strip()==previous['period'] and r[1].text.strip()==previous['wikitext'].strip()]
        if len(selected)!=1 or is_active_period(selected[0][0].text):
            raise UpdateError('previous_period_not_closed','Завершите подтверждённый период прежнего клуба в этой группе')
        validate_period(previous['period'])
        # Other primary active stints cannot silently be closed or ignored.
        active = [r for r in rows if is_active_period(r[0].text) and not any(x in r[1].text.casefold() for x in ['аренда','фарм-клуб'])]
        if active:
            raise UpdateError('previous_period_not_closed','В карточке остался незавершённый основной клуб')
    values = ref.values()
    if not values:
        raise UpdateError('unsupported_structure','Пустая карьера требует ручного рассмотрения')
    tail = values[str(len(values))]
    match = re.search(r'(\r?\n[ \t]*)$',tail.text)
    insertion = tail.start+len(tail.text)-(len(match[1]) if match else 0)
    if '\n' in str(ref.node):
        newline = '\r\n' if '\r\n' in str(ref.node) else '\n'
        indent_match = re.search(r'\n([ \t]*)$',str(ref.node.name))
        indent = indent_match[1] if indent_match else '  '
        prefix = newline+indent
    else:
        prefix = ''
    addition = f'{prefix}|{period}|{team}|{appearances} ({goals})'
    result = apply_patches(text,[Patch(insertion,insertion,'',addition,'new_career_row')],structural=True)
    WikitextParser(result).career(field,player)
    return result,'ready'


def table_layout(parser):
    parser.club_table()
    club = league = None
    layouts = {}
    for ref in parser.templates:
        if ref.name=='клстат/клуб':
            club = ref.values()['1'].text.strip()
            layouts[club] = {'club':ref,'seasons':[]}
        elif ref.name=='клстат/лига' and club:
            league = ref
        elif ref.name=='клстат/сезон' and club:
            layouts[club]['seasons'].append((ref,league))
        elif ref.name=='клстат/итогозаклуб' and club:
            layouts[club]['total'] = ref
            club = None
        elif ref.name=='клстат/итого':
            break
    return layouts


def add_season(text,parser,op):
    table = parser.club_table()
    club = op['entity']['wikitext'].strip()
    payload = op['payload']
    season = op.get('season')
    if not season or not re.fullmatch(r'(19|20)[0-9]{2}(?:/[0-9]{2})?(?: \(аренда\))?',season):
        raise UpdateError('unknown_season','Нужен точный подтверждённый сезон')
    categories = payload['categories']
    if set(categories)!=set(table.categories):
        raise UpdateError('incomplete_statistics','Для нового сезона нужны подтверждённые данные по каждой категории')
    numbers = [categories[c][f] for c in table.categories for f in ['appearances','goals']]
    existing = [r for r in table.seasons if r.club==club and r.season==season]
    if existing:
        row = existing[0]
        actual = [row.values[str(i+2)].numeric()[0].value for i in range(len(numbers))]
        if actual == numbers:
            return text,'already_applied'
        raise UpdateError('old_value_mismatch','Сезон уже существует с другими значениями')
    layouts = table_layout(parser)
    if club not in layouts:
        raise UpdateError('unsupported_structure','Нет блока клуба в таблице; создание нового блока требует ручного рассмотрения')
    layout = layouts[club]
    last,league = layout['seasons'][-1]
    if last.values()['1'].text.strip()!=payload['insert_after_season'] or league.values()['1'].text.strip()!=payload['league_wikitext']:
        raise UpdateError('unsupported_structure','Поддерживается продолжение последней лиги выбранного клуба по явному якорю')
    if int(season[:4]) <= int(payload['insert_after_season'][:4]):
        raise UpdateError('unknown_season','Новый сезон должен следовать за сезоном-якорем')
    if arithmetic_warnings(text):
        raise UpdateError('totals_mismatch','Сначала исправьте исходные итоги отдельной подтверждённой операцией')
    # Clone this article's row style, not a fabricated generic table.
    row = str(last.node)
    from .wikitext_parser import WikitextParser as Parser
    ref = Parser(row).templates[0]
    row_p = ref.values()
    edits = [exact_patch(row_p['1'],season,'season')]
    for i,value in enumerate(numbers):
        # References belong to the prior season and are never copied to a new one.
        if len(row_p[str(i+2)].code.nodes)!=1:
            raise UpdateError('unsupported_structure','Строка-образец со сносками/комментариями не клонируется автоматически')
        edits.append(exact_patch(row_p[str(i+2)],str(value),'new_stat'))
    row = apply_patches(row,edits,structural=True)
    newline = '\r\n' if '\r\n' in text else '\n'
    end = last.start+len(str(last.node))
    patches = [Patch(end,end,'',newline+row,'new_season')]
    for owner in [layout['club'],league]:
        count = owner.values()['сезоны'].numeric()[0]
        patches.append(count.patch(count.value+1,'season_count'))
    result = apply_patches(text,patches,structural=True)
    return recompute_totals(result),'ready'


def execute_operation(text,player,op,resolver=None):
    parser = WikitextParser(text)
    parser.infobox(player)
    kind = op['type']
    if kind in {'add_club','add_national_team'}:
        return append_career(text,parser,player,op,resolver)
    if kind in {'update_stats','add_competition'}:
        if kind=='add_competition' and op['target']['structure']!='club_table':
            raise UpdateError('invalid_target','Соревнование добавляется в существующую категорию таблицы')
        numbers = map_statistics(parser,player,op)
        if all(numbers[f].value==op['new'][f] for f in op['expected']):
            return text,'already_applied'
        if any(numbers[f].value!=v for f,v in op['expected'].items()):
            raise UpdateError('old_value_mismatch','Ожидаемые старые показатели не совпали')
        if op['target']['structure']=='club_table' and arithmetic_warnings(text):
            raise UpdateError('totals_mismatch','Исходные итоги не совпадают; требуется явный update_totals с полными данными')
        patches = [numbers[f].patch(op['new'][f],f) for f in op['new'] if numbers[f].value!=op['new'][f]]
        result = apply_patches(text,patches)
        if op['target']['structure']=='club_table':
            result = recompute_totals(result)
        return result,'ready'
    if kind=='update_career_period':
        period = op['payload']['period']
        validate_period(period)
        row = select_career(parser,player,op,new_period=period)
        if row[0].text.strip()==period:
            return text,'already_applied'
        validate_period(op['target']['period'])
        if int(period[:4])!=int(op['target']['period'][:4]):
            raise UpdateError('unknown_period','Изменение начального года периода требует ручного рассмотрения')
        return apply_patches(text,[exact_patch(row[0],period,'career_period')],structural=True),'ready'
    if kind=='update_current_club':
        if resolver is None:
            raise UpdateError('entity_unverified','Нет проверенного разрешения клуба')
        entity = resolver.resolve(op['entity'])
        value = parser.infobox(player).get('нынешний клуб')
        if value is None:
            raise UpdateError('unsupported_structure','Параметр нынешнего клуба отсутствует')
        if value.text.strip()==entity.wikitext:
            return text,'already_applied'
        if value.text.strip()!=op['expected']:
            raise UpdateError('old_value_mismatch','Текущий клуб не совпал с ожидаемым')
        _,rows = career_rows(parser,player,'клубы')
        if not any(r[1].text.strip() in {entity.wikitext,'{{аренда}}'+entity.wikitext} and is_active_period(r[0].text) for r in rows):
            raise UpdateError('current_club_inconsistent','Добавьте подтверждённую текущую строку клуба в той же группе')
        return apply_patches(text,[exact_patch(value,entity.wikitext,'current_club')],structural=True),'ready'
    if kind=='add_season':
        return add_season(text,parser,op)
    if kind=='update_totals':
        result = recompute_totals(text)
        return result,'ready' if result!=text else 'already_applied'
    if kind=='update_date':
        value = parser.infobox(player).get(op['payload']['field'])
        if value is None:
            raise UpdateError('unsupported_structure','Параметр даты отсутствует')
        new = date.fromisoformat(op['as_of']).strftime('%d.%m.%Y')
        if value.text.strip()==new:
            return text,'already_applied'
        if value.text.strip()!=op['payload']['expected_value']:
            raise UpdateError('old_value_mismatch','Старая дата не совпала')
        return apply_patches(text,[exact_patch(value,new,'coverage_date')],structural=True),'ready'
    raise UpdateError('operation_not_implemented','Неподдерживаемая операция')
