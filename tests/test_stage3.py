# SPDX-License-Identifier: MIT
from dataclasses import replace
from copy import deepcopy
from unittest.mock import Mock
import json
import pytest
import requests
from wiki_stats.models import Mode
from wiki_stats.errors import UpdateError
from wiki_stats.change_planner import plan_article
from wiki_stats.publisher import Publisher
from wiki_stats.transactions import approve_change,TEST_TITLE
from wiki_stats.backup import save_backup,plan_restore
from wiki_stats.audit_logger import AuditLogger
from wiki_stats.wiki_client import WikiClient
from stage23_helpers import *


def planned(ops=None,page=None):
    pkg=package(ops or [stats_operation()])
    return plan_article(page or snapshot(),pkg['articles'][0],'1.1',FakeResolver())


def publisher(tmp_path):
    return Publisher(AuditLogger(tmp_path/'audit.jsonl'),tmp_path/'backups')


def approve_all(plan):
    for c in plan.changes:
        if c.status=='ready':approve_change(plan,c.id)


def test_manual_publication_one_save_atomic_group(tmp_path):
    plan=planned(transfer());approve_all(plan)
    client=MemoryClient()
    result=publisher(tmp_path).publish(plan,client,mode=Mode.MANUAL,resolver=FakeResolver())
    assert len(client.writes)==1
    assert client.writes[0][1]==plan.preview
    assert all(c.status=='published' for c in plan.changes)
    assert not plan.publishable
    backup=json.loads(__import__('pathlib').Path(result['backup']).read_text(encoding='utf-8'))
    assert backup['text']==MINI
    assert 'add_club' in client.writes[0][2]


def test_partial_group_approval_never_writes(tmp_path):
    plan=planned(transfer());approve_change(plan,'close');client=MemoryClient()
    with pytest.raises(UpdateError) as err:publisher(tmp_path).publish(plan,client,mode=Mode.MANUAL,resolver=FakeResolver())
    assert err.value.code=='approval_required'
    assert not client.writes and client.auth_checks==0


def test_wrong_group_never_publishes_good_member(tmp_path):
    ops=transfer();ops[-1]['expected']='Wrong'
    plan=planned(ops);client=MemoryClient()
    with pytest.raises(UpdateError):approve_change(plan,'close')
    plan.changes[0].decision='approved'
    with pytest.raises(UpdateError):publisher(tmp_path).publish(plan,client,mode=Mode.MANUAL)
    assert not client.writes


@pytest.mark.parametrize('title',['Кляйндинст, Тим','Участник:Other/testbot','Участник:Zambrowski/testbot/subpage'])
def test_publisher_hard_whitelist_before_any_network(tmp_path,title):
    plan=planned();plan.snapshot=replace(plan.snapshot,title=title)
    client=Mock()
    with pytest.raises(UpdateError) as err:publisher(tmp_path).publish(plan,client,mode=Mode.MANUAL)
    assert err.value.code=='page_not_allowed'
    client.fetch_page.assert_not_called();client.save_test_page.assert_not_called()


@pytest.mark.parametrize('mode',[Mode.DRY_RUN,Mode.AUTOMATIC])
def test_no_write_outside_manual(tmp_path,mode):
    plan=planned();approve_all(plan);client=MemoryClient()
    with pytest.raises(UpdateError) as err:publisher(tmp_path).publish(plan,client,mode=mode)
    assert err.value.code=='publishing_disabled' and not client.writes


@pytest.mark.parametrize('change',['revision','text'])
def test_revision_or_text_change_requires_new_approval(tmp_path,change):
    plan=planned();approve_all(plan)
    fresh=replace(snapshot(),revid=101) if change=='revision' else snapshot(MINI+'another edit')
    client=MemoryClient(fresh)
    with pytest.raises(UpdateError) as err:publisher(tmp_path).publish(plan,client,mode=Mode.MANUAL)
    assert err.value.code=='revision_conflict'
    assert not client.writes and not plan.publishable
    assert plan.changes[0].decision=='pending'


def test_tampered_plan_never_writes(tmp_path):
    plan=planned();approve_all(plan);plan.preview+='tamper';client=MemoryClient()
    with pytest.raises(UpdateError) as err:publisher(tmp_path).publish(plan,client,mode=Mode.MANUAL)
    assert err.value.code=='plan_modified' and not client.writes


def test_unapproved_independent_operation_excluded(tmp_path):
    ops=[stats_operation(),team_operation()]
    plan=planned(ops);approve_change(plan,'stats');client=MemoryClient()
    publisher(tmp_path).publish(plan,client,mode=Mode.MANUAL,resolver=FakeResolver())
    assert len(client.writes)==1
    assert '| 5 (2)<ref' in client.writes[0][1]
    assert 'до 21' not in client.writes[0][1]


def test_edit_conflict_or_unknown_response_stops_and_keeps_backup(tmp_path):
    for code in ['revision_conflict','publication_unknown','authorization_error']:
        plan=planned();approve_all(plan);client=MemoryClient();client.failure=UpdateError(code,'Stop')
        with pytest.raises(UpdateError) as err:publisher(tmp_path).publish(plan,client,mode=Mode.MANUAL)
        assert err.value.code==code and not plan.publishable
        assert plan.changes[0].review_fingerprint==''
    assert len(list((tmp_path/'backups').glob('*.json')))==3


def test_legacy_package_manual_publication_compatible(tmp_path):
    pkg=package([stats_operation()]);article=pkg['articles'][0]
    plan=plan_article(snapshot(),article,'1.0');approve_all(plan);client=MemoryClient()
    publisher(tmp_path).publish(plan,client,mode=Mode.MANUAL)
    assert len(client.writes)==1


