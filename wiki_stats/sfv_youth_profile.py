# SPDX-License-Identifier: MIT
"""Verify keeper concessions from SFV participation, including full match minutes."""
from datetime import date, datetime
import re
from .errors import UpdateError

TEAMS = {17: 41646, 19: 41644, 20: 41649, 21: 41643}
IDENTITY = 'https://www.football.ch/sfv/nationalteams/a-team/team.aspx/t-41641/p-'
U17_80_SOURCE = 'https://www.uefa.com/under17/news/0252-0ce4410e3907-be54deb2e050-1000--practice-makes-perfect-for-peacock-s-england/'


def parse_keeper_youth(mapping, bundle, now):
    def fail():
        raise UpdateError('sfv_youth_unproven', 'Не подтверждены все минуты и пропущенные голы SFV')
    try:
        if (not isinstance(bundle, dict) or bundle.get('kind') != 'sfv_keeper'
                or type(bundle.get('player_id')) is not int or bundle['player_id'] != mapping['api_id']
                or type(bundle.get('source_player_id')) is not int or bundle['source_player_id'] <= 0): fail()
        pid = bundle['source_player_id']; identity = bundle['identity']
        if identity['url'] != IDENTITY + str(pid) + '/': fail()
        lines = identity['text'].splitlines()
        birth = date.fromisoformat(mapping['birth_date'])
        birthdays = [line for line in lines if line.startswith('Geburtstag\t')]
        positions = [line for line in lines if line.startswith('Position\t')]
        if (lines.count(mapping['source_name']) != 1 or
                birthdays != ['Geburtstag\t' + birth.strftime('%d.%m.%Y')]
                or positions != ['Position\tTor']): fail()
        results = []; ages = set()
        for profile in bundle['profiles']:
            url = re.fullmatch(r'https://www\.football\.ch/sfv/nationalteams/u-([0-9]+)/team\.aspx/t-([0-9]+)/p-([0-9]+)/', profile['url'])
            if not url: fail()
            age, tid, source_pid = map(int, url.groups())
            if (TEAMS.get(age) != tid or source_pid != pid or age in ages
                    or now.year <= date.fromisoformat(mapping['birth_date']).year + age + 2): fail()
            ages.add(age)
            summary = {}
            for key, value in profile['summary']:
                if key in summary: fail()
                summary[key] = value
            expected = {'Einsätze','Spielminuten','Tore','Verwarnungen','Ausschlüsse','Einwechslungen','Auswechslungen'}
            if set(summary) != expected: fail()
            if any(not re.fullmatch(r'[0-9]+', summary[k]) for k in expected - {'Spielminuten'}): fail()
            if not re.fullmatch(r"[0-9]+'", summary['Spielminuten']): fail()
            # Partial appearances, substitutions and red cards require a goal timeline.
            if any(int(summary[k]) != 0 for k in ('Tore','Ausschlüsse','Einwechslungen','Auswechslungen')): fail()
            rows = profile['rows']; games = []; minutes_total = 0; seen = set()
            if not rows or len(rows) % 2: fail()
            for i in range(0, len(rows), 2):
                score, participation = rows[i:i+2]
                if len(score) != 7 or len(participation) != 3: fail()
                day = datetime.strptime(score[0], '%d.%m.%Y').date()
                if day < birth or day > now.date() or day.year > birth.year + age + 2: fail()
                home, away = score[1], score[3]
                if (home == 'SUI') + (away == 'SUI') != 1 or score[2] != '-' or score[5] != ':': fail()
                if any(not re.fullmatch(r'[A-Z]{2,3}', s) for s in (home, away)) or any(not re.fullmatch(r'[0-9]+', score[j]) for j in (4,6)): fail()
                key = (day, home, away)
                if key in seen: fail()
                seen.add(key)
                competition, minutes, role = participation
                if competition not in {'Länderspiel','EM - Qualifikation','EM - Endrunde'} or not re.fullmatch(r"[0-9]+'", minutes): fail()
                minutes = int(minutes[:-1])
                if minutes == 0:
                    if role not in {'?', 'Ersatz (T)', 'Tor'}: fail()
                    continue
                duration = 80 if age == 17 and competition == 'EM - Endrunde' and date(2014,5,9) <= day <= date(2014,5,15) else 90
                if role != 'Tor' or minutes != duration: fail()
                # Finals outside this verified historical group need ET / shootout evidence.
                if competition == 'EM - Endrunde' and duration != 80: fail()
                a, b = int(score[4]), int(score[6])
                if max(a,b) > 1000: fail()
                minutes_total += minutes
                games.append({'date':day.isoformat(),'home':home,'away':away,'minutes':minutes,
                              'own_score':a if home=='SUI' else b,'opponent_score':b if home=='SUI' else a,
                              'competition':competition})
            if not games or len(games) != int(summary['Einsätze']) or minutes_total != int(summary['Spielminuten'][:-1]): fail()
            games.sort(key=lambda r:r['date'])
            results.append({'age':age,'complete':True,'provider':'sfv',
                'numbers':{'appearances':len(games),'goals':-sum(g['opponent_score'] for g in games)},
                'first_date':games[0]['date'],'statistics_as_of':games[-1]['date'],
                'matches':games,'sources':[profile['url']] + ([U17_80_SOURCE] if age==17 else [])})
        if not results: fail()
        return results
    except UpdateError: raise
    except (KeyError, ValueError, TypeError, AttributeError): fail()
