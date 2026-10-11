from dataclasses import replace
from unittest.mock import Mock
import pytest
from wiki_stats.test_pages import TEST_TITLES, is_test_page,TEST_PLAYER_BINDINGS,bound_test_player
from wiki_stats.models import PageSnapshot, Mode
from wiki_stats.schema_validator import validate_package
from wiki_stats.change_planner import plan_article
from wiki_stats.publisher import Publisher
from wiki_stats.transactions import approve_change
from wiki_stats.wiki_client import WikiClient
from wiki_stats.backup import save_backup, plan_restore
from wiki_stats.errors import UpdateError
from test_sbmatches_editor import package, text
from stage23_helpers import MemoryClient


@pytest.mark.parametrize('title',TEST_TITLES)
def test_each_sandbox_plan_publish_backup_restore(title,tmp_path):
    pkg=package('add_sbmatch')
    article=pkg['articles'][0]
    article['title']=title
    validate_package(pkg)
    page=PageSnapshot(title,10,'2026-02-02T00:00:00Z',text(),2)
    plan=plan_article(page,article,'1.1')
    assert plan.publishable
    approve_change(plan,'match-1')
    client=MemoryClient(page)
    Publisher(backup_dir=tmp_path).publish(plan,client,mode=Mode.MANUAL)
    assert client.writes[0][0].title==title
    backup=save_backup(page,tmp_path)
    modified=replace(page,text=plan.preview,revid=11)
    restore=plan_restore(MemoryClient(modified),backup)
    assert restore.snapshot.title==title
    assert restore.preview==page.text


@pytest.mark.parametrize('title',[
    'Участник:Zambrowski/testbot6','Участник:Zambrowski/testbot0',
    'Участник:Zambrowski/testbot1/subpage','Участник:Other/testbot1',
    'Участник:Zambrowski/testbot11','Тестовая статья'])
def test_nearby_pages_never_write(title):
    assert not is_test_page(title)
    session=Mock();session.headers={}
    client=WikiClient(session)
    with pytest.raises(UpdateError):
        client.save_test_page(PageSnapshot(title,1,'2026-01-01T00:00:00Z','x',2),'y','test')
    session.get.assert_not_called()
    session.post.assert_not_called()


def test_wrong_namespace_blocked(tmp_path):
    page=PageSnapshot(TEST_TITLES[1],1,'2026-01-01T00:00:00Z','x',0)
    with pytest.raises(UpdateError): save_backup(page,tmp_path)
    session=Mock();session.headers={}
    with pytest.raises(UpdateError): WikiClient(session).save_test_page(page,'y','test')
    session.get.assert_not_called()


@pytest.mark.parametrize('title',list(TEST_PLAYER_BINDINGS))
def test_operator_player_binding_is_exact(title):
    mapping={**TEST_PLAYER_BINDINGS[title],'birth_date':'1999-01-01'}
    assert bound_test_player(title,mapping)
    for field in ('wiki_name','title','qid','api_id'):
        wrong={**mapping,field:'wrong'}
        assert not bound_test_player(title,wrong)
    assert not bound_test_player(title+'/copy',mapping)


def test_missing_birthday_in_exact_operator_copy_is_not_filled(monkeypatch):
    from tests.test_sofascore import Wiki,API,Resolver
    from tests.stage23_helpers import MINI,snapshot
    from wiki_stats.sofascore_cycle import prepare_player
    from datetime import datetime,timezone
    mapping={'api_id':1,'qid':'Q114406918','title':'Игрок','wiki_name':'Кальвин Браккельман','birth_date':'1999-08-22'}
    title='Участник:Zambrowski/testbot4'
    monkeypatch.setitem(TEST_PLAYER_BINDINGS,title,{k:v for k,v in mapping.items() if k!='birth_date'})
    page=replace(snapshot(MINI.replace('2025/26','2026/27')),title=title)
    _,plan,evidence=prepare_player(page,mapping,API(),Wiki(),Resolver(),now=datetime(2026,10,9,tzinfo=timezone.utc))
    assert plan.diff and 'дата рождения' not in plan.preview
    assert any(r['code']=='sandbox_operator_binding' for r in evidence['review'])
    conflicting=replace(page,text=page.text.replace('| имя =','| дата рождения = 21.08.1999\n| имя ='))
    with pytest.raises(UpdateError) as exc:
        prepare_player(conflicting,mapping,API(),Wiki(),Resolver(),now=datetime(2026,10,9,tzinfo=timezone.utc))
    assert exc.value.code=='identity_mismatch'
    unbound=replace(page,title='Участник:Zambrowski/testbot1')
    with pytest.raises(UpdateError):
        prepare_player(unbound,mapping,API(),Wiki(),Resolver(),now=datetime(2026,10,9,tzinfo=timezone.utc))
