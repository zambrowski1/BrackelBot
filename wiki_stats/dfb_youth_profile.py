# SPDX-License-Identifier: MIT
"""Validate closed German youth totals against complete federation game lists."""
from datetime import datetime,date
import re
from .dfb_evidence import _Document,_Node,_name
from .errors import UpdateError

LABELS={19:'Deutschland U 19 (m)',20:'Deutschland U 20 (m)',21:'Deutschland U 21',23:'Deutschland (Olympia)'}


def cells(row):
    return [n for n in row.children if isinstance(n,_Node) and n.tag in {'th','td'}]


def summary(row):
    values=cells(row)
    if len(values)!=9 or any(not re.fullmatch(r'[0-9]+',c.text()) for c in values[1:]):
        raise UpdateError('dfb_youth_unproven','Не подтверждены столбцы статистики DFB')
    numbers=[int(c.text()) for c in values[1:]]
    if numbers[0]!=sum(numbers[1:4]) or numbers[0]<=0:
        raise UpdateError('dfb_youth_unproven','Суммы выступлений DFB не совпали')
    return {'appearances':numbers[0],'goals':numbers[4]}


def following(row):
    siblings=row.parent.children;index=siblings.index(row)
    return next((n for n in siblings[index+1:] if isinstance(n,_Node)),None)


def played_rows(content,team,now):
    games=[]
    for row in content.nodes():
        if not row.has('c-MatchTable-row'):continue
        cols=[n for n in row.children if isinstance(n,_Node) and n.has('c-MatchTable-col')]
        if len(cols)!=5:raise UpdateError('dfb_youth_unproven','Неверная строка матча DFB')
        if cols[0].text()=='Datum':continue
        text=cols[0].text();match=re.search(r'\b([0-9]{2}\.[0-9]{2}\.[0-9]{4})\b',text)
        if not match:raise UpdateError('dfb_youth_unproven','Нет даты матча DFB')
        day=datetime.strptime(match[1],'%d.%m.%Y').date()
        if day>now.date():raise UpdateError('dfb_youth_unproven','Будущий матч в статистике выступлений')
        home,away=(_name(cols[i].text()) for i in (1,3))
        if (home==_name(team))+(away==_name(team))!=1:
            raise UpdateError('dfb_youth_unproven','Матч другой возрастной сборной')
        links=[n.attrs.get('href','') for n in cols[2].nodes() if n.tag=='a']
        if len(links)!=1 or not re.fullmatch(r'https://datencenter\.dfb\.de/datencenter/[^/?#]+/[^/?#]+/[^/?#]+/[^/?#]+-[1-9][0-9]*',links[0]):
            raise UpdateError('dfb_youth_unproven','Нет однозначного протокола матча')
        score=re.search(r'\b([0-9]+)\s*:\s*([0-9]+)\b',cols[2].text())
        if not score:raise UpdateError('dfb_youth_unproven','Нет счёта матча')
        games.append({'date':day.isoformat(),'url':links[0],'home':cols[1].text(),'away':cols[3].text(),
                      'display_score':[int(score[1]),int(score[2])],
                      'shootout':'i.E.' in cols[2].text()})
    return games


