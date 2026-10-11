# SPDX-License-Identifier: MIT
"""Complete U21 draft tables, with shootouts verified from federation protocols."""
from datetime import date, datetime
import hashlib
import re

from .dfb_evidence import _Document, _name
from .entity_resolver import reference_identity
from .errors import UpdateError
from .sbmatches_editor import metric_field
from .test_pages import bound_test_player
from .wikitext_parser import WikitextParser

COUNTRIES = {'slowenien': 'Словения', 'wales': 'Уэльс', 'ungarn': 'Венгрия',
             'niederlande': 'Нидерланды', 'rumanien': 'Румыния',
             'danemark': 'Дания', 'portugal': 'Португалия'}
MONTHS = ['Januar', 'Februar', 'März', 'April', 'Mai', 'Juni', 'Juli',
          'August', 'September', 'Oktober', 'November', 'Dezember']


def shootout_score(row, mapping, protocol):
    def fail():
        raise UpdateError('dfb_youth_score_unproven', 'Не подтверждён игровой счёт и серия пенальти DFB')
    try:
        if (not isinstance(protocol, dict) or protocol.get('url') != row['url']
                or not isinstance(protocol.get('html'), str) or len(protocol['html']) > 2_000_000):
            fail()
        doc = _Document(); doc.feed(protocol['html']); nodes = list(doc.root.nodes())
        for key in ('og:url', 'og:description'):
            if sum(n.tag == 'meta' and n.attrs.get('property') == key for n in nodes) != 1: fail()
        meta = {n.attrs.get('property'): n.attrs.get('content') for n in nodes if n.tag == 'meta'}
        if meta.get('og:url') != row['url']: fail()
        stamp = re.search(r'(\d{1,2})\. (\w+) (\d{4}),', meta.get('og:description', ''))
        if not stamp or stamp[2] not in MONTHS: fail()
        if date(int(stamp[3]), MONTHS.index(stamp[2])+1, int(stamp[1])).isoformat() != row['date']: fail()
        teams = [n.text() for n in nodes if n.attrs.get('class') == 'm-MatchDetails-title']
        if [_name(t) for t in teams] != [_name(row[k]) for k in ('home', 'away')]: fail()
        scores = [n.text() for n in nodes if n.has('m-MatchDetails-score')]
        if len(scores) != 1: fail()
        score = re.fullmatch(r'([0-9]+):([0-9]+)\s+i\.E\.\s*\(([0-9]+):([0-9]+)\)\s*\(([0-9]+):([0-9]+)\)\s*\(([0-9]+):([0-9]+)\)', scores[0])
        if not score: fail()
        nums = [int(v) for v in score.groups()]
        penalties, play = nums[:2], nums[-2:]
        if penalties != row['display_score'] or penalties[0] == penalties[1] or play[0] != play[1]: fail()
        if any(nums[i] > nums[i+2] for i in range(2, 6)) or max(nums) > 1000: fail()
        # Check the two independent event sections: ordinary goals and shootout hits.
        for label, expected in [('Tore', play), ('Elfmeterschießen', penalties)]:
            headings = [n for n in nodes if n.has('m-MatchDetails-history-title') and n.text() == label]
            if len(headings) != 1: fail()
            counted = []
            for side in ('home', 'away'):
                events = [n.text().strip() for n in headings[0].parent.nodes()
                          if n.has('m-MatchDetails-history-event-text--' + side) and n.text().strip()]
                if label == 'Tore':
                    if any(len(re.findall(r'\b[0-9]+:[0-9]+\b', e)) != 1 for e in events): fail()
                    counted.append(len(events))
                else:
                    if any(not re.search(r'\b(?:trifft|vergibt)$', e) for e in events): fail()
                    counted.append(sum(bool(re.search(r'\btrifft$', e)) for e in events))
            if counted != expected: fail()
        own_side = 'home' if _name(row['home']) == 'deutschland u 21' else 'away'
        tables = [n for n in nodes if n.has('m-MatchDetails-lineup-table') and
                  any(t.has('m-MatchDetails-lineup-title') and t.text() == 'Aufstellung' for t in n.nodes())]
        if len(tables) != 1: fail()
        bodies = [n for n in tables[0].nodes() if n.tag == 'tbody']
        if len(bodies) != 1: fail()
        players = [_name(n.text()) for n in bodies[0].nodes()
                   if n.tag == 'td' and n.has('m-MatchDetails-lineup-person--' + own_side) and n.text()]
        if len(players) != 11 or players.count(_name(mapping.get('source_name', ''))) != 1: fail()
        return play, penalties
    except UpdateError: raise
    except (KeyError, TypeError, ValueError, AttributeError): fail()


