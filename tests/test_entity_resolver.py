# SPDX-License-Identifier: MIT
from copy import deepcopy
import pytest
from wiki_stats.entity_resolver import EntityResolver
from wiki_stats.errors import UpdateError


CATALOGUE=[{'name':'Боруссия Мёнхенгладбах','title':'Боруссия (футбольный клуб, Мёнхенгладбах)',
            'qid':'Q101959','aliases':['Borussia Mönchengladbach'],'country_qid':'Q183','flag':'Германии'},
           {'name':'Боруссия Дортмунд','title':'Боруссия (футбольный клуб, Дортмунд)',
            'qid':'Q41420','aliases':['Borussia Dortmund'],'country_qid':'Q183','flag':'Германии'}]


class IdentityClient:
    def __init__(self):
        self.objects={c['qid']:{'id':c['qid'],'sitelinks':{'ruwiki':{'title':c['title']}},'claims':{'P17':[{'mainsnak':{'datavalue':{'value':{'id':'Q183'}}}}]}} for c in CATALOGUE}
        self.objects['Q99921']={'sitelinks':{'ruwiki':{'title':'Сборная Германии по футболу (до 21 года)'}},
                                'claims':{'P17':[{'mainsnak':{'datavalue':{'value':{'id':'Q183'}}}}]}}
        self.calls=[]

    def get_wikidata_entity(self,qid):
        self.calls.append(('wikidata',qid))
        if qid not in self.objects:raise UpdateError('entity_not_found','No entity')
        return self.objects[qid]

    def get_page_identity(self,title,namespace=0):
        self.calls.append(('wiki',title))
        if namespace==10:return {'title':title,'qid':None}
        for qid,obj in self.objects.items():
            if obj.get('sitelinks',{}).get('ruwiki',{}).get('title')==title:return {'title':title,'qid':qid}
        raise UpdateError('entity_not_found','No page')


def test_borussia_identity_exact_qid_and_live_page():
    client=IdentityClient();resolver=EntityResolver(client,CATALOGUE)
    result=resolver.resolve({'name':'Borussia Mönchengladbach','kind':'club'})
    assert result.qid=='Q101959' and 'Мёнхенгладбах' in result.wikitext
    assert ('wiki','Шаблон:Флаг Германии') in client.calls


def test_borussia_ambiguous_or_wrong_object_refused():
    resolver=EntityResolver(IdentityClient(),CATALOGUE)
    with pytest.raises(UpdateError) as err:resolver.resolve({'name':'Боруссия','kind':'club'})
    assert err.value.code=='entity_ambiguous'
    with pytest.raises(UpdateError) as err:resolver.resolve({'name':'Borussia Mönchengladbach','kind':'club','wikidata_id':'Q41420'})
    assert err.value.code=='entity_mismatch'


def test_nonexistent_article_or_sitelink_refused():
    client=IdentityClient();client.objects['Q101959']['sitelinks']={}
    with pytest.raises(UpdateError) as err:EntityResolver(client,CATALOGUE).resolve({'name':'Borussia Mönchengladbach','kind':'club'})
    assert err.value.code=='entity_not_found'


def test_national_age_and_flag_checked():
    resolver=EntityResolver(IdentityClient(),CATALOGUE)
    result=resolver.resolve({'name':'Германия U21','kind':'national_team','wikidata_id':'Q99921','age':21})
    assert result.age==21 and 'до 21 года' in result.wikitext
    with pytest.raises(UpdateError) as err:resolver.resolve({'name':'Германия U20','kind':'national_team','wikidata_id':'Q99921','age':20})
    assert err.value.code=='entity_mismatch'
    with pytest.raises(UpdateError):resolver.resolve({'name':'Германия U21','kind':'national_team','wikidata_id':'Q99921','age':21,'flag':'Франции'})


def test_foreign_club_verified_from_qid_and_country():
    client=IdentityClient()
    client.objects['Q901']={'sitelinks':{'ruwiki':{'title':'Аустрия (футбольный клуб, Вена)'}},'claims':{
        'P31':[{'mainsnak':{'datavalue':{'value':{'id':'Q476028'}}}}],
        'P17':[{'mainsnak':{'datavalue':{'value':{'id':'Q40'}}}}]}}
    result=EntityResolver(client,CATALOGUE).resolve({'name':'Austria Vienna','kind':'club','wikidata_id':'Q901'})
    assert result.flag=='Австрии'
    assert result.display=='Аустрия (Вена)'
    assert '[[Аустрия (футбольный клуб, Вена)|Аустрия (Вена)]]' in result.wikitext


def test_unknown_flag_country_or_nonfootball_object_refused():
    client=IdentityClient();client.objects['Q902']={'sitelinks':{'ruwiki':{'title':'Не футбол'}},'claims':{}}
    with pytest.raises(UpdateError) as err:EntityResolver(client,CATALOGUE).resolve({'name':'Не футбол','kind':'club','wikidata_id':'Q902'})
    assert err.value.code=='unsupported_entity'


@pytest.mark.parametrize('country,title,flag',[('Q408','Австралия','Австралии'),('Q262','Алжир','Алжира'),('Q148','Китай','Китая')])
def test_country_flags_outside_the_initial_small_country_list(country,title,flag):
    client=IdentityClient()
    client.objects[country]={'sitelinks':{'ruwiki':{'title':title}}}
    client.objects['Q901']={'sitelinks':{'ruwiki':{'title':'Новый клуб'}},'claims':{
        'P31':[{'mainsnak':{'datavalue':{'value':{'id':'Q476028'}}}}],
        'P17':[{'mainsnak':{'datavalue':{'value':{'id':country}}}}]}}
    result=EntityResolver(client,[]).resolve({'name':'Новый клуб','kind':'club','wikidata_id':'Q901'})
    assert result.flag==flag and '{{Флаг '+flag+'|20px}}' in result.wikitext
    assert ('wiki','Шаблон:Флаг '+flag) in client.calls


def test_reserve_team_uses_verified_football_subclass_and_its_own_country():
    client=IdentityClient()
    client.objects['Q103229495']={'claims':{'P279':[{'mainsnak':{'datavalue':{'value':{'id':'Q15944511'}}}}]}}
    client.objects['Q16525419']={'sitelinks':{'ruwiki':{'title':'Майнц 05 II'}},'claims':{
       'P31':[{'mainsnak':{'datavalue':{'value':{'id':'Q103229495'}}}}],
       'P17':[{'mainsnak':{'datavalue':{'value':{'id':'Q183'}}}}]}}
    result=EntityResolver(client,[]).resolve({'name':'Майнц 05 II','kind':'club','wikidata_id':'Q16525419'})
    assert result.flag=='Германии' and result.qid=='Q16525419'