def parse_closed_youth(mapping,bundle,now):
    def fail():raise UpdateError('dfb_youth_unproven','Не подтверждена полная молодёжная статистика DFB')
    try:
        if isinstance(bundle,dict) and bundle.get('kind')=='dfb_roster_closed':
            from .dfb_roster_youth import parse_roster_youth
            return parse_roster_youth(mapping,bundle,now)
        if (not isinstance(bundle,dict) or type(mapping.get('api_id')) is not int
                or type(bundle.get('player_id')) is not int or bundle['player_id']!=mapping['api_id']):fail()
        profile=bundle['profile'];url=profile['url'];html=profile['html']
        if (not isinstance(html,str) or len(html)>2_000_000
                or not re.fullmatch(r'https://datencenter\.dfb\.de/(?:datencenter/)?personen/[a-z0-9-]+/spieler(?:/spiele-in-wettbewerb/[a-z0-9-]+/[a-z0-9-]+)?',url)):fail()
        doc=_Document();doc.feed(html)
        metadata={}
        for row in doc.root.nodes():
            c=cells(row) if row.tag=='tr' else []
            if len(c)==2 and c[0].text() in {'Name:','Geburtstag:','Nation:'}:
                if c[0].text() in metadata:fail()
                metadata[c[0].text()]=c[1].text()
        if (_name(metadata.get('Name:',''))!=_name(mapping.get('source_name') or mapping['wiki_name'])
                or metadata.get('Nation:')!='Deutschland'
                or datetime.strptime(metadata['Geburtstag:'],'%d.%m.%Y').date()!=date.fromisoformat(mapping['birth_date'])):fail()
        original_urls=[n.attrs.get('content') for n in doc.root.nodes() if n.tag=='meta' and n.attrs.get('property')=='og:url']
        if original_urls!=[url]:fail()
        fragments={}
        for fragment in bundle['fragments']:
            key=(fragment['frame_id'],fragment['url'])
            if key in fragments:fail()
            fragments[key]=fragment
        results=[]
        for age,label in LABELS.items():
            matches=[r for r in doc.root.nodes() if r.tag=='tr' and r.has('c-Table-dropdown-trigger')
                     and len(cells(r))==9 and cells(r)[0].text()==label]
            if not matches:continue
            if len(matches)!=1:fail()
            # An old snapshot cannot certify the final career of an eligible youth player.
            if age!=23 and now.year<=date.fromisoformat(mapping['birth_date']).year+age+2:fail()
            total=summary(matches[0]);content=following(matches[0])
            if content is None or not content.has('c-Table-dropdown-content'):fail()
            competitions=[r for r in content.nodes() if r.tag=='tr' and r.has('c-Table-match-table-row-before')]
            totals={'appearances':0,'goals':0};games=[];sources=[url]
            for competition in competitions:
                numbers=summary(competition)
                for key in totals:totals[key]+=numbers[key]
                listing=following(competition)
                if listing is None or not listing.has('c-Table-matches-content'):fail()
                lazy=[n for n in listing.nodes() if n.tag=='turbo-frame' and n.attrs.get('src')]
                if lazy:
                    if len(lazy)!=1:fail()
                    attrs=lazy[0].attrs;key=(attrs['id'],attrs['src']);fragment=fragments.get(key)
                    if fragment is None or not isinstance(fragment['html'],str) or len(fragment['html'])>2_000_000:fail()
                    if not attrs['src'].startswith('https://datencenter.dfb.de/datencenter/personen/'+url.split('/personen/')[1].split('/')[0]+'/spieler/'):fail()
                    loaded=_Document();loaded.feed(fragment['html'])
                    frames=[n for n in loaded.root.nodes() if n.tag=='turbo-frame' and n.attrs.get('id')==attrs['id']]
                    if len(frames)!=1:fail()
                    rows=played_rows(frames[0],label,now);sources.append(attrs['src'])
                else:rows=played_rows(listing,label,now)
                if len(rows)!=numbers['appearances']:fail()
                for row in rows:
                    row['competition']=cells(competition)[0].text().replace('Spiele anzeigen','').replace('Spiele verbergen','').strip()
                games.extend(rows)
            if totals!=total or len(games)!=total['appearances'] or len({r['url'] for r in games})!=len(games):fail()
            birth = date.fromisoformat(mapping['birth_date'])
            if any(date.fromisoformat(r['date']) < birth for r in games):fail()
            if age!=23 and any(int(r['date'][:4])>birth.year+age+2 for r in games):fail()
            games.sort(key=lambda r:(r['date'],r['url']))
            results.append({'age':age,'complete':True,'numbers':total,'matches':games,'sources':sources,
                            'first_date':games[0]['date'],'statistics_as_of':games[-1]['date']})
        if not results:fail()
        return results
    except UpdateError:raise
    except (KeyError,TypeError,ValueError,AttributeError):fail()
