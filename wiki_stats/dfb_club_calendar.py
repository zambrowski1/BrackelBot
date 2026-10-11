# SPDX-License-Identifier: MIT
"""Close a historical league season from every official match protocol."""
from datetime import date,datetime
import re
from .dfb_roster_youth import document,metadata
from .dfb_evidence import _name
from .dfb_youth_profile import cells
from .errors import UpdateError

CALENDAR='https://datencenter.dfb.de/competitions/2-bundesliga/seasons/2012-2013/teams/1-fc-kaiserslautern'
PREFIX='https://datencenter.dfb.de/datencenter/2-bundesliga/2012-2013/'
MONTHS=dict(zip(['Januar','Februar','März','April','Mai','Juni','Juli','August','September','Oktober','November','Dezember'],range(1,13)))
PLAYER='willi orban'
TEAM='1. FC Kaiserslautern'


def fail():
    raise UpdateError('dfb_club_calendar_unproven','Не подтверждены полный календарь, личность игрока или протоколы закрытого клубного сезона DFB')


def one(nodes,cls):
    found=[n for n in nodes if n.has(cls)]
    if len(found)!=1:fail()
    return found[0]


def section(nodes,label):
    found=[n for n in nodes if n.has('m-MatchDetails-history-title') and n.text()==label]
    if len(found)!=1:fail()
    return list(found[0].parent.nodes())


def substitutions(nodes,side,label,starters):
    events=[n for n in section(nodes,label) if n.has('m-MatchDetails-history-event-text--'+side) and n.text().strip()]
    active=set(starters);entered=[];proof=[]
    for event in events:
        item=event.parent.parent
        if not item.has('m-MatchDetails-history-item'):fail()
        minutes=re.findall(r'\b(\d{1,3})\s*\u0027',item.text())
        if len(minutes)!=1 or not 0<int(minutes[0])<=90:fail()
        pieces=[_name(n.text()) for n in event.nodes() if n.has('m-MatchDetails-history-event-text-segment')]
        if not pieces or len(pieces)%2:fail()
        for incoming,outgoing in zip(pieces[::2],pieces[1::2]):
            if not incoming.endswith(' fur'):fail()
            incoming=incoming[:-4]
            if outgoing not in active or incoming in active or incoming in entered:fail()
            active.remove(outgoing);active.add(incoming);entered.append(incoming)
            proof.append((int(minutes[0]),incoming,outgoing))
    if len(entered)>3 or proof!=sorted(proof,key=lambda p:p[0]):fail()
    return proof


