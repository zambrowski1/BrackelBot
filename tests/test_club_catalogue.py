# SPDX-License-Identifier: MIT
import json
from importlib.resources import files
import pytest
from wiki_stats.entity_resolver import EntityResolver, reference_identity
from wiki_stats.errors import UpdateError


def catalogue():
    return json.loads(files('wiki_stats').joinpath('clubs.json').read_text(encoding='utf-8'))


def test_three_leagues_have_all_distinct_teams_and_verified_links():
    data=catalogue()
    current=[c for c in data['clubs'] if c.get('season')==2026]
    assert len(current)==56 and len({c['qid'] for c in current})==56
    assert [sum(c['league_tier']==tier for c in current) for tier in (1,2,3)]==[18,18,20]
    for club in current:
        assert club['sitelinks']['ru']==club['title']
        assert club['country_qid']=='Q183' and club['display_name']
        assert club['verified_at']==data['verified_at']
    reserve={c['qid'] for c in current if c.get('team_variant')=='reserve'}
    assert reserve=={'Q107421','Q16637410'}
    assert not reserve & {'Q4512','Q22707'}


FOREIGN={'name':'Тестовый клуб','title':'Тестовый клуб (футбольный клуб)','display_name':'Тестовый клуб',
         'qid':'Q999001','aliases':['Test FC'],'country_qid':'Q183','flag':'Германии'}


class ForeignWiki:
    def __init__(self):
        self.russian=False
        self.foreign_qid='Q999001'
        self.template_title='Шаблон:Не переведено'
    def get_wikidata_entity(self,qid):
        assert qid=='Q999001'
        return {'sitelinks':{'dewiki':{'title':'Test FC'},**({'ruwiki':{'title':FOREIGN['title']}} if self.russian else {})},'claims':{}}
    def get_page_identity(self,title,namespace=0):
        if namespace==10:
            return {'title':self.template_title if title=='Шаблон:Нп5' else title,'qid':None}
        if self.russian and title==FOREIGN['title']: return {'title':title,'qid':'Q999001'}
        raise UpdateError('entity_not_found','Нет русской статьи')
    def get_foreign_page_identity(self,title,language):
        assert (title,language)==('Test FC','de')
        return {'title':title,'qid':self.foreign_qid}


def test_foreign_article_uses_np5_and_prefers_new_russian_article():
    wiki=ForeignWiki()
    ref={'name':'Test FC','kind':'club','wikidata_id':'Q999001'}
    result=EntityResolver(wiki,[FOREIGN]).resolve(ref)
    assert result.wikitext=='{{Флаг Германии|20px}} {{нп5|Тестовый клуб (футбольный клуб)|Тестовый клуб|de|Test FC}}'
    wiki.russian=True
    russian=EntityResolver(wiki,[FOREIGN]).resolve(ref)
    assert russian.foreign_language is None
    assert '[[Тестовый клуб (футбольный клуб)|Тестовый клуб]]' in russian.wikitext


@pytest.mark.parametrize('problem',['wrong_qid','occupied_ru','wrong_template'])
def test_foreign_fallback_rejects_wrong_identity_or_template(problem):
    wiki=ForeignWiki()
    if problem=='wrong_qid': wiki.foreign_qid='Q999002'
    elif problem=='wrong_template': wiki.template_title='Шаблон:Другой'
    else:
        original=wiki.get_page_identity
        wiki.get_page_identity=lambda title,namespace=0: original(title,namespace) if namespace==10 else {'title':title,'qid':'Q999002'}
    with pytest.raises(UpdateError):
        EntityResolver(wiki,[FOREIGN]).resolve({'name':'Test FC','kind':'club'})


def test_np5_existing_row_identity_and_fake_foreign_link(monkeypatch):
    wiki=ForeignWiki()
    original=EntityResolver.__init__
    monkeypatch.setattr(EntityResolver,'__init__',lambda self,client:original(self,client,[FOREIGN]))
    text='{{Флаг Германии|20px}} {{нп5|Тестовый клуб (футбольный клуб)|Тестовый клуб|de|Test FC}}'
    assert reference_identity(wiki,text)=={'title':FOREIGN['title'],'qid':FOREIGN['qid']}
    with pytest.raises(UpdateError): reference_identity(wiki,text.replace('|de|','|https://evil.example|'))


def test_catalogue_display_names_and_explicit_ambiguous_qid():
    from test_entity_resolver import IdentityClient
    wiki=IdentityClient()
    result=EntityResolver(wiki).resolve({'name':'Боруссия','kind':'club','wikidata_id':'Q101959'})
    assert result.display=='Боруссия (Мёнхенгладбах)'
    assert '[[Боруссия (футбольный клуб, Мёнхенгладбах)|Боруссия (Мёнхенгладбах)]]' in result.wikitext


def test_new_club_can_be_added_with_verified_foreign_link():
    from stage23_helpers import transfer,package,snapshot
    from wiki_stats.change_planner import plan_article
    operations=transfer('Test FC','Q999001')
    article=package(operations)['articles'][0]
    plan=plan_article(snapshot(),article,'1.1',EntityResolver(ForeignWiki(),[FOREIGN]))
    assert all(c.status=='ready' for c in plan.changes)
    assert '{{нп5|Тестовый клуб (футбольный клуб)|Тестовый клуб|de|Test FC}}' in plan.preview
    assert len(plan.groups)==1


def test_reserve_cannot_be_added_as_primary_club():
    from stage23_helpers import transfer,MINI
    from wiki_stats.structural_editor import execute_operation
    class Wiki:
        def get_wikidata_entity(self,qid): return {'sitelinks':{'ruwiki':{'title':'Штутгарт II'}},'claims':{}}
        def get_page_identity(self,title,namespace=0): return {'title':title,'qid':None if namespace==10 else 'Q107421'}
    resolver=EntityResolver(Wiki())
    club=resolver.resolve({'name':'VfB Stuttgart II','kind':'club','wikidata_id':'Q107421'})
    assert club.team_variant=='reserve' and club.qid!='Q4512'
    with pytest.raises(UpdateError) as exc:
        execute_operation(MINI,'Кальвин Браккельман',transfer('VfB Stuttgart II','Q107421')[1],resolver)
    assert exc.value.code=='entity_mismatch'


def test_offline_club_list_does_not_require_state_or_api_key(monkeypatch,capsys):
    from wiki_stats import server_cli
    monkeypatch.setattr(server_cli.StateStore,'from_env',lambda:pytest.fail('No database should be opened'))
    assert server_cli.main(['club-list','--tier','3'])==0
    data=json.loads(capsys.readouterr().out)
    assert len(data)==20 and sum(c['team_variant']=='reserve' for c in data)==2
