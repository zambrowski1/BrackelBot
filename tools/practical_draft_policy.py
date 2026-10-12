"""Apply reviewed evidence and gap-display policy to the four saved drafts.

No network access or Wikipedia edits. Original snapshots remain in the
evidence archive; reviewed federation additions stay in a separate package.
"""
import difflib
import json
import re
import sys
import zipfile
from pathlib import Path
from datetime import date, datetime, timezone

import mwparserfromhell as mw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from wiki_stats.wikitext_parser import WikitextParser
from wiki_stats.competition_labels import category_label
from wiki_stats.change_validator import arithmetic_warnings, references
from wiki_stats.table_numbers import display, sum_known, row_totals, club_total
from wiki_stats.sbmatches_create import create
from wiki_stats.sbmatches_editor import read_matches
from wiki_stats.national_match_format import competition_label
from wiki_stats.sofascore_score import match_score
from tools.recheck_saved_four import TEAMS, season

BASE = ROOT / 'handoff/2026-10-12/latest'
OUT = ROOT / 'handoff/2026-10-12/practical-policy'
TEAMS['Хоффенхайм II'] = 5887
ALIASES = {'Лига': 'Чемпионат', 'Кубок': 'Кубки', 'Кубок страны': 'Кубки', 'Остальные': 'Прочие'}


def category(value):
    label = category_label(value)
    return ALIASES.get(label, label)


def team(label):
    return TEAMS[str(mw.parse(label).filter_wikilinks()[0].title).strip()]


def old_cells(text):
    result = {}; current = None; categories = []
    for template in WikitextParser(text).templates:
        values = template.values()
        if template.name == 'клстат': categories = [category(v.text) for v in values.values()]
        if template.name == 'клстат/клуб': current = team(values['1'].text)
        if template.name == 'клстат/сезон':
            label = season(values['1'].text)
            for i, cat in enumerate(categories):
                for j in range(2):
                    result[current, label, cat, j] = values[str(2+i*2+j)].numeric(signed=bool(j))[0].value
    return result


def partial_table(text, old, known, short):
    parser = WikitextParser(text); table = parser.club_table(); sums = {}; patches = []; current = None
    for template in parser.templates:
        if template.name == 'клстат/клуб':
            current = team(template.values()['1'].text); sums[current] = [0] * (2*len(table.categories))
        elif template.name == 'клстат/сезон':
            values = template.values(); label = season(values['1'].text); rendered = mw.parse(str(template.node)); node = rendered.nodes[0]; numbers = []
            for i, name in enumerate(table.categories):
                cat = category(name)
                for j in range(2):
                    value = values[str(2+i*2+j)].numeric(signed=bool(j))[0].value
                    key = current, label, cat
                    if key not in known and (current, label, cat, j) not in old:
                        if value == 0: value = None
                    # Old reserve goalkeeper rows counted scored goals (zero),
                    # contradicting the card's known concessions. Keep games,
                    # retain card totals, do not invent a seasonal allocation.
                    if short == 'Центнер' and current in (2571, 2570) and cat == 'Чемпионат' and j == 1:
                        value = None
                    node.add(str(2+i*2+j), display(value), preserve_spacing=True)
                    numbers.append(value)
            for j, key in enumerate(('игры', 'голы')):
                wanted = sum_known(numbers[j::2])
                if any(n is None for n in numbers) or node.has(key): node.add(key, display(wanted))
            patches.append((template.start, len(str(template.node)), str(rendered)))
            sums[current] = [sum_known([a, b]) for a, b in zip(sums[current], numbers)]
        elif template.name == 'клстат/итогозаклуб':
            numbers = sums[current]
            if short == 'Центнер' and current in (2571, 2570): numbers[1] = -39 if current == 2571 else -28
            patches.append((template.start, len(str(template.node)), club_total(numbers)))
        elif template.name == 'клстат/итого':
            grand = [sum_known(v[i] for v in sums.values()) for i in range(len(next(iter(sums.values()))))]
            # The native grand-total template cannot sum unknown values.
            replacement = '|}\n{{комментарии}}' if any(n is None for n in grand) else '{{КлСтат/Итого|'+'|'.join(map(str, grand))+'}}'
            patches.append((template.start, len(str(template.node)), replacement))
    for start, length, replacement in sorted(patches, reverse=True): text = text[:start] + replacement + text[start+length:]
    return re.sub(r'(\{\{комментарии\}\}\s*){2,}', '{{комментарии}}\n', text, flags=re.I)


