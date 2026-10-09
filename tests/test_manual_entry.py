# SPDX-License-Identifier: MIT
from dataclasses import replace
import json
import pytest
from wiki_stats.manual_entry import article_rows, build_manual_package, load_article_snapshot
from wiki_stats.change_planner import plan_article
from wiki_stats.change_validator import arithmetic_warnings, references
from wiki_stats.errors import UpdateError


def build(snapshot, row, games=None, goals=None, **kwargs):
    return build_manual_package(snapshot, row, row.appearances + 1 if games is None else games,
                                row.goals + 1 if goals is None else goals,
                                kwargs.get('source_url', 'https://example.org/stats'),
                                kwargs.get('as_of', '2026-10-05'), 'Синтетический тест', bundesliga=True)


def test_season_form_updates_totals_and_preserves_references(snapshot):
    player, rows, warnings = article_rows(snapshot)
    assert player == 'Тим Кляйндинст' and not warnings
    row = next(r for r in rows if r.structure == 'club_table' and r.period == '2026/27' and r.category == 'Чемпионат')
    package = build(snapshot, row)
    plan = plan_article(snapshot, package['articles'][0], package['schema_version'])
    assert plan.changes[0].status == 'ready'
    assert '{{КлСтат/Сезон|2026/27|4|1|' in plan.preview
    assert not arithmetic_warnings(plan.preview)
    assert references(plan.preview) == references(snapshot.text)
    assert package['articles'][0]['base_revid'] == snapshot.revid
    assert not plan.publishable


def test_national_team_is_separate_from_club_statistics(snapshot):
    _, rows, _ = article_rows(snapshot)
    row = next(r for r in rows if r.field == 'национальная сборная' and r.period.startswith('2024'))
    package = build(snapshot, row)
    plan = plan_article(snapshot, package['articles'][0], '1.1')
    assert plan.changes[0].status == 'ready'
    assert '|7 (5)' in plan.preview
    assert '{{КлСтат/Сезон|2026/27|3|0|' in plan.preview


@pytest.mark.parametrize('kwargs', [dict(source_url=''), dict(source_url='file:///stats'),
                                    dict(as_of=''), dict(as_of='2026-99-99'), dict(as_of='2099-01-01')])
def test_source_and_coverage_required(snapshot, kwargs):
    row = article_rows(snapshot)[1][0]
    with pytest.raises(UpdateError):
        build(snapshot, row, **kwargs)


@pytest.mark.parametrize('games,goals', [(-1, 0), (0, -1), (True, 0), (1.5, 0), (100001, 0)])
def test_invalid_numbers_rejected(snapshot, games, goals):
    with pytest.raises(UpdateError):
        build(snapshot, article_rows(snapshot)[1][0], games, goals)


def test_changed_revision_blocks_manual_plan(snapshot):
    package = build(snapshot, article_rows(snapshot)[1][0])
    plan = plan_article(replace(snapshot, revid=snapshot.revid + 1), package['articles'][0], '1.1')
    assert plan.changes[0].status == 'revision_conflict' and not plan.diff


def test_unknown_number_is_skipped_with_warning(snapshot):
    broken = replace(snapshot, text=snapshot.text.replace('6 (4)', '? (?)'))
    _, rows, warnings = article_rows(broken)
    assert warnings
    assert not any(r.field == 'национальная сборная' and r.period.startswith('2024') for r in rows)


def test_cannot_use_row_from_another_snapshot(snapshot):
    row = replace(article_rows(snapshot)[1][0], appearances=999)
    with pytest.raises(UpdateError, match='отсутствует'):
        build(snapshot, row)


def test_same_values_make_no_diff(snapshot):
    row = article_rows(snapshot)[1][0]
    package = build(snapshot, row, row.appearances, row.goals)
    plan = plan_article(snapshot, package['articles'][0], '1.1')
    assert plan.changes[0].status == 'already_applied' and not plan.diff


def test_load_only_selected_snapshot_and_preserve_newlines(tmp_path, snapshot):
    metadata = tmp_path/'article.json'
    metadata.write_text(json.dumps({'title': snapshot.title, 'revid': snapshot.revid, 'timestamp': snapshot.timestamp}), encoding='utf-8')
    with metadata.with_suffix('.wiki').open('w', encoding='utf-8', newline='') as stream:
        stream.write(snapshot.text)
    (tmp_path/'unrelated.json').write_text('{}', encoding='utf-8')
    loaded = load_article_snapshot(metadata)
    assert loaded == snapshot


def test_scope_must_be_declared_for_real_article(snapshot):
    row = article_rows(snapshot)[1][0]
    with pytest.raises(UpdateError, match='Бундеслига'):
        build_manual_package(snapshot, row, 10, 0, 'https://example.org/stats', '2026-10-05')