def test_restore_review_revision_and_backup(tmp_path):
    backup=save_backup(snapshot(),tmp_path/'initial')
    client=MemoryClient(snapshot(MINI.replace('4 (1)','5 (2)'),101))
    plan=plan_restore(client,backup)
    assert plan.kind=='restore' and plan.preview==MINI
    with pytest.raises(UpdateError):publisher(tmp_path).publish(plan,client,mode=Mode.MANUAL)
    approve_all(plan)
    publisher(tmp_path).publish(plan,client,mode=Mode.MANUAL)
    assert client.writes[0][1]==MINI


def test_restore_invalid_title_or_hash_refused(tmp_path):
    path=save_backup(snapshot(),tmp_path)
    data=json.loads(path.read_text(encoding='utf-8'));data['text']+='tamper'
    path.write_text(json.dumps(data),encoding='utf-8')
    client=Mock()
    with pytest.raises(UpdateError):plan_restore(client,path)
    client.fetch_page.assert_not_called()


def mock_session(responses):
    session=Mock();session.headers={}
    queue=[]
    for data in responses:
        r=Mock(status_code=200);r.json.return_value=data;queue.append(r)
    session.get.side_effect=queue
    session.post.return_value=Mock(status_code=200)
    return session


def test_botpassword_session_no_credentials_on_disk(monkeypatch,tmp_path):
    monkeypatch.setattr('wiki_stats.wiki_client.time.sleep',lambda _:None)
    session=mock_session([{'query':{'tokens':{'logintoken':'LOGIN-SECRET'}}},
                          {'query':{'userinfo':{'id':12,'name':'Zambrowski'}}}])
    session.post.return_value.json.return_value={'login':{'result':'Success'}}
    client=WikiClient(session)
    assert client.login_botpassword('Zambrowski@BrackelBot','PRIVATE-BOT-PASSWORD')=='Zambrowski'
    assert not hasattr(client,'password') and not hasattr(client,'token')
    assert session.post.call_args.kwargs['data']['lgpassword']=='PRIVATE-BOT-PASSWORD'
    assert not list(tmp_path.iterdir())
    client.logout_local();assert client.authenticated_user is None


def test_auth_error_hides_password_and_server_response(monkeypatch):
    monkeypatch.setattr('wiki_stats.wiki_client.time.sleep',lambda _:None)
    session=mock_session([{'query':{'tokens':{'logintoken':'LOGIN-SECRET'}}}])
    session.post.return_value.json.return_value={'login':{'result':'Failed','reason':'PRIVATE-BOT-PASSWORD'}}
    client=WikiClient(session)
    with pytest.raises(UpdateError) as err:client.login_botpassword('Zambrowski@BrackelBot','PRIVATE-BOT-PASSWORD')
    assert 'PRIVATE' not in str(err.value) and client.authenticated_user is None


def test_action_edit_baserevid_assertions_and_csrf(monkeypatch):
    monkeypatch.setattr('wiki_stats.wiki_client.time.sleep',lambda _:None)
    session=mock_session([{'query':{'userinfo':{'id':12,'name':'Zambrowski'}}},
                          {'query':{'tokens':{'csrftoken':'CSRF-SECRET'}}}])
    session.post.return_value.json.return_value={'edit':{'result':'Success','newrevid':101}}
    client=WikiClient(session);client.authenticated_user='Zambrowski'
    result=client.save_test_page(snapshot(),'NEW TEXT','BrackelBot: update_stats (тест)')
    payload=session.post.call_args.kwargs['data']
    assert payload['baserevid']==100 and payload['basetimestamp']==snapshot().timestamp
    assert payload['starttimestamp']==snapshot().starttimestamp
    assert payload['assert']=='user' and payload['assertuser']=='Zambrowski'
    assert payload['token']=='CSRF-SECRET' and payload['nocreate']==1
    assert result['newrevid']==101 and session.post.call_count==1


def test_wiki_client_write_hard_gate(monkeypatch):
    session=Mock();session.headers={};client=WikiClient(session)
    with pytest.raises(UpdateError):client.save_test_page(replace(snapshot(),title='Тестовая статья',namespace=0),'text','summary')
    session.get.assert_not_called();session.post.assert_not_called()


@pytest.mark.parametrize('error,expected',[('editconflict','revision_conflict'),('assertuserfailed','authorization_error'),
                                          ('protectedpage','edit_rejected'),('badtoken','authorization_error')])
def test_api_edit_errors_no_retry(monkeypatch,error,expected):
    monkeypatch.setattr('wiki_stats.wiki_client.time.sleep',lambda _:None)
    session=mock_session([{'query':{'userinfo':{'id':12,'name':'Zambrowski'}}},
                          {'query':{'tokens':{'csrftoken':'CSRF'}}}])
    session.post.return_value.json.return_value={'error':{'code':error,'info':'do not log server text'}}
    client=WikiClient(session);client.authenticated_user='Zambrowski'
    with pytest.raises(UpdateError) as err:client.save_test_page(snapshot(),'TEXT','summary')
    assert err.value.code==expected and session.post.call_count==1


def test_edit_timeout_ambiguous_never_retried(monkeypatch):
    monkeypatch.setattr('wiki_stats.wiki_client.time.sleep',lambda _:None)
    session=mock_session([{'query':{'userinfo':{'id':12,'name':'Zambrowski'}}},{'query':{'tokens':{'csrftoken':'CSRF'}}}])
    session.post.side_effect=requests.Timeout('should not leak body')
    client=WikiClient(session);client.authenticated_user='Zambrowski'
    with pytest.raises(UpdateError) as err:client.save_test_page(snapshot(),'TEXT','summary')
    assert err.value.code=='publication_unknown' and session.post.call_count==1
