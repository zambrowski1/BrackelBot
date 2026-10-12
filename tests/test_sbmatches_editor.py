from copy import deepcopy
import pytest
from wiki_stats.models import PageSnapshot
from wiki_stats.change_planner import plan_article
from wiki_stats.schema_validator import validate_package
from wiki_stats.errors import UpdateError

TEAM = '[[Сборная Германии по футболу (до 18 лет)|Германию U18]]'
ROW = {'дата':'01-01-2026','соперник':'Ирландия','счёт':'2:1','голы':'0','соревнование':'Товарищеский матч'}


def package(kind='update_sbmatch', compact=True):
    op = {'id':'match-1','type':kind,'entity':{'name':'Германия U18','kind':'national_team','wikitext':TEAM},
          'target':{'structure':'sbmatches','age':18,'match_index':1 if kind == 'update_sbmatch' else 2},
          'as_of':'2026-02-01','sources':[{'url':'https://example.org/match','coverage_as_of':'2026-02-01','provenance':'user_provided'}],
          'payload':{'match':{**ROW,'голы':'1'} if kind == 'update_sbmatch' else {**ROW,'дата':'02-01-2026','соперник':'Франция'}}}
    if kind == 'update_sbmatch': op['payload']['expected_match'] = deepcopy(ROW)
    return {'schema_version':'1.1','package_id':'test-sbmatches','generated_at':'2026-02-02T00:00:00Z','is_test':True,
            'articles':[{'title':'Участник:Zambrowski/testbot','player':'Тест','base_revid':10,'operations':[op]}]}


def text(compact=True):
    row = '|матч1=01-01-2026 ;; Ирландия ;; 2:1 ;; 0\n' if compact else ''.join('|'+f+'1='+ROW[f]+'\n' for f in ROW)
    return '{{Футболист|имя=Тест}}\nДо <ref name="a">Источник</ref>\n{{СбМатчи\n|игрок=Теста\n|сборная='+TEAM+'\n|возраст=18\n|обновлено=01-01-2026\n|соревнование=Товарищеский матч\n'+row+'|источник1=<ref name="a"/>\n}}\nПосле'


def plan(pkg, raw=None, revid=10):
    validate_package(pkg)
    return plan_article(PageSnapshot('Участник:Zambrowski/testbot',revid,'2026-02-02T00:00:00Z',raw or text(),2),pkg['articles'][0],'1.1')


@pytest.mark.parametrize('compact', [True, False])
@pytest.mark.parametrize('kind', ['update_sbmatch','add_sbmatch'])
def test_edit_formats_and_idempotence(compact, kind):
    pkg = package(kind)
    result = plan(pkg,text(compact))
    assert result.changes[0].status == 'ready'
    assert 'До <ref name="a">Источник</ref>' in result.preview
    assert '|источник1=<ref name="a"/>' in result.preview
    assert result.preview.endswith('После')
    assert '|обновлено=01-02-2026' in result.preview
    assert plan(pkg,result.preview).changes[0].status == 'already_applied'
    if kind == 'add_sbmatch':
        assert '|источник2=[https://example.org/match]' in result.preview
    elif compact:
        assert ';; 1 ;; Товарищеский матч' in result.preview
    else:
        assert '|голы1=1' in result.preview


@pytest.mark.parametrize('mutation,status', [
    ('age','target_not_found'), ('expected','old_value_mismatch'),
    ('goals','invalid_match'), ('chronology','invalid_match'), ('future','source_coverage_mismatch'),
    ('penalties','invalid_match'), ('injection','invalid_match')])
def test_reject_unsafe_changes(mutation,status):
    pkg=package('add_sbmatch' if mutation == 'chronology' else 'update_sbmatch')
    op=pkg['articles'][0]['operations'][0]
    if mutation=='age': op['target']['age']=19
    if mutation=='expected': op['payload']['expected_match']['голы']='2'
    if mutation=='goals': op['payload']['match']['голы']='3'
    if mutation=='chronology': op['payload']['match']['дата']='31-12-2025'
    if mutation=='future': op['payload']['match']['дата']='02-02-2026'
    if mutation=='penalties': op['payload']['match']['пенальти']='4:3'
    if mutation=='injection': op['payload']['match']['соперник']='{{Вредный}}'
    result=plan(pkg)
    assert result.changes[0].status==status
    assert result.preview==text()


def test_duplicate_template_and_parameter_and_gap():
    pkg=package()
    assert plan(pkg,text()+text()).changes[0].status=='unsupported_structure'  # two infoboxes
    raw=text().replace('|матч1=', '|матч2=').replace('|источник1=<ref name="a"/>\n', '')
    assert plan(pkg,raw).changes[0].status=='unsupported_structure'
    raw=text().replace('|возраст=18', '|возраст=18|возраст=18')
    assert plan(pkg,raw).changes[0].status=='ambiguous_target'
    raw=text()+text().split('До')[1]
    assert plan(pkg,raw).changes[0].status=='ambiguous_target'


