# SPDX-License-Identifier: MIT
from copy import deepcopy
import json
import os
import subprocess
import sys
import pytest
from wiki_stats.state_store import StateStore
from wiki_stats.scheduler import CycleRunner,publish_plan
from wiki_stats.publication_policy import PublicationPolicy
from wiki_stats.operator_review import deserialize_plan,approve_stored
from wiki_stats.transactions import TEST_TITLE,approve_change
from wiki_stats.player_registry import PlayerRegistry
from wiki_stats.errors import UpdateError
from wiki_stats.server_cli import main
from server_helpers import seed,community,ServerApi,ServerWiki,player_row,ANCHOR,TITLE,PLAYER


@pytest.fixture
def store(tmp_path):
    value=StateStore(tmp_path/'state.sqlite')
    yield value
    value.close()


def prepare(store,mode='dry_run',api=None,wiki=None):
    seed(store);api=api or ServerApi();wiki=wiki or ServerWiki()
    report=CycleRunner(store,api,wiki,mode=mode).run()
    return report,wiki


def test_store_persists_and_lock_excludes_second_process(tmp_path):
    path=tmp_path/'state.sqlite';one=StateStore(path);two=StateStore(path)
    one.put('players',1,{'name':'Один'})
    assert two.get('players',1)['name']=='Один'
    with one.run_lock():
        one.put('runs','one',{'status':'running'})
        with pytest.raises(UpdateError) as exc:
            with two.run_lock(): pass
        assert exc.value.code=='run_in_progress'
    with two.run_lock(): two.put('runs','two',{'status':'done'})
    one.close();two.close();three=StateStore(path)
    assert len(three.items('runs'))==2;three.close()


def test_run_prepares_atomic_career_and_season_using_existing_editor(store):
    report,wiki=prepare(store)
    assert report['status']=='completed' and report['prepared_count']==1 and not wiki.saved
    plan=deserialize_plan(store.get('plans',report['plans'][0]['hash'])['plan'])
    assert len(plan.changes)==2 and len(plan.groups)==1
    assert '5 (2)' in plan.preview
    assert '{{КлСтат/Сезон|2026/27|5|2|0|0|0|0|0|0}}' in plan.preview
    assert '{{КлСтат/Итого|5|2|0|0|0|0|0|0}}' in plan.preview
    assert '<!-- статистика -->' in plan.preview and 'Текст 4 (1)' in plan.preview


def test_automatic_default_never_writes_main(store):
    report,wiki=prepare(store,mode='automatic')
    assert report['published_count']==0 and not wiki.saved
    assert report['status']=='stopped'


def test_automatic_mock_publication_after_explicit_approval(store,monkeypatch):
    community(store);monkeypatch.setenv('BRACKELBOT_PUBLICATION','1')
    report,wiki=prepare(store,mode='automatic')
    assert report['status']=='completed' and report['published_count']==1 and len(wiki.saved)==1
    assert store.items('backups') and store.items('edits')
    repeated=CycleRunner(store,ServerApi(),wiki,mode='automatic').run()
    assert repeated['prepared_count']==0 and repeated['published_count']==0 and len(wiki.saved)==1


def test_null_stats_no_plan_or_zero_substitution(store):
    report,wiki=prepare(store,api=ServerApi([player_row(None,0)]))
    assert not report['plans'] and report['review_count'] and not wiki.saved


def test_missing_anchor_goes_to_review(store):
    wiki=ServerWiki();report=CycleRunner(store,ServerApi(),wiki).run()
    assert report['player_count']==1 and report['prepared_count']==0
    assert any(x['kind']=='identity' for _,x in store.items('review'))


def test_roster_incomplete_stops_before_scope_change(store):
    api=ServerApi();api.squads=lambda _: {'data':{'response':[]}}
    report,wiki=prepare(store,api=api)
    assert report['status']=='stopped' and store.get('scope','2026') is None and not wiki.saved


def test_transfer_after_anchor_requires_review(store):
    api=ServerApi();api.transfers=lambda _: {'data':{'response':[{'player':{'id':990001},'transfers':[{'date':'2026-10-08','teams':{'out':{'id':15755},'in':{'id':15755}},'type':'Loan'}]}]}}
    seed(store);anchor=store.get('anchors','990001:15755:2026');anchor['as_of']='2026-10-07';store.put('anchors','990001:15755:2026',anchor)
    report=CycleRunner(store,api,ServerWiki()).run()
    assert report['prepared_count']==0 and any(x['reason']=='needs_review' for _,x in store.items('review'))


