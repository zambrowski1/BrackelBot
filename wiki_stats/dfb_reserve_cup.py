# SPDX-License-Identifier: MIT
"""National cup absence proved by eligibility and the parent club's entry."""
import hashlib
import re
from datetime import date
from urllib.parse import urljoin, urlsplit
from .dfb_evidence import _Document
from .errors import UpdateError

RULE_URL = 'https://www.dfb.de/fileadmin/_dfbdam/223009-OM_05b_2020_Final.pdf'
RULE_PDF_SHA = '2f8c11493b8383c7899c40b6c7eb6086a8d47e5fe4c91ae2bb86788049296651'
# Extracted pages 5 and 6, visually checked against the official PDF.
RULE_TEXT_SHA = '29f1187019b803a265090c110299f0011da1fa4b9f16148dad73c6da45c8d996'
RESERVES = {
    'energie-cottbus-ii': (546,'Q107818','Energie Cottbus','energie-cottbus','fc-energie-cottbus-e-v'),
    'sc-freiburg-ii': (1641,'Q106394','SC Freiburg','sc-freiburg','sc-freiburg-e-v'),
}


def parent_calendar_season(url, slug):
    parsed=urlsplit(url)
    if parsed.scheme!='https' or parsed.netloc!='datencenter.dfb.de' or parsed.fragment: return None
    for pattern in [r'/dfb-pokal/(20\d{2})-(\d{2}|20\d{2})/mannschaft/',
                    r'/competitions/dfb-pokal/seasons/(20\d{2})-(\d{2}|20\d{2})/teams/']:
        match=re.fullmatch(pattern+re.escape(slug),parsed.path)
        if match:
            first,last=map(int,match.groups())
            if last in {first+1,(first+1)%100}: return first
    return None


def reserve_cup_zero(scope, identity, bundle, now):
    def fail():
        raise UpdateError('dfb_reserve_cup_unproven', 'Не подтверждён допуск резервной команды в национальный кубок')
    try:
        known=RESERVES.get(scope.get('team_slug'))
        if not known: fail()
        team_id,parent_qid,parent_name,parent_slug,parent_club=known
        proof = bundle.get('freiburg_reserve_cup_eligibility' if team_id==1641 else 'reserve_cup_eligibility')
        if proof is None: return None
        if (identity.get('source_entity_id') != f'dfb:{team_id}'
                or identity.get('parent_wikidata_id') != parent_qid
                or type(scope.get('source_team_id')) is not int or scope['source_team_id'] != team_id): fail()
        if team_id==1641 and identity.get('wikidata_id')!='Q20631356': fail()
        season = re.fullmatch(r'(20\d{2})/(\d{2})', scope['season'])
        if not season: fail()
        first, last = int(season[1]), int(season[2])
        if not 2009 <= first <= 2019 or last != (first+1)%100: fail()
        end = date(first+1, 6, 30)
        if end >= now.date(): fail()
        if isinstance(proof,list):
            if not proof: return None
            seasons=[parent_calendar_season(p['parent_cup']['url'],parent_slug) for p in proof]
            if None in seasons or len(set(seasons))!=len(seasons): fail()
            selected=[p for p,year in zip(proof,seasons) if year==first]
            if not selected: return None
            proof=selected[0]
        rule = proof['rule']
        if (rule['url'] != RULE_URL or rule['pdf_sha256'] != RULE_PDF_SHA
                or rule['pages'] != [5,6] or not isinstance(rule['text'], str)
                or hashlib.sha256(rule['text'].encode()).hexdigest() != RULE_TEXT_SHA): fail()
        parent = proof['parent_cup']
        url=parent['url']
        if (parent_calendar_season(url,parent_slug)!=first or not isinstance(parent['html'], str)
                or len(parent['html']) > 2_000_000): fail()
        doc = _Document(); doc.feed(parent['html'])
        nodes = list(doc.root.nodes())
        for tag, heading in [('h2',parent_name),('h3',f'Vereinsspielplan DFB-Pokal {first}/{first+1}')]:
            if sum(n.tag == tag and n.text() == heading for n in nodes) != 1: fail()
        back = [urlsplit(urljoin(url,n.attrs.get('href',''))) for n in nodes
                if n.tag == 'a' and n.text() == 'Zurück zur Vereinsseite']
        if (len(back) != 1 or back[0].scheme != 'https' or back[0].netloc != 'datencenter.dfb.de'
                or back[0].path != '/clubs/'+parent_club): fail()
        entries = []
        for row in (n for n in nodes if n.has('c-MatchTable-row')):
            cells = list(row.nodes())
            teams = [n.text() for n in cells if n.has('c-MatchTable-team')]
            if not teams or teams == ['Heim','Auswärts']: continue
            scores = [n.text() for n in cells if n.has('c-MatchTable-center')]
            if (len(teams) != 2 or teams.count(parent_name) != 1 or len(scores) != 1
                    or not re.fullmatch(r'\d+\s*:\s*\d+(?:\s+(?:i\.E\.|n\.V\.))?',scores[0])): fail()
            dates = re.findall(r'\b(\d{2}\.\d{2}\.\d{4})\b',row.text())
            if len(dates) != 1: fail()
            day, month, year = map(int,dates[0].split('.')); played = date(year,month,day)
            if not date(first,7,1) <= played <= end: fail()
            season_path=rf'/datencenter/dfb-pokal/{first}-(?:{first+1}|{(first+1)%100:02d})/'
            links = {n.attrs.get('href','') for n in cells if n.tag == 'a'
                     and re.search(season_path,n.attrs.get('href',''))}
            if len(links) != 1: fail()
            match_url = links.pop(); parsed = urlsplit(match_url)
            if parsed.scheme != 'https' or parsed.netloc != 'datencenter.dfb.de': fail()
            entries.append(match_url)
        if not entries or len(set(entries)) != len(entries): fail()
        return {**scope, 'category':'Национальный кубок', 'numbers':{'appearances':0,'goals':0},
                'competition_slug':'dfb-pokal',
                'matches':[], 'statistics_as_of':end.isoformat(), 'complete':True,
                'sources':identity['sources']+[RULE_URL,url],
                'absence_reason':'one_team_per_club_since_2009_parent_entered',
                'parent_entry_match_urls':entries}
    except UpdateError: raise
    except (KeyError,TypeError,ValueError,AttributeError,IndexError): fail()
