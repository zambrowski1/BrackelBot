# SPDX-License-Identifier: MIT
import copy
import json
from unittest.mock import Mock
import pytest
import requests
from wiki_stats.errors import UpdateError
from wiki_stats.json_loader import load_package
from wiki_stats.schema_validator import validate_package
from wiki_stats.wiki_client import WikiClient
from wiki_stats.change_planner import plan_package
from wiki_stats.audit_logger import AuditLogger, make_report, audit_report


@pytest.mark.parametrize('case', ['negative', 'bool', 'missing_source', 'future_as_of', 'unknown', 'unequal_fields', 'duplicate_id', 'fake_verified'])
def test_invalid_input(package, case):
    op = package['articles'][0]['operations'][0]
    if case == 'negative': op['new']['goals'] = -1
    if case == 'bool': op['new']['goals'] = True
    if case == 'missing_source': op['sources'] = []
    if case == 'future_as_of': op['as_of'] = '2030-01-01'
    if case == 'unknown': op['surprise'] = 1
    if case == 'unequal_fields': del op['new']['goals']
    if case == 'duplicate_id': package['articles'][0]['operations'].append(copy.deepcopy(op))
    if case == 'fake_verified': op['sources'][0]['provenance'] = 'independently_verified'
    with pytest.raises(UpdateError): validate_package(package)
    client = Mock()
    with pytest.raises(UpdateError): plan_package(package, client)
    client.fetch_page.assert_not_called()


def test_loader_bom_and_duplicate_key(tmp_path, package):
    f = tmp_path/'data.json'
    f.write_text(json.dumps(package, ensure_ascii=False), encoding='utf-8-sig')
    assert load_package(f) == package
    f.write_text('{"a":1,"a":2}', encoding='utf-8')
    with pytest.raises(UpdateError, match='Повторный ключ'): load_package(f)


def session_with(data=None, status=200, exception=None):
    session = Mock()
    session.headers = {}
    if exception:
        session.get.side_effect = exception
    else:
        response = Mock(status_code=status)
        response.json.return_value = data
        session.get.return_value = response
    return session


def test_api_read_only_revision_and_protected_page(monkeypatch):
    monkeypatch.setattr('wiki_stats.wiki_client.time.sleep', lambda _: None)
    data = {'query': {'pages': [{'title': 'Игрок', 'ns': 0, 'protection': [{'type': 'edit', 'level': 'sysop'}],
                  'revisions': [{'revid': 42, 'timestamp': '2026-10-08T00:00:00Z', 'slots': {'main': {'content': 'text'}}}]}]}}
    session = session_with(data)
    p = WikiClient(session).fetch_page('Игрок')
    assert p.revid == 42
    assert p.text == 'text'
    params = session.get.call_args.kwargs['params']
    assert params['action'] == 'query'
    session.post.assert_not_called()


@pytest.mark.parametrize('status,code', [(401, 'authorization_error'), (403, 'authorization_error'), (429, 'rate_limited')])
def test_api_access_errors(monkeypatch, status, code):
    monkeypatch.setattr('wiki_stats.wiki_client.time.sleep', lambda _: None)
    session = session_with({}, status)
    with pytest.raises(UpdateError) as exc: WikiClient(session).fetch_page('Игрок')
    assert exc.value.code == code
    assert session.get.call_count == 1


@pytest.mark.parametrize('data,code', [({'error': {'code': 'badvalue', 'info': 'bad'}}, 'mediawiki_error'),
    ({'query': {'pages': [{'missing': True}]}}, 'page_not_found'),
    ({'query': {'pages': [{'redirect': True}]}}, 'redirect_not_supported'),
    ({'query': {'pages': [{'ns': 1}]}}, 'namespace_not_supported'), ({}, 'invalid_api_response')])
def test_api_structural_errors(monkeypatch, data, code):
    monkeypatch.setattr('wiki_stats.wiki_client.time.sleep', lambda _: None)
    with pytest.raises(UpdateError) as exc: WikiClient(session_with(data)).fetch_page('Игрок')
    assert exc.value.code == code


def test_connection_retries_bounded(monkeypatch):
    monkeypatch.setattr('wiki_stats.wiki_client.time.sleep', lambda _: None)
    session = session_with(exception=requests.Timeout('timeout'))
    with pytest.raises(UpdateError) as exc: WikiClient(session).fetch_page('Игрок')
    assert exc.value.code == 'connection_error'
    assert session.get.call_count == 3


def test_auth_failure_stops_batch(package):
    second = copy.deepcopy(package['articles'][0])
    second['title'] = 'Другая статья'
    second['operations'][0]['id'] = 'other'
    package['articles'].append(second)
    client = Mock()
    client.fetch_page.side_effect = UpdateError('authorization_error', 'stop')
    plans, failures = plan_package(package, client)
    assert not plans
    assert len(failures) == 1
    assert client.fetch_page.call_count == 1


def test_report_and_audit(tmp_path, client, package):
    plans, failures = plan_package(package, client)
    report = make_report(package, plans, failures)
    assert report['publishing_enabled'] is False
    assert report['articles'][0]['changes'][0]['status'] == 'ready'
    logger = AuditLogger(tmp_path/'audit.jsonl')
    audit_report(logger, report)
    entry = json.loads(logger.path.read_text(encoding='utf-8'))
    assert entry['event'] == 'change_checked'


def test_only_zambrowski_testbot_user_namespace_allowed(monkeypatch):
    monkeypatch.setattr('wiki_stats.wiki_client.time.sleep', lambda _: None)
    rev = {'revid': 155489454, 'timestamp': '2026-10-08T10:14:55Z', 'slots': {'main': {'content': 'test'}}}
    sandbox = {'query': {'pages': [{'title': 'Участник:Zambrowski/testbot', 'ns': 2, 'revisions': [rev]}]}}
    assert WikiClient(session_with(sandbox)).fetch_page('Участник:Zambrowski/testbot').text == 'test'
    other = {'query': {'pages': [{'title': 'Участник:Other/test', 'ns': 2, 'revisions': [rev]}]}}
    with pytest.raises(UpdateError) as exc:
        WikiClient(session_with(other)).fetch_page('Участник:Other/test')
    assert exc.value.code == 'namespace_not_supported'


def test_sandbox_title_json_schema_exception(package):
    package['articles'][0]['title'] = 'Участник:Zambrowski/testbot'
    validate_package(package)
    package['articles'][0]['title'] = 'Участник:Other/test'
    with pytest.raises(UpdateError):
        validate_package(package)
