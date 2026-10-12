"""Audit four drafts against archived API responses. Never uses the network."""
import hashlib
import json
import re
import sys
import zipfile
from collections import defaultdict
from pathlib import Path

import mwparserfromhell as mw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from wiki_stats.change_validator import arithmetic_warnings, references
from wiki_stats.sbmatches_editor import read_matches

from wiki_stats.wikitext_parser import WikitextParser

BASE = ROOT / 'handoff/2026-10-12/latest'
OUT = ROOT / 'handoff/2026-10-12/saved-data-recheck'
CASES = [
    ('Грифо', 171961, 2538, 2019, 'Винченцо Грифо', '2026-10-11'),
    ('Крамарич', 67647, 2569, 2016, 'Андрей Крамарич', '2026-10-10'),
    ('Центнер', 174345, 2556, 2017, 'Робин Центнер', '2026-09-12'),
    ('Шик', 280609, 2681, 2020, 'Патрик Шик', '2026-10-10'),
]
# Explicit club identities: reserve teams are separate from their parent club.
TEAMS = {
    'Хоффенхайм (футбольный клуб)': 2569, 'Динамо (футбольный клуб, Дрезден)': 2595,
    'Франкфурт (футбольный клуб, Франкфурт-на-Майне)': 5777,
    'Фрайбург (футбольный клуб)': 2538, 'Боруссия (футбольный клуб, Мёнхенгладбах)': 2527,
    'Динамо (футбольный клуб, Загреб)': 2032, 'Риека (футбольный клуб)': 2039,
    'Лестер Сити': 31, 'Локомотива': 2033, 'Майнц 05': 2556,
    'Майнц 05 II': 2571, 'Хольштайн (футбольный клуб)': 2570,
    'Спарта (футбольный клуб, Прага)': 2218, 'Богемианс 1905': 6064,
    'Сампдория': 2711, 'Рома (футбольный клуб)': 2702, 'РБ Лейпциг': 36360,
    'Байер 04': 2681,
}
# Audit classification independent of the collector's package/planner.
LEAGUE = {35, 44, 491, 42, 493, 1079, 1085, 17, 23, 170, 172}
CUP = {217, 19, 307, 328, 96, 329, 21}
EURO = {7, 679, 17015}
OTHER = {799}


def visible(value):
    code = mw.parse(value)
    for t in list(code.filter_templates()):
        name = str(t.name).strip().casefold()
        if name.startswith('флаг '): code.replace(t, '')
        elif name == 'футбол': code.replace(t, str(t.get(1).value))
    return code.strip_code().strip()


def season(value):
    m = re.search(r'(?:20)?(\d{2})/(?:20)?(\d{2})', visible(value))
    if not m: raise ValueError(value)
    return f'20{m[1]}/{m[2]}'


def day(value):
    parts = re.split('[-.]', value)
    return '-'.join(parts if len(parts[0]) == 4 else parts[::-1])


