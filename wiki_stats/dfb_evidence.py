# SPDX-License-Identifier: MIT
"""Cross-check an unused substitute against an independent DFB match sheet."""
from html.parser import HTMLParser
from datetime import datetime
import re
import unicodedata


class _Node:
    def __init__(self,tag='',attrs=(),parent=None):
        self.tag=tag;self.attrs=dict(attrs);self.parent=parent;self.children=[]
    def text(self):
        return ' '.join(c if isinstance(c,str) else c.text() for c in self.children).strip()
    def nodes(self):
        for child in self.children:
            if isinstance(child,_Node):
                yield child;yield from child.nodes()
    def has(self,cls):return cls in self.attrs.get('class','').split()


class _Document(HTMLParser):
    def __init__(self):super().__init__();self.root=_Node();self.current=self.root
    def handle_starttag(self,tag,attrs):
        node=_Node(tag,attrs,self.current);self.current.children.append(node)
        if tag not in {'br','img','meta','link','hr','input','source','wbr'}:self.current=node
    def handle_startendtag(self,tag,attrs):self.current.children.append(_Node(tag,attrs,self.current))
    def handle_endtag(self,tag):
        node=self.current
        while node.parent and node.tag!=tag:node=node.parent
        if node.parent:self.current=node.parent
    def handle_data(self,data):self.current.children.append(data)


def _name(text):
    text=re.sub(r'\s*\(T\)\s*$','',text)
    return ' '.join(''.join(c for c in unicodedata.normalize('NFKD',text.casefold())
                           if not unicodedata.combining(c)).split())


def dfb_unused_substitute(row,pid,team_id,lineup,html,url):
    """Return independent evidence, never infer zero from missing API events."""
    try:
        if (not isinstance(html,str) or len(html)>2_000_000
                or not re.fullmatch(r'https://datencenter\.dfb\.de/datencenter/dfb-pokal/\d{4}-\d{4}/[^/?#]+/[^/?#]+-\d+',url)
                or lineup.get('match_id')!=row.get('id') or lineup.get('is_confirmed') is not True
                or (row.get('tournament') or {}).get('id')!=217
                or row.get('is_finished') is not True or (row.get('status') or {}).get('type')!='finished'):
            return None
        own=[side for side in ('home','away') if row[side+'_team']['id']==team_id]
        if len(own)!=1:return None
        doc=_Document();doc.feed(html)
        nodes=list(doc.root.nodes())
        meta={n.attrs.get('property'):n.attrs.get('content') for n in nodes if n.tag=='meta'}
        if meta.get('og:url')!=url:return None
        description=meta.get('og:description','')
        stamp=re.search(r'(\d{1,2})\. (\w+) (\d{4}), (\d{2}:\d{2}) Uhr',description)
        months=['Januar','Februar','März','April','Mai','Juni','Juli','August','September','Oktober','November','Dezember']
        day=datetime.fromisoformat(row['start_time'].replace('Z','+00:00'))
        if (day.tzinfo is None or not stamp or stamp[2] not in months
                or (int(stamp[3]),months.index(stamp[2])+1,int(stamp[1]))!=(day.year,day.month,day.day)):
            return None
        # Explicit calendar season is authoritative, including July cup games.
        label=str(row['season']['year']);year=int(label.split('/')[0])
        if year<100:year+=2000
        # Fourth substitutions were introduced during 2016/17. This proof
        # uses only the preceding, known three-substitution rule: three
        # independently named entrants exhaust the permitted replacements.
        # https://www.dfb.de/fileadmin/_dfbdam/126236-OM_09_2016_Final.pdf
        if not 2009<=year<2016 or f'/{year}-{year+1}/' not in url:return None
        score=re.search(r' (\d+):(\d+) \(',description)
        if not score or [int(score[1]),int(score[2])]!=[row['home_score']['current'],row['away_score']['current']]:
            return None
        tables=[n for n in nodes if n.tag=='table' and n.has('m-MatchDetails-lineup-table')]
        sheets={}
        for table in tables:
            headings=[n.text() for n in table.nodes() if n.tag=='th' and n.has('m-MatchDetails-lineup-title')]
            if len(headings)!=1 or headings[0] not in {'Aufstellung','Auswechselspieler'}:return None
            if headings[0] in sheets:return None
            bodies=[n for n in table.nodes() if n.tag=='tbody']
            if len(bodies)!=1:return None
            sheets[headings[0]]={side:[_name(n.text()) for n in bodies[0].nodes()
                if n.tag=='td' and n.has('m-MatchDetails-lineup-person--'+side) and n.text()] for side in ('home','away')}
        if set(sheets)!={'Aufstellung','Auswechselspieler'}:return None
        seen_ids=[];target=None
        for side in ('home','away'):
            players=lineup[side]['players']
            if sum(p.get('is_substitute') is False for p in players)!=11:return None
            if any(type(p.get('is_substitute')) is not bool for p in players):return None
            names=[_name(p['player']['name']) for p in players]
            starters=sheets['Aufstellung'][side];bench=sheets['Auswechselspieler'][side]
            if (len(starters)!=11 or not bench or len(names)!=len(set(names))
                    or len(starters+bench)!=len(set(starters+bench)) or set(names)!=set(starters+bench)):
                return None
            if set(starters)!={_name(p['player']['name']) for p in players if p['is_substitute'] is False}:
                return None
            for p in players:
                seen_ids.append(p['player']['id'])
                if p['player']['id']==pid:
                    if side!=own[0] or p.get('is_substitute') is not True:return None
                    target=_name(p['player']['name'])
            if target and side==own[0] and target not in bench:return None
        if (target is None or any(type(i) is not int or i<=0 for i in seen_ids)
                or len(seen_ids)!=len(set(seen_ids))):return None
        sections=[n.parent for n in nodes if n.has('m-MatchDetails-history-title') and n.text()=='Wechsel']
        if len(sections)!=1:return None
        entered=[];left=[]
        for event in sections[0].nodes():
            if not event.has('m-MatchDetails-history-event-text--'+own[0]):continue
            for segment in event.nodes():
                if not segment.has('m-MatchDetails-history-event-text-segment'):continue
                icons=[n for n in segment.nodes() if n.has('m-MatchDetails-icon-caret-right--green')
                       or n.has('m-MatchDetails-icon-caret-right--red')]
                links=[n for n in segment.nodes() if n.tag=='a']
                if len(icons)!=1 or len(links)!=1:return None
                (entered if icons[0].has('m-MatchDetails-icon-caret-right--green') else left).append(_name(links[0].text()))
        if (len(entered)!=3 or len(entered)!=len(left) or len(entered)!=len(set(entered))
                or len(left)!=len(set(left))
                or any(n not in sheets['Auswechselspieler'][own[0]] for n in entered)
                or any(n not in sheets['Aufstellung'][own[0]] for n in left)
                or target in entered or target in left):return None
        return {'appearances':0,'goals':0,'source_url':url,'match_id':row['id'],
                'player_id':pid,'team_id':team_id,'entered_players':entered,
                'proof':'independent_dfb_roster_and_exhausted_three_substitution_limit'}
    except (KeyError,TypeError,ValueError,AttributeError,RecursionError):return None
