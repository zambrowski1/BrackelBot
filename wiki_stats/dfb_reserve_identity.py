# SPDX-License-Identifier: MIT
"""Keep a DFB reserve team distinct when Wikipedia redirects to its parent."""
import mwparserfromhell as mw
from urllib.parse import urljoin, urlsplit
from .dfb_evidence import _Document
from .errors import UpdateError

PARENT = 'https://datencenter.dfb.de/datencenter/vereine/fc-energie-cottbus-e-v'
TEAM = 'https://datencenter.dfb.de/teams/energie-cottbus-ii'


def reserve_reference(text):
    code = mw.parse(text)
    translations = [t for t in code.filter_templates() if str(t.name).strip().casefold()=='нп5']
    if code.filter_wikilinks() or code.filter_external_links() or code.filter_tags() or len(translations)!=1: return False
    flags = [t for t in code.filter_templates() if str(t.name).strip().casefold()=='флаг германии']
    if len(flags)>1: return False
    for template in code.filter_templates():
        if template not in translations and str(template.name).strip().casefold()!='флаг германии': return False
    if code.strip_code().strip().strip('«»').strip(): return False
    template = translations[0]
    try:
        return len(template.params)==4 and [str(template.get(str(i)).value).strip() for i in range(1,5)]==[
            'Энерги II','Энерги II','en','FC Energie Cottbus II']
    except ValueError:
        return False


def energie_reserve_identity(scope, bundle, wiki):
    def fail():
        raise UpdateError('dfb_reserve_identity_unproven','Не подтверждена отдельная резервная команда DFB')
    try:
        if (scope.get('team_slug')!='energie-cottbus-ii' or scope.get('team')!='Energie Cottbus II'
                or type(scope.get('source_team_id')) is not int or scope['source_team_id']!=546): fail()
        profiles = bundle['reserve_identity_profiles']
        if not isinstance(profiles,list) or len(profiles)!=2: fail()
        documents = {}
        for profile in profiles:
            if profile['url'] not in {PARENT,TEAM} or profile['url'] in documents: fail()
            if not isinstance(profile['html'],str) or len(profile['html'])>2_000_000: fail()
            doc=_Document();doc.feed(profile['html']);documents[profile['url']]=doc
        parent = documents[PARENT]; team = documents[TEAM]
        if sum(n.tag=='h2' and n.text()=='FC Energie Cottbus' for n in parent.root.nodes())!=1: fail()
        if sum(n.tag=='h4' and n.text()=='Energie Cottbus II (Männer)' for n in parent.root.nodes())!=1: fail()
        if sum(n.tag=='h2' and n.text()=='Energie Cottbus II' for n in team.root.nodes())!=1: fail()
        back = [urlsplit(urljoin(TEAM,n.attrs.get('href',''))) for n in team.root.nodes()
                if n.tag=='a' and n.text()=='Zurück zur Vereinsseite']
        if (len(back)!=1 or back[0].scheme!='https' or back[0].netloc!='datencenter.dfb.de'
                or back[0].path!='/clubs/fc-energie-cottbus-e-v'): fail()
        ru=wiki.get_page_identity('Энерги')
        en=wiki.get_foreign_page_identity('FC Energie Cottbus','en')
        if ru!={'title':'Энерги','qid':'Q107818'} or en!={'title':'FC Energie Cottbus','qid':'Q107818'}: fail()
        return {'identity_key':'dfb-546','source_entity_id':'dfb:546','name':'Энерги II',
                'parent_wikidata_id':'Q107818','sources':[PARENT,TEAM]}
    except UpdateError: raise
    except (KeyError,TypeError,ValueError,AttributeError): fail()


def freiburg_reserve_identity(scope, bundle, wiki):
    def fail():
        raise UpdateError('dfb_reserve_identity_unproven','Не подтверждена мужская резервная команда «Фрайбурга»')
    parent_url='https://datencenter.dfb.de/clubs/sc-freiburg-e-v?datacenter_name=datencenter'
    team_url='https://datencenter.dfb.de/teams/sc-freiburg-ii'
    try:
        if (scope.get('team_slug')!='sc-freiburg-ii' or scope.get('team')!='SC Freiburg II'
                or type(scope.get('source_team_id')) is not int or scope['source_team_id']!=1641): fail()
        profiles=bundle['freiburg_reserve_identity_profiles']
        if not isinstance(profiles,list) or len(profiles)!=2: fail()
        documents={}
        for p in profiles:
            if p['url'] not in {parent_url,team_url} or p['url'] in documents: fail()
            if not isinstance(p['html'],str) or len(p['html'])>2_000_000: fail()
            doc=_Document();doc.feed(p['html']);documents[p['url']]=doc
        parent=documents[parent_url];team=documents[team_url]
        if sum(n.tag=='h2' and n.text()=='SC Freiburg' for n in parent.root.nodes())!=1: fail()
        if sum(n.tag=='h4' and n.text()=='SC Freiburg II (Männer)' for n in parent.root.nodes())!=1: fail()
        if sum(n.tag=='h2' and n.text()=='SC Freiburg II' for n in team.root.nodes())!=1: fail()
        back=[urlsplit(urljoin(team_url,n.attrs.get('href',''))) for n in team.root.nodes()
              if n.tag=='a' and n.text()=='Zurück zur Vereinsseite']
        if (len(back)!=1 or back[0].scheme!='https' or back[0].netloc!='datencenter.dfb.de'
                or back[0].path!='/clubs/sc-freiburg-e-v'): fail()
        checks=[(wiki.get_page_identity('Фрайбург II'),'Фрайбург II','Q20631356'),
                (wiki.get_foreign_page_identity('SC Freiburg II','en'),'SC Freiburg II','Q20631356'),
                (wiki.get_page_identity('Фрайбург (футбольный клуб)'),'Фрайбург (футбольный клуб)','Q106394'),
                (wiki.get_foreign_page_identity('SC Freiburg','en'),'SC Freiburg','Q106394')]
        if any(value!={'title':title,'qid':qid} for value,title,qid in checks): fail()
        return {'identity_key':'Q20631356','wikidata_id':'Q20631356','source_entity_id':'dfb:1641',
                'parent_wikidata_id':'Q106394','name':'Фрайбург II','sources':[parent_url,team_url]}
    except UpdateError: raise
    except (KeyError,TypeError,ValueError,AttributeError): fail()