def test_revision_and_schema():
    pkg=package()
    assert plan(pkg,revid=11).changes[0].status=='revision_conflict'
    del pkg['articles'][0]['base_revid']
    with pytest.raises(UpdateError): validate_package(pkg)
    pkg=package()
    del pkg['articles'][0]['operations'][0]['payload']['expected_match']
    with pytest.raises(UpdateError): validate_package(pkg)


def test_atomic_group_failure():
    pkg=package()
    first=pkg['articles'][0]['operations'][0]
    first['group_id']='both'
    second=deepcopy(first)
    second['id']='second'
    second['target']['age']=19
    pkg['articles'][0]['operations'].append(second)
    result=plan(pkg)
    assert result.preview==text()
    assert result.changes[0].status=='group_blocked'


def test_append_existing_match_rejected():
    pkg=package('add_sbmatch')
    pkg['articles'][0]['operations'][0]['payload']['match']=deepcopy(ROW)
    assert plan(pkg).changes[0].status=='duplicate_match'


def test_parameter_injection_rejected_and_link_allowed():
    pkg=package()
    pkg['articles'][0]['operations'][0]['payload']['match']['соперник']='Ирландия|цвета=Россия'
    assert plan(pkg).changes[0].status=='invalid_match'
    pkg['articles'][0]['operations'][0]['payload']['match']['соперник']='[[Ирландия|Ирландия]]'
    assert plan(pkg).changes[0].status=='ready'


def test_publisher_rechecks_and_saves(tmp_path):
    from wiki_stats.publisher import Publisher
    from wiki_stats.transactions import approve_change
    from wiki_stats.models import Mode
    result=plan(package('add_sbmatch'))
    approve_change(result,'match-1')
    class Client:
        def verify_authenticated(self): pass
        def fetch_page(self,title): return result.snapshot
        def save_test_page(self,snapshot,content,summary):
            self.content=content
            return {'newrevid':11}
    client=Client()
    saved=Publisher(backup_dir=tmp_path).publish(result,client,mode=Mode.MANUAL)
    assert saved['newrevid']==11
    assert client.content==result.preview
    assert result.changes[0].status=='published'


@pytest.mark.parametrize('compact', [True, False])
@pytest.mark.parametrize('kind', ['update_sbmatch', 'add_sbmatch'])
def test_keeper_statistic_edit_and_replay(compact, kind):
    pkg = package(kind)
    op = pkg['articles'][0]['operations'][0]
    for key in ('match', 'expected_match'):
        if key in op['payload']:
            row = op['payload'][key]
            row['пропущенные голы'] = row.pop('голы')
            row['счёт'] = '0:2'
    op['payload']['match']['дата'] = '01.01.2026' if kind == 'update_sbmatch' else '02.01.2026'
    raw = text(compact).replace('|возраст=18', '|возраст=18|статистика=пропущенные голы').replace('2:1', '0:2').replace('|голы1=', '|пропущенные голы1=')
    result = plan(pkg, raw)
    assert result.changes[0].status == 'ready'
    assert plan(pkg, result.preview).changes[0].status == 'already_applied'
    assert '|источник1=<ref name="a"/>' in result.preview
    if not compact:
        assert '|пропущенные голы' in result.preview and '|голы' not in result.preview
    op['payload']['match']['пропущенные голы'] = '3'
    assert plan(pkg, raw).changes[0].status == 'invalid_match'


@pytest.mark.parametrize('value', [None, '', '0'])
def test_empty_goal_payload_allowed_and_preserved(value):
    pkg = package()
    row = pkg['articles'][0]['operations'][0]['payload']['match']
    row.pop('голы')
    if value is not None:
        row['голы'] = value
    result = plan(pkg)
    assert result.changes[0].status in ('ready', 'already_applied')
    assert plan(pkg, result.preview).changes[0].status == 'already_applied'


def test_point_update_preserves_unspecified_extras_and_replays():
    raw=text().replace('|источник1=','|место1=[[Прага]]\n|примечание1=Сохранить\n|источник1=')
    pkg=package();pkg['articles'][0]['operations'][0]['payload']['expected_match'].update(место='[[Прага]]',примечание='Сохранить')
    result=plan(pkg,raw)
    assert result.changes[0].status=='ready'
    assert '|место1=[[Прага]]' in result.preview and '|примечание1=Сохранить' in result.preview
    assert plan(pkg,result.preview).changes[0].status=='already_applied'


def test_point_update_and_add_write_explicit_extras():
    pkg=package();pkg['articles'][0]['operations'][0]['payload']['match']['место']='[[Прага]]'
    result=plan(pkg)
    assert result.changes[0].status=='ready' and '|место1=[[Прага]]' in result.preview
    assert plan(pkg,result.preview).changes[0].status=='already_applied'
    pkg=package('add_sbmatch');pkg['articles'][0]['operations'][0]['payload']['match']['минуты']='90'
    result=plan(pkg)
    assert result.changes[0].status=='ready' and '|минуты2=90' in result.preview
    assert plan(pkg,result.preview).changes[0].status=='already_applied'
