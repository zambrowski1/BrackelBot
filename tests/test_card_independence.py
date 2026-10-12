from datetime import datetime, timezone
import pytest
from tests.test_sofascore import API, Wiki, Resolver, MINI, snapshot
from tests.test_season_aggregate_mode import MAPPING
from wiki_stats.errors import UpdateError
from wiki_stats.sofascore_cycle import prepare_player
from wiki_stats.wikitext_parser import WikitextParser

NOW = datetime(2026,10,9,tzinfo=timezone.utc)


def text():
    return MINI.split('{{КлСтат|')[0].replace('| имя =','| дата рождения = 22.08.1999\n| имя =')+'== Примечания ==\n'


def current_card(plan):
    value=WikitextParser(plan.preview).career('клубы',MAPPING['wiki_name'])[-1][2]
    return [n.value for n in value.numeric(pair=True)]


def test_unavailable_optional_table_does_not_block_card(monkeypatch):
    def unavailable(*args): raise UpdateError('source_unavailable','Missing optional table source')
    monkeypatch.setattr('wiki_stats.statistics_section.create_section',unavailable)
    _, plan, _ = prepare_player(snapshot(text()),MAPPING,API(),Wiki(),Resolver(),statistics_since=0,now=NOW)
    assert current_card(plan)==[5,2]
    assert any(c.status=='source_unavailable' for c in plan.changes)
    assert any(g.id=='sofascore-card-club' and g.status=='ready' for g in plan.groups)


def test_card_numbers_roll_back_when_their_date_fails(monkeypatch):
    from wiki_stats import structural_editor
    execute = structural_editor.execute_operation
    def fail_date(value,player,op,resolver=None):
        if op['type']=='update_date': raise UpdateError('old_value_mismatch','Changed date')
        return execute(value,player,op,resolver)
    monkeypatch.setattr(structural_editor,'execute_operation',fail_date)
    _, plan, _ = prepare_player(snapshot(text()),MAPPING,API(),Wiki(),Resolver(),statistics_since=0,now=NOW)
    assert current_card(plan)==[4,1]
    assert any(g.id=='sofascore-card-club' and g.status=='blocked' for g in plan.groups)


def test_missing_infobox_name_is_shared_without_patch_conflicts():
    value=text().replace('| имя = Кальвин Браккельман\n','')
    _, plan, _ = prepare_player(snapshot(value),MAPPING,API(),Wiki(),Resolver(),statistics_since=0,now=NOW)
    assert current_card(plan)==[5,2]
    assert not any(c.status=='patch_conflict' for c in plan.changes)
    from wiki_stats.change_planner import plan_article
    for group in plan.groups:
        if group.status!='ready': continue
        operations=[op for op in plan.article['operations'] if op['id'] in group.change_ids]
        single=plan_article(plan.snapshot,{**plan.article,'operations':operations},'1.1',Resolver())
        assert single.groups[0].patches==group.patches


def test_malformed_klstat_does_not_prevent_card_collection():
    broken=text()+'{{КлСтат|Чемпионат}}\n{{КлСтат/Сезон|2026/27|x}}\n'
    _,plan,evidence=prepare_player(snapshot(broken),MAPPING,API(),Wiki(),Resolver(),statistics_since=0,now=NOW)
    assert current_card(plan)==[5,2]
    assert any(i['code']=='club_table_unusable' for i in evidence['review'])