def competition(value):
    label = visible(value)
    if label in ('Товарищеский матч', 'Товарищеский турнир'):
        return 'friendly'
    year = re.search(r'20\d{2}(?:/(?:20)?\d{2})?', label)
    if 'Лига наций' in label and year:
        return 'nations:' + season(year[0])
    if 'Отборочн' in label and year:
        return ('qualifying-wc:' if 'ЧМ' in label else 'qualifying-euro:') + year[0]
    if label.startswith(('ЧЕ-', 'Финальные матчи ЧЕ-')) and year:
        return 'final-euro:' + year[0]
    return label


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(BASE / 'latest-evidence.zip') as archive:
        def read(name): return json.loads(archive.read(name))
        manifest = json.loads((BASE / 'evidence-manifest.json').read_text(encoding='utf-8-sig'))
        for item in manifest['files']:
            data = archive.read(item['path'])
            assert len(data) == item['bytes']
            assert hashlib.sha256(data).hexdigest() == item['sha256'], item['path']
        common = read('bundesliga-three-2026-10-12/cache.json')
        schick = read('schick-sbmatches-api-2026-10-12/seed-cache.json')
        schick.update(read('schick-sbmatches-api-2026-10-12/fresh-cache.json'))
        results = []
        for short, pid, current_team, start, player, updated in CASES:
            draft = BASE / 'articles' / f'{short} — готовая статья.wiki'
            text = draft.read_text(encoding='utf-8')
            parser = WikitextParser(text)
            table = parser.club_table()
            errors, limitations, comparisons = [], [], []
            def check(condition, message):
                if not condition: errors.append(message)
            check(not arithmetic_warnings(text), 'Ошибки арифметики: ' + str(arithmetic_warnings(text)))
            check(text.count('== Статистика выступлений ==') == 1, 'Дублирование раздела статистики')
            achievement = re.search(r'^== Достижения ==', text, re.M)
            check(not achievement or text.index('== Статистика выступлений ==') < achievement.start(), 'Статистика после достижений')
            box = parser.infobox(player)
            check(day(box['обновление данных о клубе'].text.strip()) == updated, 'Дата карточки не совпадает с подтверждённой')
            cache = schick if short == 'Шик' else common
            records = list(cache.values())
            seasons = {}
            for response in records:
                if response.get('player_id') != pid: continue
                for tournament in response.get('tournament_seasons', []):
                    for s in tournament['seasons']:
                        try: seasons[(tournament['tournament']['id'], s['id'])] = season(s['year'])
                        except ValueError: pass
            raw = defaultdict(list)
            for response in records:
                if response.get('player_id') != pid or 'tournament_id' not in response: continue
                tid = response['tournament_id']
                category = 'Чемпионат' if tid in LEAGUE else 'Кубки' if tid in CUP else 'Еврокубки' if tid in EURO else 'Прочие' if tid in OTHER else None
                if not category or not response.get('team'): continue
                label = seasons.get((tid, response['season_id']))
                if not label: continue
                stats = response['statistics']
                goals = stats.get('goals_conceded') if short == 'Центнер' else stats.get('goals')
                if type(stats.get('appearances')) is not int or type(goals) is not int: continue
                raw[(response['team']['id'], label, category, tid)].append((response['type'], stats['appearances'], -goals if short == 'Центнер' else goals))
            expected = defaultdict(lambda: [0, 0])
            for (team, label, category, tid), alternatives in raw.items():
                preferred = 'regularSeason' if category == 'Чемпионат' else 'overall'
                chosen = [r for r in alternatives if r[0] == preferred]
                if not chosen: chosen = alternatives
                pairs = {(r[1], r[2]) for r in chosen}
                if len(pairs) != 1:
                    limitations.append(f'Неоднозначные ответы: {team}, {label}, {tid}')
                    continue
                apps, goals = pairs.pop()
                expected[(team, label, category)][0] += apps
                expected[(team, label, category)][1] += goals
            reviewed_split = None
            if short == 'Грифо':
                # The saved player response labels a combined 23/7 aggregate
                # with Freiburg. Its 23 positive appearances close across two
                # clubs; this is a reviewed correction, not a guessed split.
                matches = {}
                for response in records:
                    if response.get('player_id') != pid: continue
                    for m in response.get('matches', []):
                        if ((m.get('tournament') or {}).get('id'), (m.get('season') or {}).get('id')) != (35, 17597): continue
                        if ((m.get('player') or {}).get('statistics') or {}).get('minutes_played', 0) > 0:
                            matches[m['id']] = m
                grouped = defaultdict(lambda: [0, 0])
                for m in matches.values():
                    p = m['player']; grouped[p['team_id']][0] += 1
                    grouped[p['team_id']][1] += (p.get('incidents') or {}).get('goals', 0)
                aggregate = expected[(2538, '2018/19', 'Чемпионат')]
                check([sum(p[i] for p in grouped.values()) for i in range(2)] == aggregate, 'Разделение Грифо не замыкается на сезонный итог')
                check(dict(grouped) == {2538: [16, 6], 2569: [7, 1]}, 'Разделение Грифо изменилось')
                for team, pair in grouped.items(): expected[(team, '2018/19', 'Чемпионат')] = pair
                reviewed_split = {'aggregate': aggregate, 'club_totals': dict(grouped), 'match_ids': sorted(matches)}
            actual = {}
            for row in table.seasons:
                links = mw.parse(row.club).filter_wikilinks()
                team = TEAMS.get(str(links[0].title).strip()) if links else None
                check(team is not None, 'Неизвестная идентичность клуба: ' + row.club)
                for i, header in enumerate(table.categories):
                    category = visible(header).split('{{')[0]
                    # Footnotes are templates and strip_code removes them.
                    category = next((c for c in ('Чемпионат', 'Кубки', 'Еврокубки', 'Прочие') if category.startswith(c)), category)
                    pair = [row.values[str(2 + i*2)].numeric()[0].value, row.values[str(3 + i*2)].numeric(signed=True)[0].value]
                    actual[(team, season(row.season), category)] = pair
            for key, pair in expected.items():
                if key not in actual:
                    limitations.append(f'В таблице нет отдельной строки для API: {key}, {pair}')
                    continue
                check(actual[key] == pair, f'Расхождение API/таблицы: {key}: {pair} / {actual[key]}')
                comparisons.append({'team_id': key[0], 'season': key[1], 'category': key[2], 'api': pair, 'draft': actual[key]})
            latest = [pair for (team, label, cat), pair in expected.items() if team == current_team and cat == 'Чемпионат' and int(label[:4]) >= start]
            total = [sum(pair[i] for pair in latest) for i in range(2)]
            current = parser.career('клубы', player)[-1][2].text.strip().replace('−', '-')
            check(current == f'{total[0]} ({total[1]})', f'Карточка: {current}, сумма чемпионатов API: {total}')
            nationals = [t for t in parser.templates if t.name == 'сбматчи']
            if short in ('Грифо', 'Шик'):
                package_path = 'schick-ready-2026-10-12/api-package-after-reviewed-correction.json' if short == 'Шик' else f'bundesliga-three-2026-10-12/{pid}/final/package.json'
                op = next(o for o in read(package_path)['articles'][0]['operations'] if 'sbmatch' in o['type'])
                check(len(nationals) == 1, 'Нужен единственный СбМатчи')
                _, rows = read_matches(nationals[0])
                source_rows = op['payload']['matches']
                check(len(rows) == len(source_rows), 'Число матчей сборной отличается от проверенного пакета')
                for index, (a, b) in enumerate(zip(rows, source_rows), 1):
                    for field in ('дата', 'соперник', 'счёт', 'голы', 'соревнование', 'пенальти'):
                        av, bv = a.get(field, ''), b.get(field, '')
                        av, bv = (day(av), day(bv)) if field == 'дата' else (competition(av), competition(bv)) if field == 'соревнование' else (visible(av), visible(bv))
                        check(av == bv, f'Сборная, матч {index}, {field}: {av} / {bv}')
                totals = op['payload']['totals']
                check(len(rows) == totals['appearances'] and sum(int(r['голы']) for r in rows) == totals['goals'], 'Итог СбМатчи не совпадает с пакетом')
                collection_path = 'schick-sbmatches-api-2026-10-12/report.json' if short == 'Шик' else f'bundesliga-three-2026-10-12/{pid}/final/report.json'
                scope = read(collection_path)['collection']['national_scopes'][0]
                source_by_day = {r['date']: r for r in scope['matches']}
                check(len(source_by_day) == len(rows), 'Список сборной не замыкается на сохранённые исходные матчи')
                for index, row in enumerate(rows, 1):
                    source_match = source_by_day.get(day(row['дата']))
                    check(source_match is not None, f'Нет исходного матча сборной {index}')
                    if source_match:
                        check(int(row['голы']) == source_match['numbers']['goals'], f'Голы сборной не совпадают с исходным матчем {index}')
                        check(row['счёт'] == f"{source_match['own_score']}:{source_match['opponent_score']}", f'Счёт сборной не совпадает с исходным матчем {index}')
                national_card = parser.career('национальная сборная', player)[-1][2].text.strip()
                check(national_card == f"{totals['appearances']} ({totals['goals']})", 'Карточка сборной не совпадает с СбМатчи')
                check(day(box['обновление данных о сборной'].text.strip()) == max(day(r['дата']) for r in rows), 'Дата обновления сборной')
            else:
                check(not nationals, 'Добавлен неподтверждённый СбМатчи')
            if short == 'Крамарич':
                national = next(v for v in records if v.get('player_id') == pid and list(v) == ['player_id', 'statistics'])['statistics'][0]
                recovered = read(f'bundesliga-three-2026-10-12/{pid}/national-reconstructed-matches.json')
                pairs = [r['numbers'] for r in recovered.values()]
                confirmed = [sum(p['appearances'] for p in pairs), sum(p['goals'] for p in pairs)]
                check(confirmed == [116, 35], 'Изменился сохранённый итог реконструкции сборной')
                limitations.append(f"Сборная: API {national['appearances']}/{national['goals']}, подтверждённые матчи {confirmed[0]}/{confirmed[1]}; старые данные сохранены, СбМатчи не добавлен")
            if short == 'Центнер':
                limitations.append('Пропущенные голы за Майнц 05 II и Хольштайн по сезонам не подтверждены; показанные нули не означают сухие сезоны. В карточке сохранены старые итоги −39 и −28')
            if short != 'Шик':
                source = read(f'bundesliga-three-2026-10-12/{pid}/final/source-table.json')['payload']
                nulls = sum(pair is None for club in source['clubs'] for row in club['rows'] for pair in row['categories'].values())
                if nulls: limitations.append(f'В пакете {nulls} неизвестных пар категория/сезон; по текущей настройке отображаются нулями, это не подтверждённые нули')
            else:
                review = read('schick-sbmatches-api-2026-10-12/report.json')['collection']['review']
                missing = sum(r['code'] == 'category_not_covered' for r in review)
                if missing: limitations.append(f'{missing} категорий старых сезонов не покрыты API; сохранены прежние показатели статьи')
            before_path = 'schick-ready-2026-10-12/before.wiki' if short == 'Шик' else f'bundesliga-three-2026-10-12/{pid}/before.wiki'
            before = archive.read(before_path).decode('utf-8-sig')
            old = WikitextParser(before)
            old_box = next(t for t in old.templates if t.name == 'футболист').values()
            birthday = lambda values: values['дата рождения'].text if 'дата рождения' in values else None
            check(birthday(box) == birthday(old_box), 'Изменена дата рождения')
            missing_refs = [r for r in references(before) if r not in references(text)]
            check(not missing_refs, 'Утеряны ссылки: ' + str(missing_refs))
            if short in ('Центнер', 'Крамарич'):
                check(box['национальная сборная'].text == old_box['национальная сборная'].text, 'Изменена неподтверждённая сборная в карточке')
            results.append({'player': player, 'draft': str(draft.relative_to(ROOT)), 'errors': errors, 'raw_api_pairs_checked': len(comparisons), 'season_rows': len(table.seasons), 'current_league_total': total, 'comparisons': comparisons, 'limitations': limitations, 'reviewed_transfer_split': reviewed_split, 'national_match_count': len(read_matches(nationals[0])[1]) if nationals else 0})
        report = {'evidence_files_verified': len(manifest['files']), 'football_api_requests': 0, 'wikipedia_edits': 0, 'articles': results}
        (OUT / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({**report, 'articles': [{k: v for k, v in r.items() if k != 'comparisons'} for r in results]}, ensure_ascii=False, indent=2))
        return 1 if any(r['errors'] for r in results) else 0


if __name__ == '__main__':
    sys.exit(main())