def protocol(source,calendar,now):
    nodes=document(source,calendar['url'])
    teams=[n.text().strip() for n in nodes if n.attrs.get('class')=='m-MatchDetails-title']
    if teams!=calendar['teams'] or teams.count(TEAM)!=1:fail()
    side='home' if teams[0]==TEAM else 'away'
    raw=one(nodes,'m-MatchDetails-score').text()
    score=re.fullmatch(r'(\d+):(\d+)\s*\((\d+):(\d+)\)',raw)
    if not score:fail()
    home,away,hh,ha=map(int,score.groups())
    if [home,away]!=calendar['score'] or hh>home or ha>away or max(home,away)>100:fail()
    round=calendar['round']
    competition=calendar.get('competition','2. Bundesliga')
    if competition not in {'2. Bundesliga','DFB-Pokal'}:fail()
    stage='Runde' if competition=='DFB-Pokal' else 'Spieltag'
    if metadata(nodes,'og:title')!=f'{teams[0]} - {teams[1]}, {home}:{away}, {competition} 2012/13 {round}. {stage} - DFB Datencenter':fail()
    description=metadata(nodes,'og:description')
    stamp=re.search(r' am \w+, (\d{2})\. (\w+) (\d{4}), (\d{2}:\d{2}) Uhr,',description)
    if not stamp or stamp[2] not in MONTHS:fail()
    day=date(int(stamp[3]),MONTHS[stamp[2]],int(stamp[1]))
    if day.isoformat()!=calendar['date'] or not date(2012,7,1)<=day<=date(2013,6,30) or day>now.date():fail()
    if stamp[4]!=calendar['kickoff']:fail()
    tables=[n for n in nodes if n.has('m-MatchDetails-lineup-table') and any(
        a.has('m-MatchDetails-lineup-title') and a.text()=='Aufstellung' for a in n.nodes())]
    if len(tables)!=1:fail()
    bodies=[n for n in tables[0].nodes() if n.tag=='tbody']
    if len(bodies)!=1:fail()
    starters={}
    for team_side in ['home','away']:
        names=[_name(n.text()) for n in bodies[0].nodes() if n.tag=='td' and n.has('m-MatchDetails-lineup-person--'+team_side) and n.text().strip()]
        if len(names)!=11 or len(set(names))!=11:fail()
        starters[team_side]=names
    subs=substitutions(nodes,side,'Wechsel',starters[side])
    benches=[n for n in nodes if n.has('m-MatchDetails-lineup-table') and any(
        a.has('m-MatchDetails-lineup-title') and a.text()=='Auswechselspieler' for a in n.nodes())]
    if len(benches)!=1:fail()
    bench=[_name(n.text()) for n in benches[0].nodes() if n.tag=='td' and n.has('m-MatchDetails-lineup-person--'+side) and n.text().strip()]
    if len(bench)>7 or len(set(bench))!=len(bench) or any(incoming not in bench for _,incoming,_ in subs):fail()
    # The full chronology and dedicated substitution table must agree.
    history=[n for n in section(nodes,'Spielverlauf') if n.has('m-MatchDetails-history-event-text--'+side)
        and any(a.has('m-MatchDetails-history-event-text-segment') and _name(a.text()).endswith(' fur') for a in n.nodes())]
    chronology=[]
    for event in history:
        item=event.parent.parent
        minutes=re.findall(r'\b(\d{1,3})\s*\u0027',item.text())
        pieces=[_name(n.text()) for n in event.nodes() if n.has('m-MatchDetails-history-event-text-segment')]
        if len(minutes)!=1 or not pieces or len(pieces)%2:fail()
        chronology.extend((int(minutes[0]),incoming[:-4],outgoing) for incoming,outgoing in zip(pieces[::2],pieces[1::2]) if incoming.endswith(' fur'))
    if chronology!=subs:fail()
    appearance=int(PLAYER in starters[side])+sum(incoming==PLAYER for _,incoming,_ in subs)
    if appearance not in {0,1}:fail()
    events=[n for n in section(nodes,'Tore') if (n.has('m-MatchDetails-history-event-text--home') or n.has('m-MatchDetails-history-event-text--away')) and n.text().strip()]
    tally=[0,0];goals=0
    goal_proof=[]
    for event in events:
        index=0 if event.has('m-MatchDetails-history-event-text--home') else 1
        segments=[n for n in event.nodes() if n.has('m-MatchDetails-history-event-text-segment')]
        if not segments:fail()
        for segment in segments:
            scores=re.findall(r'\b(\d+):(\d+)\b',segment.text())
            if len(scores)!=1:fail()
            tally[index]+=1
            if list(map(int,scores[0]))!=tally:fail()
            name=_name(re.sub(r'\b\d+:\d+\b','',segment.text()))
            goal_proof.append((index,tuple(tally),name))
            if PLAYER in name:
                if name!=PLAYER:fail() # Never credit an own-goal or ambiguous scorer.
                if index==(0 if side=='home' else 1):goals+=1
    history_goals=[]
    for event in section(nodes,'Spielverlauf'):
        if not (event.has('m-MatchDetails-history-event-text--home') or event.has('m-MatchDetails-history-event-text--away')):continue
        index=0 if event.has('m-MatchDetails-history-event-text--home') else 1
        for segment in event.nodes():
            if not segment.has('m-MatchDetails-history-event-text-segment'):continue
            scores=re.findall(r'\b(\d+):(\d+)\b',segment.text())
            if not scores:continue
            if len(scores)!=1:fail()
            history_goals.append((index,tuple(map(int,scores[0])),_name(re.sub(r'\b\d+:\d+\b','',segment.text()))))
    if history_goals!=goal_proof:fail()
    if tally!=[home,away] or goals and not appearance:fail()
    return {'date':day.isoformat(),'url':calendar['url'],'match_id':calendar['match_id'],
        'appearances':appearance,'goals':goals,'appearance_proof':'starter' if PLAYER in starters[side] else 'entered_substitute' if appearance else 'absent_from_full_played_lineup'}