def national_rows(archive):
    cached = json.loads(archive.read('bundesliga-three-2026-10-12/cache.json'))
    recovered = json.loads(archive.read('bundesliga-three-2026-10-12/67647/national-reconstructed-matches.json'))
    countries = json.loads((ROOT/'wiki_stats/national_teams.json').read_text(encoding='utf8'))
    identities = json.loads((OUT/'national-identities.json').read_text(encoding='utf8'))
    pages = {p['title']: p for batch in identities for p in batch['query']['pages']}
    def opponent(name):
        country = countries['aliases'][name]; title = countries['countries'][country]['title']
        assert pages[title].get('pageprops', {}).get('wikibase_item') and not pages[title].get('missing'), title
        return '[['+title+'|'+country+']]'
    matches = {m['id']: m for v in cached.values() if v.get('player_id') == 67647 for m in v.get('matches', []) if m.get('player', {}).get('team_id') == 4715}
    rows = []
    for mid, evidence in recovered.items():
        if not evidence['numbers']['appearances']: continue
        match = matches[int(mid)]; home = match['home_team']['id'] == 4715; score = match_score(match)
        assert score is not None
        own, other = score if home else score[::-1]
        row = {'дата': evidence['date'][:10], 'соперник': opponent(match['away_team' if home else 'home_team']['name']),
               'счёт': f'{own}:{other}', 'голы': str(evidence['numbers']['goals']), 'соревнование': competition_label(match)}
        pens = [(match[side+'_score'].get('periods') or {}).get('penalties') for side in ('home', 'away')]
        if all(type(p) is int for p in pens):
            a, b = pens if home else pens[::-1]
            if own == other: row['пенальти'] = f'{a}:{b}'
            else: row['примечание'] = f'По сумме двух матчей 2:2; серия пенальти {a}:{b}'
        rows.append((row, match['link'], 'sofascore'))
    # Individually reviewed federation records. Do not guess absent games.
    gaps = [('2014-09-04', 'Cyprus', '2:0', 0, 90), ('2015-06-07', 'Gibraltar', '4:0', 1, 45), ('2017-03-28', 'Estonia', '0:3', 0, 90)]
    excerpt_lines = {}
    for path in OUT.glob('hns-page-extract-*.txt'):
        for line in path.read_text(encoding='utf8').splitlines():
            match = re.match(r'L(\d+):\s?(.*)', line)
            if match: excerpt_lines[int(match[1])] = match[2]
    assert excerpt_lines[83].strip() == '#### 119'
    assert excerpt_lines[87].strip() == '#### 36'
    assert '19.06.1991.' in excerpt_lines[76]
    visible = []
    for index, value in sorted(excerpt_lines.items()):
        if not 125 <= index <= 1578: continue
        value = re.sub(r'cite\d+†([^†]*)(?:†[^]*)?', r'\1', value)
        value = re.sub(r'\s+L\d+:.*$', '', value)
        if value.strip() and 'Image:' not in value: visible.append(value.strip())
    for day, name, score, goals, minutes in gaps:
        stamp = date.fromisoformat(day).strftime('%d.%m.%Y.')
        start = next(i for i, line in enumerate(visible) if line.startswith('* '+stamp))
        end = next((i for i in range(start+1, len(visible)) if visible[i].startswith('* ')), len(visible))
        evidence = visible[start+1:end]
        score_index = next(i for i, line in enumerate(evidence) if re.fullmatch(r'\d+:\d+', line))
        raw_score = evidence[score_index]
        expected_name = {'Cyprus': 'Cipar', 'Gibraltar': 'Gibraltar', 'Estonia': 'Estonija'}[name]
        assert expected_name in evidence and 'Prijateljska utakmica' in evidence
        oriented = raw_score if evidence.index('Hrvatska') < score_index else ':'.join(raw_score.split(':')[::-1])
        metrics = [int(line) for line in evidence if re.fullmatch(r'\d+', line)]
        assert oriented == score and metrics[0] == goals and metrics[-1] == minutes
        assert not any(r[0]['дата'] == day for r in rows)
        rows.append(({'дата': day, 'соперник': opponent(name), 'счёт': score, 'голы': str(goals), 'соревнование': 'Товарищеский матч'},
                     'https://hns.team/igraci/14791/andrej-kramaric/', 'user_provided'))
    rows.sort(key=lambda item: item[0]['дата'])
    assert len(rows) == 119 and sum(int(row[0]['голы']) for row in rows) == 36
    return rows, gaps, pages['Сборная Хорватии по футболу']['pageprops']['wikibase_item']


