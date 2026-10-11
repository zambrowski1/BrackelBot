# SPDX-License-Identifier: MIT
"""Closed U21 evidence when a former player's personal DFB profile is absent."""
import re
from datetime import date
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit
from .dfb_evidence import _Document,_name
from .dfb_youth_profile import cells
from .errors import UpdateError

ALL_URL='https://datencenter.dfb.de/datencenter/mannschaft/deutschland-u-21/alle-spieler/15'
ROSTER_URL='https://datencenter.dfb.de/en/competitions/under-21-friendly/seasons/2014-2015/teams/germany-u-21'
MATCHES={2210000:('netherlands','Netherlands','Niederlande U 21'),2215041:('italy','Italy','Italien U 21')}

def fail():
    raise UpdateError('dfb_roster_youth_unproven','Не подтверждены личность, полный итог и выходы на поле за молодёжную сборную DFB')

def document(source,canonical):
    if not isinstance(source,dict) or not isinstance(source.get('html'),str) or not 0<len(source['html'])<=2_000_000:fail()
    u=urlsplit(source.get('url',''));expected=urlsplit(canonical)
    if (u.scheme,u.netloc,u.path,u.fragment)!=(expected.scheme,expected.netloc,expected.path,''):fail()
    if u.query and canonical!=ALL_URL:fail()
    if canonical==ALL_URL and u.query not in {'','sort_by=statistics.matches&sort_order=DESC'}:fail()
    doc=_Document();doc.feed(source['html']);nodes=list(doc.root.nodes())
    if [n.attrs.get('content') for n in nodes if n.tag=='meta' and n.attrs.get('property')=='og:url']!=[canonical]:fail()
    return nodes

def metadata(nodes,key):
    values=[n.attrs.get('content') for n in nodes if n.tag=='meta' and n.attrs.get('property')==key]
    if len(values)!=1 or not isinstance(values[0],str):fail()
    return values[0]

def match(source,mid,now):
    slug,opponent,display=MATCHES[mid]
    url=f'https://datencenter.dfb.de/en/data-center/under-21-friendly/2014-2015/season/germany-{slug}-{mid}'
    nodes=document(source,url)
    description=metadata(nodes,'og:description')
    stamp=re.search(r' at ((?:Mon|Tue|Wed|Thu|Fri|Sat|Sun), \d{1,2} [A-Za-z]{3} \d{4} \d{2}:\d{2}:\d{2} [+-]\d{4}),',description)
    if not stamp:fail()
    day=parsedate_to_datetime(stamp[1]).date()
    if not date(2014,7,1)<=day<=date(2015,6,30) or day>now.date():fail()
    teams=[n.text().strip() for n in nodes if n.attrs.get('class')=='m-MatchDetails-title']
    if teams!=['Germany',opponent]:fail()
    scores=[n.text().strip() for n in nodes if n.has('m-MatchDetails-score')]
    if len(scores)!=1:fail()
    score=re.fullmatch(r'(\d+):(\d+)\s*\((\d+):(\d+)\)',scores[0])
    if not score:fail()
    home,away,halfhome,halfaway=map(int,score.groups())
    if max(home,away)>100 or halfhome>home or halfaway>away:fail()
    if metadata(nodes,'og:title')!=f'Germany - {opponent}, {home}:{away}, Under-21 friendly 2014/15 Season - DFB data center':fail()
    tables=[n for n in nodes if n.has('m-MatchDetails-lineup-table') and any(a.has('m-MatchDetails-lineup-title') and a.text()=='Lineup' for a in n.nodes())]
    if len(tables)!=1:fail()
    bodies=[n for n in tables[0].nodes() if n.tag=='tbody']
    if len(bodies)!=1:fail()
    starters=[_name(n.text()) for n in bodies[0].nodes() if n.tag=='td' and n.has('m-MatchDetails-lineup-person--home') and n.text().strip()]
    if len(starters)!=11 or len(set(starters))!=11:fail()
    subhead=[n for n in nodes if n.has('m-MatchDetails-history-title') and n.text()=='Substitutes']
    if len(subhead)!=1:fail()
    incoming=[n for n in subhead[0].parent.nodes() if n.has('m-MatchDetails-history-event-text--home')
              and any(a.has('m-MatchDetails-history-event-text-segment') and _name(a.text())=='willi orban for' for a in n.nodes())]
    starter=starters.count('willi orban')
    if starter+len(incoming)!=1:fail()
    if incoming:
        entry=incoming[0]
        segments=[_name(n.text()) for n in entry.nodes() if n.has('m-MatchDetails-history-event-text-segment')]
        if len(segments)!=2 or segments[0]!='willi orban for' or segments[1] not in starters:fail()
        item=entry.parent.parent
        if not item.has('m-MatchDetails-history-item'):fail()
        minutes=re.findall(r'\b(\d{1,2})\s*\u0027',item.text())
        if len(minutes)!=1 or not 0<int(minutes[0])<90:fail()
    goals=[n for n in nodes if n.has('m-MatchDetails-history-title') and n.text()=='Goals']
    if len(goals)!=1:fail()
    for side,count in [('home',home),('away',away)]:
        events=[n.text().strip() for n in goals[0].parent.nodes() if n.has('m-MatchDetails-history-event-text--'+side) and n.text().strip()]
        if len(events)!=count or any(len(re.findall(r'\b\d+:\d+\b',e))!=1 for e in events):fail()
        if any('willi orban' in _name(e) for e in events):fail()
    return {'date':day.isoformat(),'url':url,'home':'Deutschland U 21','away':display,
            'display_score':[home,away],'shootout':False,'competition':'Länderspiel',
            'appearance_proof':'starter' if starter else 'entered_substitute'}

