# SPDX-License-Identifier: MIT
"""Explicit, revision-bound editing of individual national-team matches."""
from datetime import date
import re
import mwparserfromhell
from .errors import UpdateError
from .models import Patch
from .change_validator import apply_patches
from .wikitext_parser import WikitextParser

EXTRAS = ('место','передачи','минуты','примечание')
FIELDS = ('дата', 'соперник', 'счёт', 'голы', 'соревнование')
INDEXED = (*FIELDS, 'матч', 'источник', 'пенальти', 'пропущенные голы', *EXTRAS)
GENERAL = {'игрок', 'сборная', 'заголовок', 'цвета', 'обновлено', 'свёрнуто', 'источники', 'соревнование', 'возраст', 'статистика', 'порядок'}


def match_date(value):
    try:
        if re.fullmatch(r'(?:\d{2}-\d{2}-\d{4}|\d{2}\.\d{2}\.\d{4})', value):
            value = value[6:] + '-' + value[3:5] + '-' + value[:2]
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            raise ValueError()
        result = date.fromisoformat(value)
        if not 1872 <= result.year <= 2100:
            raise ValueError()
        return result
    except ValueError:
        raise UpdateError('invalid_match', 'Неверная дата матча') from None


def validate_row(row, metric='голы'):
    if any(not row.get(f, '').strip() for f in ('дата', 'соперник', 'счёт', 'соревнование')):
        raise UpdateError('invalid_match', 'Нужны дата, соперник, счёт и соревнование')
    inactive = 'голы' if metric == 'пропущенные голы' else 'пропущенные голы'
    if row.get(inactive, '').strip():
        raise UpdateError('invalid_match', 'Значение в невыбранном столбце ' + inactive)
    day = match_date(row['дата'])
    score = re.fullmatch(r'([0-9]+)\s*:\s*([0-9]+)', row['счёт'])
    raw = row.get(metric, '').strip() or '0'
    if not score or not re.fullmatch(r'[0-9]+', raw):
        raise UpdateError('invalid_match', 'Неверный счёт или ' + metric)
    own, other = map(int, score.groups())
    if max(own, other, int(raw)) > 1000 or int(raw) > (own if metric == 'голы' else other):
        raise UpdateError('invalid_match', 'Значение статистики больше счёта сборной или число слишком велико')
    for extra,limit in [('передачи',own),('минуты',130)]:
        value=row.get(extra,'').strip()
        if value and (not re.fullmatch(r'[0-9]+',value) or int(value)>limit):
            raise UpdateError('invalid_match','Неверное значение '+extra)
    penalties = row.get('пенальти', '')
    if penalties:
        pair = re.fullmatch(r'([0-9]+)\s*:\s*([0-9]+)', penalties)
        if not pair or own != other or int(pair[1]) == int(pair[2]) or max(map(int, pair.groups())) > 1000:
            raise UpdateError('invalid_match', 'Неверная серия пенальти')
    return day


def metric_field(values):
    ref = values.get('статистика')
    metric = ref.text.strip() if ref else 'голы'
    metric = metric or 'голы'
    if metric not in ('голы', 'пропущенные голы'):
        raise UpdateError('unsupported_structure', 'Статистика: голы или пропущенные голы')
    return metric


def normalized_row(row, metric):
    return {**{f: row.get(f, '') for f in ('дата', 'соперник', 'счёт', metric, 'соревнование')},
            'пенальти': row.get('пенальти', ''),
            **{key:row[key] for key in EXTRAS if row.get(key,'').strip()}}


def read_matches(template):
    values = template.values()
    metric = metric_field(values)
    fields = ('дата', 'соперник', 'счёт', metric, 'соревнование')
    indices = set()
    for key, value in values.items():
        if not value.text.strip() or key in GENERAL:
            continue
        found = re.fullmatch(r'(матч|дата|соперник|счёт|голы|соревнование|источник|пенальти|пропущенные голы|место|передачи|минуты|примечание)([1-9][0-9]*)', key)
        if not found or int(found[2]) > 500:
            raise UpdateError('unsupported_structure', 'Неизвестный параметр СбМатчи: ' + key)
        indices.add(int(found[2]))
    if not indices or indices != set(range(1, max(indices) + 1)):
        raise UpdateError('unsupported_structure', 'Пропущены номера матчей СбМатчи')
    ordering=values.get('порядок').text.strip() if values.get('порядок') else ''
    if ordering not in {'','исходный','хронологический'}:raise UpdateError('unsupported_structure','Неизвестный порядок матчей')
    rows, previous, latest = [], None, None
    for index in sorted(indices):
        row = {f: values[f + str(index)].text.strip() if f + str(index) in values else '' for f in (*fields, 'пенальти', 'голы', 'пропущенные голы', *EXTRAS)}
        compact = values.get('матч' + str(index))
        if compact and compact.text.strip():
            if any(row[f] for f in (*fields, 'голы', 'пропущенные голы')):
                raise UpdateError('unsupported_structure', 'Смешаны краткий и подробный форматы матча')
            parts = [p.strip() for p in compact.text.split(';;')]
            if len(parts) not in (4, 5):
                raise UpdateError('unsupported_structure', 'Неверный краткий формат матча')
            row.update(dict(zip(fields, parts)))
        row['соревнование'] = row['соревнование'] or (values['соревнование'].text.strip() if 'соревнование' in values else '')
        day = validate_row(row, metric)
        if ordering!='исходный' and previous and day < previous:
            raise UpdateError('invalid_match', 'Матчи должны идти по датам')
        previous = day
        latest=max(latest,day) if latest else day
        rows.append(normalized_row(row, metric))
    if 'обновлено' in values and values['обновлено'].text.strip() and match_date(values['обновлено'].text.strip()) < latest:
        raise UpdateError('invalid_match', 'Дата обновления раньше последнего матча')
    return values, rows