def main():
    OUT.mkdir(exist_ok=True)
    known_report = json.loads((ROOT/'handoff/2026-10-12/saved-data-recheck/report.json').read_text(encoding='utf8'))
    report = []
    with zipfile.ZipFile(BASE/'latest-evidence.zip') as archive:
        for short, pid in [('Грифо', 171961), ('Крамарич', 67647), ('Центнер', 174345), ('Шик', 280609)]:
            file = BASE/'articles'/f'{short} — готовая статья.wiki'; before = file.read_text(encoding='utf8'); after = before
            case = next(r for r in known_report['articles'] if short in r['player'])
            known = {(r['team_id'], r['season'], r['category']) for r in case['comparisons']}
            old = {} if short == 'Грифо' else old_cells(archive.read('schick-ready-2026-10-12/before.wiki' if short == 'Шик' else f'bundesliga-three-2026-10-12/{pid}/before.wiki').decode('utf8'))
            after = partial_table(after, old, known, short)
            if short == 'Грифо' and not any(t.name == 'клстат/клуб' and '[[Хоффенхайм II' in str(t.node) for t in WikitextParser(after).templates):
                parser = WikitextParser(after); first = next(t for t in parser.templates if t.name == 'клстат/итогозаклуб'); point = first.start + len(str(first.node))
                block = '\n{{КлСтат/Клуб|{{Флаг Германии|20px}} {{фарм-клуб}} [[Хоффенхайм II]]|[[Региональная лига «Юго-Запад»]]|сезоны=1}}\n{{КлСтат/Сезон|2013/2014|8|4|—|—|—|—|—|—|игры=—|голы=—}}\n'+club_total([8, 4, None, None, None, None, None, None])+'\n'
                after = after[:point]+block+after[point:]
            if short == 'Крамарич':
                rows, gaps, qid = national_rows(archive); player = 'Андрей Крамарич'
                op = {'id': 'reviewed-kramaric-senior-list', 'type': 'create_sbmatches', 'entity': {'name': 'Хорватия', 'kind': 'national_team', 'age': None, 'wikidata_id': qid, 'page_title': 'Сборная Хорватии по футболу', 'wikitext': '[[Сборная Хорватии по футболу|Хорватия]]'},
                      'target': {'structure': 'sbmatches', 'age': None}, 'as_of': '2026-10-12',
                      'sources': [{'url': url, 'provenance': provenance, 'coverage_as_of': '2026-10-12', 'note': 'Сохранённый протокол Sofascore' if provenance == 'sofascore' else 'Отдельно проверенная строка федерации Хорватии'} for _, url, provenance in rows],
                      'payload': {'complete': True, 'metric': 'голы', 'matches': [r[0] for r in rows], 'label': 'Матчи Андрея Крамарича за сборную Хорватии', 'totals': {'appearances': 119, 'goals': 36}, 'source_url': 'https://www.sofascore.com/football/player/andrej-kramaric/67647', 'source_access_date': '2026-10-12', 'statistics_as_of': rows[-1][0]['дата']}}
                current = [t for t in WikitextParser(after).templates if t.name == 'сбматчи']
                if not current:
                    after, _ = create(after, player, op)
                    parser = WikitextParser(after); box = next(t.node for t in parser.templates if t.name == 'футболист'); old_box = str(box)
                    career = box.get('национальная сборная'); value = str(career.value); value = re.sub(r'\d+\s*\(\s*\d+\s*\)(\s*\}\}\s*)$', r'119 (36)\1', value)
                    career.value = value
                    box.add('обновление данных о сборной', date.fromisoformat(rows[-1][0]['дата']).strftime('%d.%m.%Y'))
                    after = after.replace(old_box, str(box), 1)
                    source = '<ref>{{Cite web|url=https://hns.team/igraci/14791/andrej-kramaric/|title=Andrej Kramarić|website=Хорватский футбольный союз|lang=hr|access-date=2026-10-12}}</ref>'
                    parser = WikitextParser(after); t = next(t for t in parser.templates if t.name == 'сбматчи'); node = mw.parse(str(t.node)).nodes[0]; node.add('источник', str(node.get('источник').value)+source)
                    after = after[:t.start]+str(node)+after[t.start+len(str(t.node)):]
                else:
                    _, existing = read_matches(current[0]); assert len(existing) == 119 and sum(int(r['голы']) for r in existing) == 36
                (OUT/'kramaric-reviewed-national-operation.json').write_text(json.dumps(op, ensure_ascii=False, indent=2), encoding='utf8')
                (OUT/'kramaric-missing-matches.json').write_text(json.dumps({'player_id': 67647, 'api_confirmed': [116,35], 'federation_total': [119,36], 'gaps': gaps, 'source': 'https://hns.team/igraci/14791/andrej-kramaric/'}, ensure_ascii=False, indent=2), encoding='utf8')
            assert not arithmetic_warnings(after), arithmetic_warnings(after)
            assert all(r in references(after) for r in references(before)), short
            assert WikitextParser(after).club_table().seasons
            if before != after:
                (OUT/f'{short}-policy.diff').write_text(''.join(difflib.unified_diff(before.splitlines(True), after.splitlines(True), fromfile=file.name, tofile=file.name)), encoding='utf8')
                file.write_text(after, encoding='utf8')
            report.append({'player': short, 'changed': before != after, 'season_rows': len(WikitextParser(after).club_table().seasons), 'arithmetic_errors': [], 'references_preserved': True})
    (OUT/'draft-replay.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__': main()
