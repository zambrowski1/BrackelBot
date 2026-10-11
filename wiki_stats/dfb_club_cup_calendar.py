# SPDX-License-Identifier: MIT
"""Verify Kaiserslautern's complete 2012/13 cup run through elimination."""
from datetime import date,datetime
import re
from .dfb_club_calendar import TEAM,PLAYER,one,protocol
from .dfb_roster_youth import document,metadata
from .dfb_evidence import _name
from .dfb_youth_profile import cells
from .errors import UpdateError

CALENDAR='https://datencenter.dfb.de/competitions/dfb-pokal/seasons/2012-2013/teams/1-fc-kaiserslautern'
PREFIX='https://datencenter.dfb.de/datencenter/dfb-pokal/2012-2013/'

def fail():
    raise UpdateError('dfb_club_cup_calendar_unproven','Не подтверждены личность игрока, полный кубковый календарь или вылет команды DFB')

def parse_cup_calendar(mapping,bundle,now):
    try:
        if (type(mapping.get('api_id')) is not int or mapping['api_id']!=136924
                or mapping.get('qid')!='Q2577379' or mapping.get('birth_date')!='1992-11-03'
                or mapping.get('source_name') and _name(mapping['source_name'])!=PLAYER
                or type(bundle.get('player_id')) is not int or bundle['player_id']!=136924
                or now.date()<=date(2013,6,30)):fail()
        nodes=document(bundle['calendar'],CALENDAR)
        if metadata(nodes,'og:title')!='1. FC Kaiserslautern, DFB-Pokal 2012/13 - DFB Datencenter':fail()
        roster=[cells(n) for n in nodes if n.tag=='tr']
        own=[r for r in roster if len(r)==3 and _name(r[1].text().split('(')[0])==PLAYER]
        if len(own)!=1 or own[0][0].text()!='34' or own[0][2].text()!='03.11.1992':fail()
        rows=[n for n in nodes if n.has('c-MatchTable-row') and any(a.tag=='a' and a.text()=='Schema' for a in n.nodes())]
        # This adapter certifies this closed run only: win in round one,
        # loss in round two. Extra time and shootouts require separate proof.
        if len(rows)!=2:fail()
        calendar=[]
        for row in rows:
            children=list(row.nodes())
            links=[n.attrs.get('href') for n in children if n.tag=='a' and n.text()=='Schema']
            if len(links)!=1:fail()
            match=re.fullmatch(re.escape(PREFIX)+r'([12])-runde/[a-z0-9-]+-([1-9][0-9]*)',links[0])
            if not match:fail()
            stamp=re.fullmatch(r'\w+, (\d{2}\.\d{2}\.\d{4}) (\d{2}:\d{2}) Uhr',one(children,'c-MatchTable-description').text())
            score=re.fullmatch(r'(\d+)\s*:\s*(\d+)',one(children,'c-MatchTable-score').text())
            teams=[one(children,'c-MatchTable-team--'+side).text() for side in ['home','away']]
            if not stamp or not score or teams.count(TEAM)!=1:fail()
            calendar.append({'round':int(match[1]),'match_id':int(match[2]),'url':links[0],
                'date':datetime.strptime(stamp[1],'%d.%m.%Y').date().isoformat(),'kickoff':stamp[2],
                'teams':teams,'score':list(map(int,score.groups())),'competition':'DFB-Pokal'})
        calendar.sort(key=lambda g:g['round'])
        if [g['round'] for g in calendar]!=[1,2] or len({g['match_id'] for g in calendar})!=2:fail()
        if calendar[0]['date']>=calendar[1]['date']:fail()
        for g,won in zip(calendar,[True,False]):
            own=g['teams'].index(TEAM)
            ours,theirs=g['score'][own],g['score'][1-own]
            if (ours<=theirs if won else ours>=theirs):fail()
        protocols=bundle['protocols']
        if len(protocols)!=2 or any(type(p.get('match_id')) is not int for p in protocols):fail()
        indexed={p['match_id']:p for p in protocols}
        if len(indexed)!=2 or set(indexed)!={g['match_id'] for g in calendar}:fail()
        games=[protocol(indexed[g['match_id']],g,now) for g in calendar]
        played=[g for g in games if g['appearances']]
        return [{'team':TEAM,'team_slug':'1-fc-kaiserslautern','season':'2012/13','category':'Национальный кубок',
            'competition_slug':'dfb-pokal','numbers':{'appearances':len(played),'goals':sum(g['goals'] for g in played)},
            'complete':True,'matches':played,'statistics_as_of':'2013-06-30',
            'confirmed_calendar_match_ids':[g['match_id'] for g in games],
            'verified_absence_match_ids':[g['match_id'] for g in games if not g['appearances']],
            'elimination_match_id':calendar[-1]['match_id'],
            'sources':[CALENDAR]+[g['url'] for g in games]}]
    except UpdateError as exc:
        if exc.code!='dfb_club_cup_calendar_unproven':fail()
        raise
    except (KeyError,TypeError,ValueError,AttributeError,IndexError):fail()