def test_main_policy_modes_page_and_task_allowlists(store):
    policy=PublicationPolicy(store,'automatic',enabled=lambda:True)
    with pytest.raises(UpdateError): policy.gate_page(TITLE)
    community(store);policy.gate_page(TITLE)
    with pytest.raises(UpdateError): policy.gate_page('Другой футболист')
    test=PublicationPolicy(store,'test',enabled=lambda:True);test.gate_page(TEST_TITLE)
    with pytest.raises(UpdateError): test.gate_page(TITLE)
    store.put('policy','kill_switch',True)
    with pytest.raises(UpdateError): test.gate_page(TEST_TITLE)


def test_revision_conflict_clears_persisted_approvals(store):
    report,wiki=prepare(store);community(store)
    key=report['plans'][0]['hash']
    plan=deserialize_plan(store.get('plans',key)['plan'])
    for c in plan.changes: approve_stored(store,key,c.id)
    from dataclasses import replace
    wiki.current=replace(wiki.current,revid=999)
    with pytest.raises(UpdateError) as exc:
        publish_plan(store,key,wiki,PublicationPolicy(store,'manual',enabled=lambda:True),source_api=ServerApi())
    assert exc.value.code=='revision_conflict' and not wiki.saved
    assert store.get('plans',key)['status']=='stale'
    assert all(c['decision']=='pending' for c in store.get('plans',key)['plan']['changes'])


def test_partial_group_approval_no_write(store):
    report,wiki=prepare(store);community(store);key=report['plans'][0]['hash']
    plan=deserialize_plan(store.get('plans',key)['plan']);approve_stored(store,key,plan.changes[0].id)
    with pytest.raises(UpdateError) as exc: publish_plan(store,key,wiki,PublicationPolicy(store,'manual',enabled=lambda:True),source_api=ServerApi())
    assert exc.value.code=='approval_required' and not wiki.saved


def test_changed_mapping_or_anchor_invalidates_source(store):
    report,_=prepare(store);community(store);key=report['plans'][0]['hash']
    plan=deserialize_plan(store.get('plans',key)['plan'])
    anchor=store.get('anchors','990001:15755:2026');anchor['period']='2025—{{н.в.}}';store.put('anchors','990001:15755:2026',anchor)
    with pytest.raises(UpdateError) as exc: PublicationPolicy(store,'automatic',enabled=lambda:True).gate_plan(plan)
    assert exc.value.code=='identity_changed'


def test_registry_checks_dob_and_prevents_id_collision(store):
    registry=PlayerRegistry(store);wiki=ServerWiki()
    registry.confirm(PLAYER,'Q990001',TITLE,'Кальвин Браккельман',wiki)
    with pytest.raises(UpdateError) as exc: registry.confirm({**PLAYER,'id':999},'Q990001',TITLE,'Кальвин Браккельман',wiki)
    assert exc.value.code=='registry_collision'
    with pytest.raises(UpdateError): registry.confirm({**PLAYER,'id':900,'birth':{'date':'1991-01-01'}},'Q990001',TITLE,'Кальвин Браккельман',wiki)


def test_cli_review_requires_seen_diff_and_disabled_approval(tmp_path,store):
    report,_=prepare(store);key=report['plans'][0]['hash']
    operation_id=store.get('plans',key)['plan']['changes'][0]['id']
    base=['--state',str(store.path)]
    assert main(base+['review-approve',key,operation_id,'--confirm'])==2
    assert main(base+['review-show',key])==0
    assert main(base+['review-approve',key,operation_id,'--confirm'])==0
    from pathlib import Path
    assert main(base+['approval-install',str(Path(__file__).parents[1]/'examples/server/community-approval.DISABLED.json'),'--confirm'])==2


def test_headless_cli_imports_no_qt():
    script='import sys; import wiki_stats.server_cli; assert not any(x.startswith("PySide") for x in sys.modules)'
    completed=subprocess.run([sys.executable,'-c',script],capture_output=True,text=True)
    assert completed.returncode==0,completed.stderr


def test_secrets_rejected_from_state(store):
    with pytest.raises(UpdateError): store.put('anything','oops',{'API_FOOTBALL_KEY':'secret'})
    assert store.get('anything','oops') is None


def test_source_changes_invalidate_old_review(store):
    report,wiki=prepare(store);community(store);key=report['plans'][0]['hash']
    plan=deserialize_plan(store.get('plans',key)['plan'])
    for c in plan.changes: approve_stored(store,key,c.id)
    with pytest.raises(UpdateError) as exc:
        publish_plan(store,key,wiki,PublicationPolicy(store,'manual',enabled=lambda:True),source_api=ServerApi([player_row(6,2)]))
    assert exc.value.code=='source_changed' and store.get('plans',key)['status']=='stale'
    assert not wiki.saved


