# SPDX-License-Identifier: MIT
"""Read closed German club seasons from identity-bound DFB player lists."""
from datetime import date
import re
from .dfb_evidence import _Document, _Node
from .dfb_youth_profile import cells, summary, played_rows, parse_closed_youth
from .errors import UpdateError

COMPETITIONS = {
    'bundesliga': 'Чемпионат', '2-bundesliga': 'Чемпионат',
    '3-liga': 'Чемпионат', 'regionalliga-nordost': 'Чемпионат',
    'regionalliga-suedwest': 'Чемпионат', 'dfb-pokal': 'Национальный кубок',
    'qualifikation-zur-europa-league': 'Еврокубки',
    'relegation-bundesliga': 'Стыковые матчи',
}


def parse_closed_clubs(mapping, bundle, now):
    if isinstance(bundle,dict) and bundle.get('kind') in {'dfb_full_club_calendar','dfb_full_club_calendar_and_cup'}:
        from .dfb_club_calendar import parse_calendar_club
        return parse_calendar_club(mapping,bundle,now)
    def fail():
        raise UpdateError('dfb_club_unproven', 'Не подтверждён полный клубный сезон DFB')
    try:
        # This source adapter currently requires the complete German youth profile
        # as an independent identity check; goalkeepers are handled elsewhere.
        parse_closed_youth(mapping, bundle, now)
        doc = _Document(); doc.feed(bundle['profile']['html'])
        fragments = {(f['frame_id'], f['url']): f for f in bundle['fragments']}
        if len(fragments) != len(bundle['fragments']): fail()
        slug = bundle['profile']['url'].split('/personen/')[1].split('/')[0]
        prefix = 'https://datencenter.dfb.de/datencenter/personen/' + slug + '/spieler/spiele-in-wettbewerb/'
        current_season = now.year if now.month >= 7 else now.year - 1
        results = []; seen = set()
        for frame in doc.root.nodes():
            if frame.tag != 'turbo-frame' or not frame.attrs.get('src'): continue
            url = frame.attrs['src']
            if not url.startswith(prefix): fail()
            tail = url[len(prefix):]
            if tail.split('/')[0] not in COMPETITIONS: continue
            match = re.fullmatch(r'([a-z0-9-]+)/([a-z0-9-]+)\?season_id=([1-9][0-9]*)#c([1-9][0-9]*)_t([1-9][0-9]*)_s([1-9][0-9]*)', tail)
            if not match: fail()
            competition, team_slug, sid, cid, tid, duplicate_sid = match.groups()
            if sid != duplicate_sid or frame.attrs.get('id') != f'matches_{cid}_{sid}_{tid}': fail()
            if competition not in COMPETITIONS: continue
            listing = frame.parent.parent
            if listing.tag != 'tr' or not listing.has('c-Table-matches-content'): fail()
            siblings = [n for n in listing.parent.children if isinstance(n, _Node)]
            index = siblings.index(listing)
            if index == 0: fail()
            row = siblings[index - 1]
            if not row.has('c-Table-match-table-row-before'): fail()
            values = cells(row)
            if len(values) != 9: fail()
            label = values[0].text().replace('Spiele anzeigen', '').replace('Spiele verbergen', '').strip()
            season = re.fullmatch(r'(.+?)\s+([12][0-9]{3})/([12][0-9]{3})', label)
            if not season: fail()
            team, first, last = season.groups(); first, last = int(first), int(last)
            if last != first + 1: fail()
            if first >= current_season: continue
            numbers = summary(row)
            fragment = fragments.get((frame.attrs['id'], url))
            if fragment is None or not isinstance(fragment['html'], str) or len(fragment['html']) > 2_000_000: fail()
            loaded = _Document(); loaded.feed(fragment['html'])
            frames = [n for n in loaded.root.nodes() if n.tag == 'turbo-frame' and n.attrs.get('id') == frame.attrs['id']]
            if len(frames) != 1: fail()
            games = played_rows(frames[0], team, now)
            if len(games) != numbers['appearances'] or len({g['url'] for g in games}) != len(games): fail()
            def in_season(game):
                day = date.fromisoformat(game['date'])
                if date(first, 7, 1) <= day <= date(last, 6, 30): return True
                # The two official 2019/20 relegation legs were postponed into July.
                exceptional = {'2020-07-02': '2308375', '2020-07-06': '2308376'}
                return (first == 2019 and competition == 'relegation-bundesliga'
                        and game['date'] in exceptional
                        and '/relegation-bundesliga/2019-2020/' in game['url']
                        and game['url'].endswith('-' + exceptional[game['date']]))
            if any(not in_season(g) or date.fromisoformat(g['date']) < date.fromisoformat(mapping['birth_date'])
                   for g in games): fail()
            key = (team_slug, first, competition)
            if key in seen: fail()
            seen.add(key)
            results.append({'team': team, 'team_slug': team_slug, 'season': f'{first}/{str(last)[-2:]}',
                            'source_team_id':int(tid),
                            'category': COMPETITIONS[competition], 'competition_slug': competition,
                            'numbers': numbers, 'complete': True, 'matches': games,
                            'statistics_as_of': max(g['date'] for g in games),
                            'sources': [bundle['profile']['url'], url]})
        if not results: fail()
        return results
    except UpdateError: raise
    except (KeyError, ValueError, TypeError, AttributeError, IndexError): fail()