def full_youth_repair(snapshot, mapping, scope, entity, bundle, wiki):
    if not bound_test_player(snapshot.title, mapping) or scope.get('age') != 21:
        return None
    selected = []
    for template in WikitextParser(snapshot.text).templates:
        if template.name != 'сбматчи': continue
        values = template.values(); age = values.get('возраст'); team = values.get('сборная')
        if not age or age.text.strip() != '21' or not team: continue
        if reference_identity(wiki, team.text.strip()).get('qid') == entity['wikidata_id']:
            selected.append(template)
    if not selected: return None
    if len(selected) != 1:
        raise UpdateError('ambiguous_target', 'Повторный молодёжный СбМатчи')
    template = selected[0]
    if metric_field(template.values()) != 'голы' or scope.get('complete') is not True or scope['numbers']['goals'] != 0:
        raise UpdateError('dfb_youth_goals_unproven', 'Для ненулевых голов нужна индивидуальная сверка протоколов')
    protocols = {}
    for protocol in bundle.get('protocols', []):
        if protocol['url'] in protocols:
            raise UpdateError('dfb_youth_score_unproven', 'Повторный протокол DFB')
        protocols[protocol['url']] = protocol
    finals = {int(m[1]) for row in scope['matches']
              if (m := re.search(r'/europameisterschaft-1136/(20[0-9]{2})-in-', row['url']))}
    if len(finals) != 1:
        raise UpdateError('national_competition_unproven', 'Не подтверждён выпуск молодёжного ЧЕ')
    year = finals.pop(); rows = []; sources = []
    for match in scope['matches']:
        own_home = _name(match['home']) == 'deutschland u 21'
        opponent = _name(match['away'] if own_home else match['home'])
        opponent = re.fullmatch(r'(.+) u 21', opponent)
        if not opponent or opponent[1] not in COUNTRIES:
            raise UpdateError('national_identity_mismatch', 'Не подтверждена возрастная сборная соперника')
        competition = match['competition']; path = match['url'].split('/datencenter/', 1)[1]
        if competition == 'Länderspiel' and path.startswith('laenderspiel-1173/'):
            label = 'Товарищеский матч'
        elif competition == 'Europameisterschaft' and path.startswith(f'europameisterschaft-1136/{year}-in-'):
            label = f'Финальные матчи ЧЕ-{year} (до 21)'
        elif competition == 'EM-Qualifikation' and path.startswith('em-qualifikation-1109/'):
            label = 'Отборочные матчи молодёжного чемпионата Европы'
        else:
            raise UpdateError('national_competition_unproven', 'Не подтверждено молодёжное соревнование')
        score = match['display_score']; penalties = None
        if match['shootout']:
            score, penalties = shootout_score(match, mapping, protocols.get(match['url']))
        if not own_home:
            score = score[::-1]
            if penalties is not None: penalties = penalties[::-1]
        row = {'дата': date.fromisoformat(match['date']).strftime('%d-%m-%Y'),
               'соперник': COUNTRIES[opponent[1]], 'счёт': f'{score[0]}:{score[1]}',
               'голы': '0', 'соревнование': label}
        if penalties is not None: row['пенальти'] = f'{penalties[0]}:{penalties[1]}'
        rows.append(row)
        sources.append({'url': match['url'], 'provenance': 'dfb',
                        'coverage_as_of': scope['statistics_as_of'],
                        'note': 'Полный список выступлений DFB; суммарно ноль голов; пенальти проверены отдельно'})
    if len(rows) != scope['numbers']['appearances']:
        raise UpdateError('source_scope_mismatch', 'Не совпало число молодёжных матчей')
    return {'id': 'dfb-full-youth-sbmatches-21', 'type': 'repair_sbmatches', 'group_id': 'dfb-youth-21',
            'entity': {**entity, 'wikitext': template.values()['сборная'].text.strip()},
            'target': {'structure': 'sbmatches', 'age': 21}, 'as_of': scope['statistics_as_of'],
            'sources': sources, 'payload': {'complete': True, 'totals': scope['numbers'],
                'expected_template_sha256': hashlib.sha256(str(template.node).encode()).hexdigest(),
                'label': mapping['wiki_name'] + ': матчи за сборную Германии (до 21)', 'matches': rows}}
