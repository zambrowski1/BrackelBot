# SPDX-License-Identifier: MIT
"""Prove remaining empty categories from an official, ended club spell."""
import re
from datetime import date
from .dfb_evidence import _Document
from .sofascore_match_stats import played_matches

TOTAL_URL='https://www.kaagent.be/nl/nieuws/18-06-2021/tim-kleindienst-keert-terug-naar-fc-heidenheim'
LOAN_URL='https://www.kaagent.be/nl/nieuws/29-01-2021/kleindienst-uitgeleend-aan-fc-heidenheim'
RECEIVING_URL='https://www.fc-heidenheim.de/news/detail/vor-der-bundesliga-premiere-tim-kleindienst-verlangert-vorzeitig-bis-2027?cHash=0c584ceb8dfc2a171ab0340983a2a805'

def _receiving_club(proof,apps):
    source=proof.get('receiving_club')
    if not isinstance(source,dict) or source.get('url')!=RECEIVING_URL:return False
    text=source.get('html')
    if not isinstance(text,str) or not text or len(text)>2_000_000:return False
    doc=_Document();doc.feed(text);nodes=list(doc.root.nodes())
    titles=[n.attrs.get('content') for n in nodes if n.tag=='meta' and n.attrs.get('property')=='og:title']
    if titles!=['Vor der Bundesliga-Premiere: Tim Kleindienst verlängert vorzeitig bis 2027! | 1. FC Heidenheim 1846 e. V.']:return False
    paragraphs=[' '.join(n.text().split()) for n in nodes if n.tag=='p']
    statement=f'Dort kam er in der ersten Halbserie in der Liga sowie in der Champions League-Qualifikation und in der Europa League auf insgesamt {apps} Einsätze.'
    return sum(statement in p and 'Tim Kleindienst blickt' in p and 'zum belgischen Erstligisten KAA Gent.' in p
               and 'Sein dritter Wechsel zum FCH folgte schließlich im Januar 2021 zunächst als Leihe, bevor im Sommer 2021 die feste Verpflichtung folgte.' in p for p in paragraphs)==1

def _paragraphs(source,url,title,day):
    if not isinstance(source,dict) or source.get('url')!=url:return None
    text=source.get('html')
    if not isinstance(text,str) or not text or len(text)>2_000_000:return None
    doc=_Document();doc.feed(text);nodes=list(doc.root.nodes())
    for key,value in [('og:url',url),('og:site_name','KAA Gent')]:
        if [n.attrs.get('content') for n in nodes if n.tag=='meta' and n.attrs.get('property')==key]!=[value]:return None
    headings=[n.text().strip() for n in nodes if n.tag=='h1' and n.attrs.get('itemprop')=='headline']
    days=[n.text().strip() for n in nodes if n.tag=='h2' and n.attrs.get('itemprop')=='datePublished']
    if headings!=[title] or days!=[day]:return None
    return [' '.join(n.text().split()) for n in nodes if n.tag=='p']

def gent_closed_spell(mapping,records,history,api,now):
    if (mapping.get('api_id'),mapping.get('qid'))!=(228981,'Q15627096'):
        return None
    if now.date()<date(2023,8,18) or history.get('complete') is not True:return None
    if not hasattr(api,'club_career_secondary_source'):return None
    proof=api.club_career_secondary_source(mapping['api_id'])
    if not isinstance(proof,dict):return None
    total=_paragraphs(proof.get('total'),TOTAL_URL,'Tim Kleindienst keert terug naar FC Heidenheim','18-06-2021')
    loan=_paragraphs(proof.get('loan'),LOAN_URL,'Tim Kleindienst uitgeleend aan FC Heidenheim','29-01-2021')
    if total is None or loan is None:return None
    intro=[p for p in total if p.startswith('In de zomer van 2020 arriveerde Tim Kleindienst in de Ghelamco Arena.')]
    if len(intro)!=1:return None
    count=re.fullmatch(r'In de zomer van 2020 arriveerde Tim Kleindienst in de Ghelamco Arena\. Gedurende een half seizoen vond de boomlange Duitse spits in (\d+) wedstrijden (\d+) keer de weg naar doel\. Daarsnaast gaf hij ook 1 assist\.',intro[0])
    ended='In januari 2021 werd hij uitgeleend aan zijn vorige club, FC Heidenheim. Na een uitleenbeurt van zes maanden maakt hij nu definitief de overstap. De club wenst Tim veel succes toe in zijn verdere carrière.'
    if not count or total.count(ended)!=1 or loan.count('Tim Kleindienst wordt tot 30 juni uitgeleend aan FC Heidenheim. Het betreft een uitleenbeurt met aankooptie.')!=1:return None
    if not _receiving_club(proof,int(count[1])):return None
    selected=[r for r in records if r.get('season')=='2020/21' and r.get('team_id')==2903]
    if (len(selected)!=3 or {r['tournament_id'] for r in selected}!={38,7,679}
            or any(r['resolved'].qid!='Q18232' for r in selected)):return None
    if any(type(r['numbers'].get(k)) is not int or r['numbers'][k]<0 for r in selected for k in ('appearances','goals')):return None
    if (sum(r['numbers']['appearances'] for r in selected),sum(r['numbers']['goals'] for r in selected))!=tuple(map(int,count.groups())):return None
    ids=[]
    for r in selected:
        rows=played_matches(history,r,now,league=r['tournament_id']==38,european=r['tournament_id']!=38)
        if rows is None or len(rows)!=r['numbers']['appearances']:return None
        ids.extend(m['id'] for m in rows)
    if len(ids)!=len(set(ids)):return None
    all_ids=[m.get('id') for m in history.get('matches',[])]
    if any(type(i) is not int or i<=0 for i in all_ids) or len(all_ids)!=len(set(all_ids)):return None
    for m in history['matches']:
        p=m.get('player') or {}
        if p.get('team_id')!=2903:continue
        if (m.get('tournament') or {}).get('id')==853:continue
        if m.get('is_finished') is not True or (m.get('status') or {}).get('type')!='finished':continue
        minutes=(p.get('statistics') or {}).get('minutes_played')
        if m['id'] not in ids and not (type(minutes) in (int,float) and minutes==0 or minutes is None and p.get('is_on_bench') is True):return None
        if m['id'] in ids and not '2020-07-01'<=m.get('start_time','')[:10]<'2021-01-29':return None
    return {'qid':'Q18232','season':'2020/21','numbers':{'appearances':0,'goals':0},
            'statistics_as_of':'2021-06-30','sources':[TOTAL_URL,LOAN_URL,RECEIVING_URL],
            'confirmed_match_ids':ids,'official_total':{'appearances':int(count[1]),'goals':int(count[2])}}