def execute_match(text, player, op):
    parser = WikitextParser(text)
    parser.infobox(player)
    target = op['target']
    matches = []
    for template in parser.templates:
        if template.name.removeprefix('шаблон:') != 'сбматчи':
            continue
        values = template.values()
        team = values.get('сборная')
        age = values.get('возраст')
        age_text = age.text.strip() if age else ''
        if team and team.text.strip() == op['entity']['wikitext'].strip() and age_text == (str(target['age']) if target['age'] is not None else ''):
            matches.append(template)
    if len(matches) != 1:
        raise UpdateError('ambiguous_target' if matches else 'target_not_found', 'Сборная и возраст не определяют единственный СбМатчи')
    template = matches[0]
    values, rows = read_matches(template)
    metric = metric_field(values)
    fields = ('дата', 'соперник', 'счёт', metric, 'соревнование')
    index = target['match_index']
    new = dict(op['payload']['match'])
    if op['type']!='add_sbmatch' and index<=len(rows):
        for field in EXTRAS:
            if field not in new and field in rows[index-1]:new[field]=rows[index-1][field]
    day = validate_row(new, metric)
    if day > date.fromisoformat(op['as_of']):
        raise UpdateError('source_coverage_mismatch', 'Матч позже даты охвата источника')
    if any(';;' in v or '\n' in v or '\r' in v or '<' in v or '{{' in v or '}}' in v for v in new.values()):
        raise UpdateError('invalid_match', 'Новый матч содержит неподдерживаемую разметку')
    for value in new.values():
        code = mwparserfromhell.parse(value)
        if any(isinstance(node, mwparserfromhell.nodes.Text) and '|' in str(node) for node in code.nodes):
            raise UpdateError('invalid_match', 'Разделитель параметров вне викиссылки запрещён')
    if any(any(c in source['url'] for c in '|{}<>[]\r\n') for source in op['sources']):
        raise UpdateError('invalid_match', 'Источник содержит разделители викитекста')
    adding = op['type'] == 'add_sbmatch'
    desired = normalized_row(new, metric)
    if index <= len(rows) and rows[index - 1] == desired:
        return text, 'already_applied'
    if adding:
        if index != len(rows) + 1:
            raise UpdateError('old_value_mismatch', 'Добавление разрешено только в конец списка')
    else:
        if index > len(rows) or rows[index - 1] != normalized_row(op['payload']['expected_match'], metric):
            raise UpdateError('old_value_mismatch', 'Исходный матч не совпал')
    staged = rows + [desired] if adding else [desired if n == index - 1 else r for n, r in enumerate(rows)]
    dates = [validate_row(r, metric) for r in staged]
    if dates != sorted(dates) and not (values.get('порядок') and values['порядок'].text.strip()=='исходный'):
        raise UpdateError('invalid_match', 'Изменение нарушает порядок дат')
    if adding and any(match_date(r['дата']) == day and r['соперник'] == new['соперник'] for r in rows):
        raise UpdateError('duplicate_match', 'Матч с этой датой и соперником уже есть')
    patches = []
    additions = []
    newline = '\r\n' if '\r\n' in str(template.node) else '\n'
    def replace(key, value):
        ref = values.get(key)
        if ref:
            raw = ref.text
            prefix = raw[:len(raw) - len(raw.lstrip())]
            suffix = raw[len(raw.rstrip()):] if raw.rstrip() else ''
            patches.append(Patch(ref.start, ref.start + len(raw), raw, prefix + value + suffix, key))
        else:
            additions.append('|' + key + '=' + value + newline)
    compact = adding and 'матч' + str(len(rows)) in values or not adding and 'матч' + str(index) in values and values['матч' + str(index)].text.strip()
    if compact:
        replace('матч' + str(index), ' ;; '.join(new.get(f, '') for f in fields))
    else:
        for f in fields:
            replace(f + str(index), new.get(f, ''))
    if new.get('пенальти') or not adding and 'пенальти' + str(index) in values:
        replace('пенальти' + str(index), new.get('пенальти', ''))
    for field in EXTRAS:
        if field in new:replace(field+str(index),new[field])
    if adding:
        replace('источник' + str(index), '[' + op['sources'][0]['url'] + ']')
    if 'обновлено' in values:
        old = values['обновлено'].text.strip()
        updated = max(date.fromisoformat(op['as_of']), match_date(old)) if old else date.fromisoformat(op['as_of'])
        replace('обновлено', updated.strftime('%d-%m-%Y'))
    if additions:
        offset = template.start + len(str(template.node)) - 2
        patches.append(Patch(offset, offset, '', ('' if text[offset - 1:offset] == '\n' else newline) + ''.join(additions), 'sbmatch'))
    result = apply_patches(text, patches, structural=True)
    selected = next(t for t in WikitextParser(result).templates if t.start == template.start)
    read_matches(selected)
    return result, 'ready'