def test_fixture_plans_are_never_published(store):
    report,wiki=prepare(store);key=report['plans'][0]['hash']
    item=store.get('plans',key);item['offline']=True;store.put('plans',key,item)
    with pytest.raises(UpdateError) as exc:
        publish_plan(store,key,wiki,PublicationPolicy(store,'test',enabled=lambda:True))
    assert exc.value.code=='offline_publication' and not wiki.saved


def test_unknown_table_stops_all_automatic_publication(store,monkeypatch):
    from dataclasses import replace
    wiki=ServerWiki();wiki.current=replace(wiki.current,text=wiki.current.text.replace('{{КлСтат/Сезон','{{НЕИЗВЕСТНЫЙ/Сезон'))
    community(store);monkeypatch.setenv('BRACKELBOT_PUBLICATION','1')
    report,_=prepare(store,mode='automatic',wiki=wiki)
    assert report['publication_blocked'] and report['published_count']==0 and not wiki.saved


def test_missing_ru_article_never_creates_mapping(store):
    wiki=ServerWiki();wiki.get_wikidata_entity=lambda _: {'claims':{},'sitelinks':{}}
    with pytest.raises(UpdateError) as exc:
        PlayerRegistry(store).confirm(PLAYER,'Q990001',TITLE,'Кальвин Браккельман',wiki)
    assert exc.value.code=='no_ru_article' and not store.items('players')


def test_departure_requires_confirmed_recent_transfer_not_absence(store):
    seed(store);mapping=store.get('players',990001);mapping['verified_at']='2026-10-07T00:00:00+00:00';store.put('players',990001,mapping)
    api=ServerApi([player_row(pid=990002)])
    api.transfers=lambda _: {'data':{'response':[{'player':{'id':990001},'transfers':[{'date':'2026-10-08','type':'Free','teams':{'out':{'id':15755},'in':{'id':888}}}]}]}}
    report=CycleRunner(store,api,ServerWiki()).run()
    assert report['departures']==1 and store.get('players',990001)['monitoring']=='departed'
    assert store.get('anchors','990001:15755:2026') is not None


def test_absence_without_transfer_does_not_remove_history_or_monitoring(store):
    seed(store);report=CycleRunner(store,ServerApi([player_row(pid=990002)]),ServerWiki()).run()
    assert report['departures']==0 and store.get('players',990001)['monitoring']=='active'


def test_policy_blocks_unapproved_operation_and_expired_source(store):
    report,_=prepare(store);community(store);key=report['plans'][0]['hash'];plan=deserialize_plan(store.get('plans',key)['plan'])
    approval=store.get('policy','community_approval');approval['operations']=['update_date'];store.put('policy','community_approval',approval)
    with pytest.raises(UpdateError) as exc: PublicationPolicy(store,'automatic',enabled=lambda:True).gate_plan(plan)
    assert exc.value.code=='task_not_approved'
    approval['operations']=['update_stats'];store.put('policy','community_approval',approval)
    evidence=store.get('plan_evidence',key);evidence['fetched_at']='2000-01-01T00:00:00+00:00';store.put('plan_evidence',key,evidence)
    with pytest.raises(UpdateError) as exc: PublicationPolicy(store,'automatic',enabled=lambda:True).gate_plan(plan)
    assert exc.value.code=='source_stale'


def test_fetch_wrong_title_is_blocked_even_if_names_match(store):
    from dataclasses import replace
    wiki=ServerWiki();wiki.current=replace(wiki.current,title='Другой игрок')
    report,_=prepare(store,wiki=wiki)
    assert not report['plans'] and any(e['status']=='identity_mismatch' for e in report['errors'])


def test_api_identity_changed_before_write_is_stale(store):
    report,wiki=prepare(store);community(store);key=report['plans'][0]['hash']
    plan=deserialize_plan(store.get('plans',key)['plan'])
    for c in plan.changes: approve_stored(store,key,c.id)
    row=deepcopy(player_row());row['player']['birth']['date']='1991-01-01'
    with pytest.raises(UpdateError) as exc:
        publish_plan(store,key,wiki,PublicationPolicy(store,'manual',enabled=lambda:True),source_api=ServerApi([row]))
    assert exc.value.code=='source_changed' and not wiki.saved


def test_run_lock_blocks_a_separate_python_process(store):
    script='''import sys
from wiki_stats.state_store import StateStore
from wiki_stats.errors import UpdateError
s=StateStore(sys.argv[1])
try:
    with s.run_lock(): sys.exit(8)
except UpdateError as exc:
    sys.exit(0 if exc.code=="run_in_progress" else 9)
'''
    with store.run_lock():
        result=subprocess.run([sys.executable,'-c',script,str(store.path)],capture_output=True,text=True,timeout=15)
        assert result.returncode==0,result.stderr