def parse_calendar_club(mapping,bundle,now):
    try:
        if (type(mapping.get('api_id')) is not int or mapping['api_id']!=136924 or mapping.get('qid')!='Q2577379'
                or mapping.get('birth_date')!='1992-11-03' or mapping.get('source_name') and _name(mapping['source_name'])!=PLAYER
                or type(bundle.get('player_id')) is not int or bundle['player_id']!=136924 or now.date()<=date(2013,6,30)):fail()
        nodes=document(bundle['calendar'],CALENDAR)
        if metadata(nodes,'og:title')!='1. FC Kaiserslautern, 2. Bundesliga 2012/13 - DFB Datencenter':fail()
        own=[cells(n) for n in nodes if n.tag=='tr']
        own=[r for r in own if len(r)==3 and _name(r[1].text().split('(')[0])==PLAYER]
        if len(own)!=1 or own[0][0].text()!='34' or own[0][2].text()!='03.11.1992':fail()
        rows=[n for n in nodes if n.has('c-MatchTable-row') and any(a.tag=='a' and a.text()=='Schema' for a in n.nodes())]
        if len(rows)!=34:fail()
        calendar=[]
        for row in rows:
            children=list(row.nodes());links=[n.attrs.get('href') for n in children if n.tag=='a' and n.text()=='Schema']
            if len(links)!=1:fail()
            match=re.fullmatch(re.escape(PREFIX)+r'([1-9][0-9]*)-spieltag/[a-z0-9-]+-([1-9][0-9]*)',links[0])
            if not match:fail()
            raw=one(children,'c-MatchTable-description').text()
            stamp=re.fullmatch(r'\w+, (\d{2}\.\d{2}\.\d{4}) (\d{2}:\d{2}) Uhr',raw)
            if not stamp:fail()
            teams=[one(children,'c-MatchTable-team--'+side).text() for side in ['home','away']]
            if teams.count(TEAM)!=1:fail()
            raw_score=one(children,'c-MatchTable-score').text()
            score=re.fullmatch(r'(\d+)\s*:\s*(\d+)',raw_score)
            if not score:fail()
            calendar.append({'round':int(match[1]),'match_id':int(match[2]),'url':links[0],
                'date':datetime.strptime(stamp[1],'%d.%m.%Y').date().isoformat(),'kickoff':stamp[2],
                'teams':teams,'score':list(map(int,score.groups()))})
        if {g['round'] for g in calendar}!=set(range(1,35)) or len({g['match_id'] for g in calendar})!=34:fail()
        protocols=bundle['protocols']
        if len(protocols)!=34 or any(type(p.get('match_id')) is not int for p in protocols):fail()
        indexed={p['match_id']:p for p in protocols}
        if len(indexed)!=34 or set(indexed)!={g['match_id'] for g in calendar}:fail()
        games=[protocol(indexed[g['match_id']],g,now) for g in calendar]
        played=[g for g in games if g['appearances']]
        scopes=[{'team':TEAM,'team_slug':'1-fc-kaiserslautern','season':'2012/13','category':'Чемпионат',
            'competition_slug':'2-bundesliga','numbers':{'appearances':len(played),'goals':sum(g['goals'] for g in played)},
            'complete':True,'matches':played,'statistics_as_of':'2013-06-30',
            'confirmed_calendar_match_ids':[g['match_id'] for g in games],
            'verified_absence_match_ids':[g['match_id'] for g in games if not g['appearances']],
            'sources':[CALENDAR]+[g['url'] for g in games]}]
        if bundle.get('kind')=='dfb_full_club_calendar_and_cup':
            from .dfb_club_cup_calendar import parse_cup_calendar
            scopes.extend(parse_cup_calendar(mapping,bundle['cup'],now))
        return scopes
    except UpdateError as exc:
        if exc.code!='dfb_club_calendar_unproven':fail()
        raise
    except (KeyError,TypeError,ValueError,AttributeError,IndexError):fail()
