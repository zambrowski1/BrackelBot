# SPDX-License-Identifier: MIT
from copy import deepcopy
from datetime import date
from stage23_helpers import MINI,AUGSBURG,MemoryClient
from wiki_stats.models import PageSnapshot
from wiki_stats.api_football_client import utcnow

TITLE='Тестовый футболист'
PLAYER={'id':990001,'name':'Synthetic Player','firstname':'Synthetic','lastname':'Player','birth':{'date':'1990-01-01'}}
MAPPING={'api_id':990001,'qid':'Q990001','title':TITLE,'wiki_name':'Кальвин Браккельман',
         'birth_date':'1990-01-01','api_name':'Synthetic Player','club_ids':[15755],
         'monitoring':'active','verified_at':utcnow(),'last_confirmed_statistics':{}}
ANCHOR={'team_id':15755,'season':2026,'period':'2026—{{н.в.}}','career_wikitext':AUGSBURG,
        'career':{'appearances':4,'goals':1},'season_stats':{'appearances':4,'goals':1},
        'as_of':date.today().isoformat(),'evidence_url':'https://example.org/verified-test-fixture'}


def player_row(appearances=5,goals=2,pid=990001,tid=15755):
    return {'player':{**PLAYER,'id':pid},'statistics':[{'team':{'id':tid,'name':'Augsburg'},
            'league':{'id':78,'season':2026},'games':{'appearences':appearances,'lineups':999},'goals':{'total':goals}}]}


def dataset(rows=None):
    return {'complete':True,'league':78,'season':2026,'response':rows or [player_row()],
            'fetched_at':utcnow(),'pages':1}


class ServerWiki(MemoryClient):
    def __init__(self):
        super().__init__()
        self.saved=[]
        self.current=PageSnapshot(TITLE,100,'2026-10-08T00:00:00Z',MINI.replace('2025/26','2026/27'),0,utcnow())
        self.publication_policy=None
    def fetch_page(self,title): return self.current
    def get_wikidata_entity(self,qid):
        if qid=='Q990001':
            return {'sitelinks':{'ruwiki':{'title':TITLE}},'claims':{
              'P31':[{'mainsnak':{'snaktype':'value','datavalue':{'value':{'id':'Q5'}}}}],
              'P569':[{'mainsnak':{'snaktype':'value','datavalue':{'value':{'time':'+1990-01-01T00:00:00Z','precision':11}}}}]}}
        return {'sitelinks':{'ruwiki':{'title':'Аугсбург (футбольный клуб)'}},'claims':{}}
    def get_page_identity(self,title,namespace=0):
        return {'title':title,'qid':'Q990001' if title==TITLE else 'Q15755'}
    def save_allowed_page(self,snapshot,text,summary):
        self.publication_policy.gate_page(snapshot.title)
        self.saved.append((snapshot,text,summary))
        self.current=PageSnapshot(snapshot.title,snapshot.revid+1,utcnow(),text,snapshot.namespace,utcnow())
        return {'title':snapshot.title,'oldrevid':snapshot.revid,'newrevid':snapshot.revid+1}


class ServerApi:
    def __init__(self,rows=None): self.rows=rows or [player_row()];self.used=[]
    def status(self): return {'requests':{'current':0,'limit_day':100},'subscription':{'active':True}}
    def leagues(self,season): return {'data':{'response':[{'league':{'id':78},'seasons':[{'year':season,'coverage':{'players':True}}]}]}}
    def teams(self,season): return {'data':{'response':[{'team':{'id':15755,'name':'Augsburg','national':False}}]}}
    def players(self,season): return dataset(self.rows)
    def squads(self,team_id,fresh=False): return {'data':{'response':[{'team':{'id':team_id},'players':[{'id':row['player']['id']} for row in self.rows]}]}}
    def transfers(self,player_id,fresh=False): return {'data':{'response':[]}}
    def player(self,player_id,season): return dataset(self.rows)


def seed(store):
    store.put('players',990001,deepcopy(MAPPING))
    store.put('clubs',15755,{'api_id':15755,'name':'Augsburg','qid':'Q15755','title':'Аугсбург (футбольный клуб)','verified_at':utcnow()})
    store.put('anchors','990001:15755:2026',deepcopy(ANCHOR))


def community(store):
    store.put('policy','kill_switch',False)
    store.put('policy','community_approval',{'community_approved':True,'operator_enabled':True,
              'approval_url':'https://ru.wikipedia.org/wiki/Википедия:ТЕСТ','approved_at':utcnow(),
              'league':78,'seasons':[2026],'titles':[TITLE],'operations':['update_stats']})