def parse_roster_youth(mapping,bundle,now):
    try:
        if (type(mapping.get('api_id')) is not int
                or (mapping.get('api_id'),mapping.get('qid'),mapping.get('birth_date'))!=(136924,'Q2577379','1992-11-03')
                or bundle.get('player_id')!=136924 or type(bundle['player_id']) is not int
                or mapping.get('source_name') and _name(mapping['source_name'])!='willi orban'
                or now.year<=2015):fail()
        roster=document(bundle['roster'],ROSTER_URL)
        if metadata(roster,'og:title')!='Germany Under-21 friendly 2014/15 - DFB data center':fail()
        players=[cells(n) for n in roster if n.tag=='tr' and n.has('c-Table-body-row')]
        own=[r for r in players if len(r)==3 and _name(r[1].text().split('(')[0])=='willi orban']
        if len(own)!=1 or own[0][0].text()!='18' or own[0][2].text()!=mapping['birth_date']:fail()
        summary=document(bundle['all_players'],ALL_URL)
        if metadata(summary,'og:description')!='Alle bisherigen und aktuellen Spieler der Mannschaft Deutschland U 21.':fail()
        headers=[n.text() for n in summary if n.tag=='th']
        if headers!=['Spieler/in','Spiele','Tore','Elfmeter','erstes Spiel','letztes Spiel']:fail()
        rows=[cells(n) for n in summary if n.tag=='tr']
        own=[r for r in rows if len(r)==6 and _name(r[0].text())=='orban, willi']
        if len(own)!=1 or [c.text() for c in own[0][1:4]]!=['2','0','0']:fail()
        first,last=[date.fromisoformat('-'.join(reversed(c.text().split('.')))) for c in own[0][4:]]
        protocols=bundle['protocols']
        if len(protocols)!=2 or {p['match_id'] for p in protocols}!=set(MATCHES) or any(type(p['match_id']) is not int for p in protocols):fail()
        games=sorted([match(p,p['match_id'],now) for p in protocols],key=lambda g:g['date'])
        if [date.fromisoformat(g['date']) for g in games]!=[first,last]:fail()
        return [{'age':21,'complete':True,'numbers':{'appearances':2,'goals':0},'matches':games,
                 'sources':[ALL_URL,ROSTER_URL]+[g['url'] for g in games],
                 'first_date':first.isoformat(),'statistics_as_of':last.isoformat()}]
    except UpdateError:raise
    except (KeyError,TypeError,ValueError,AttributeError):fail()
