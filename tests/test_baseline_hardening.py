# SPDX-License-Identifier: MIT
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from wiki_stats.errors import UpdateError
from wiki_stats.api_provider import make_api
from wiki_stats.json_loader import load_json
from wiki_stats.klstat_planner import prepare_table
from wiki_stats.statistics_engine import validate_anchor
from test_statistics_section import evidence, resolver, BARE
from stage23_helpers import snapshot


@pytest.mark.parametrize('provider,code',[('paused','provider_paused'),('misspelled','provider_invalid')])
def test_internal_provider_never_falls_back(provider,code):
    with pytest.raises(UpdateError) as exc:make_api(SimpleNamespace(provider=provider))
    assert exc.value.code==code


def test_unwrapped_store_obeys_paused_environment(monkeypatch):
    monkeypatch.setenv('BRACKELBOT_API_PROVIDER','paused')
    with pytest.raises(UpdateError) as exc:make_api(SimpleNamespace())
    assert exc.value.code=='provider_paused'


@pytest.mark.parametrize('alias',['{{нв}}','{{НВ}}','{{н.в.}}'])
def test_anchor_accepts_current_period_alias(alias):
    anchor={'team_id':1,'season':2026,'period':'2023—'+alias,'career_wikitext':'[[Клуб]]',
            'career':{'appearances':20,'goals':1},'season_stats':{'appearances':4,'goals':1},
            'as_of':'2026-10-04','evidence_url':'https://example.org/source'}
    validate_anchor(anchor)


@pytest.mark.parametrize('content',['{"player":"A","player":"B"}','{"number":NaN}'])
def test_evidence_json_rejects_duplicate_keys_and_nonfinite_values(tmp_path,content):
    path=tmp_path/'input.json';path.write_text(content,encoding='utf-8')
    with pytest.raises(UpdateError) as exc:load_json(path)
    assert exc.value.code=='invalid_input'


@pytest.mark.parametrize('field',['player','sources','as_of'])
def test_bad_evidence_rejected_before_identity_requests(field):
    data=evidence();del data[field];r=resolver()
    with pytest.raises(UpdateError) as exc:prepare_table(snapshot(BARE),data,r)
    assert exc.value.code=='invalid_input'
    r.client.get_page_identity.assert_not_called()


def test_same_league_is_verified_once_per_section():
    data=evidence();row=deepcopy(data['clubs'][0]['rows'][0]);row['season']='2026/27'
    data['clubs'][0]['rows'].append(row);r=resolver()
    _,plan=prepare_table(snapshot(BARE),data,r)
    assert plan.changes[0].status=='ready'
    assert r.client.get_page_identity.call_count==1
