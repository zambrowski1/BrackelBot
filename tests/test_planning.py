# SPDX-License-Identifier: MIT
import copy
from dataclasses import replace
import pytest
from wiki_stats.change_planner import plan_article, plan_package
from wiki_stats.change_validator import arithmetic_warnings, references, apply_patches
from wiki_stats.errors import UpdateError
from wiki_stats.models import Mode, PageSnapshot, Patch
from wiki_stats.publisher import Publisher
from wiki_stats.schema_validator import validate_package
from wiki_stats.wikitext_parser import WikitextParser


def test_single_number(snapshot, article):
    op = article['operations'][0]
    op['expected'] = {'goals': 0}
    op['new'] = {'goals': 1}
    plan = plan_article(snapshot, article)
    assert plan.changes[0].status == 'ready'
    assert len(plan.changes[0].patches) == 1
    assert '{{КлСтат/Сезон|2026/27|3|1|' in plan.preview
    assert not plan.publishable


def test_pair_and_remainder_exact(snapshot, article):
    plan = plan_article(snapshot, article)
    c = plan.changes[0]
    assert c.status == 'ready'
    assert len(c.patches) == 2
    assert references(snapshot.text) == references(plan.preview)
    # Reverse each authorized numeric patch and prove the entire text is identical.
    shift = 0
    reverse = []
    for p in sorted(c.patches, key=lambda p: p.start):
        reverse.append(Patch(p.start+shift, p.start+shift+len(p.after), p.after, p.before, p.field))
        shift += len(p.after)-len(p.before)
    assert apply_patches(plan.preview, reverse) == snapshot.text
    assert 'Итог клуба' in ' '.join(plan.warnings)


def career_operation(snapshot, article, field='национальная сборная'):
    op = article['operations'][0]
    row = WikitextParser(snapshot.text).career(field, article['player'])[-1]
    nums = row[2].numeric(pair=True)
    op['target'] = {'structure': 'career', 'field': field, 'period': row[0]}
    op['entity']['wikitext'] = row[1]
    op['season'] = None
    op['competition'] = {'name': 'Все матчи сборной' if field != 'клубы' else 'Чемпионат',
                         'category': 'all', 'scope': 'national_all' if field != 'клубы' else 'league'}
    op['expected'] = {'appearances': nums[0].value, 'goals': nums[1].value}
    op['new'] = {'appearances': nums[0].value+1, 'goals': nums[1].value+1}
    return op


def test_national_statistics(snapshot, article):
    career_operation(snapshot, article)
    plan = plan_article(snapshot, article)
    assert plan.changes[0].status == 'ready'
    assert '|7 (5)' in plan.preview
    assert '{{КлСтат/Сезон|2026/27|3|0|' in plan.preview


def test_career_league_scope(snapshot, article, package):
    career_operation(snapshot, article, 'клубы')
    package['articles'] = [article]
    validate_package(package)
    assert plan_article(snapshot, article).changes[0].status == 'ready'
    article['operations'][0]['competition']['scope'] = 'national_all'
    with pytest.raises(UpdateError):
        validate_package(package)


@pytest.mark.parametrize('name', ['Табакович, Харис', 'Фюллькруг, Никлас', 'Кляйндинст, Тим'])
def test_real_fixture_structures(client, package, name):
    snapshot = client.fetch_page(name)
    parser = WikitextParser(snapshot.text)
    player = next(t for t in parser.templates if t.name == 'футболист').values()['имя'].text.strip()
    row = parser.club_table().seasons[-1]
    article = package['articles'][0]
    article.update(title=name, player=player, base_revid=snapshot.revid)
    op = article['operations'][0]
    op['entity']['wikitext'] = row.club
    op['season'] = row.season
    op['expected'] = {'appearances': row.values['2'].numeric()[0].value, 'goals': row.values['3'].numeric()[0].value}
    op['new'] = {k: v+1 for k, v in op['expected'].items()}
    assert arithmetic_warnings(snapshot.text) == []
    plan = plan_article(snapshot, article)
    assert plan.changes[0].status == 'ready'
    for p in plan.changes[0].patches:
        assert snapshot.text[p.start:p.end] == p.before
    career_operation(snapshot, article)
    assert plan_article(snapshot, article).changes[0].status == 'ready'


@pytest.mark.parametrize('operation', ['add_season', 'add_competition', 'update_totals', 'update_date', 'add_club'])
def test_later_stage_operations_explicitly_disabled(snapshot, article, operation):
    article['operations'][0]['type'] = operation
    # add_club intentionally contains no transfer date; the stage gate rejects it.
    result = plan_article(snapshot, article)
    assert result.changes[0].status == 'operation_not_implemented'
    assert result.preview == snapshot.text


def test_competition_existing_category_requires_explicit_mapping(snapshot, article):
    op = article['operations'][0]
    op['competition'].update(name='Лига Европы', category='Еврокубки')
    op['expected'] = {'appearances': 0, 'goals': 0}
    op['new'] = {'appearances': 1, 'goals': 0}
    assert plan_article(snapshot, article).changes[0].status == 'mapping_required'
    op['competition']['mapping_rule'] = 'Итог категории Еврокубки; остальные соревнования категории — 0'
    assert plan_article(snapshot, article).changes[0].status == 'ready'


def test_new_category_demands_review(snapshot, article):
    article['operations'][0]['competition'].update(category='Кубок лиги', name='Кубок лиги')
    plan = plan_article(snapshot, article)
    assert plan.changes[0].status == 'structure_change_required'
    assert plan.preview == snapshot.text


def test_arithmetic_reports_real_mismatch(snapshot):
    parser = WikitextParser(snapshot.text)
    value = parser.club_table().grand_total[0].numeric()[0]
    changed = apply_patches(snapshot.text, [value.patch(value.value+1, 'appearances')])
    assert arithmetic_warnings(changed) == ['Итог карьеры не равен сумме сезонов']


def test_missing_season(snapshot, article):
    article['operations'][0]['season'] = '2099/00'
    assert plan_article(snapshot, article).changes[0].status == 'season_not_found'


def test_old_mismatch_is_atomic(snapshot, article):
    article['operations'][0]['expected']['goals'] = 99
    plan = plan_article(snapshot, article)
    assert plan.changes[0].status == 'old_value_mismatch'
    assert plan.preview == snapshot.text
    assert not plan.diff


def test_unknown_career_template(snapshot, article):
    career_operation(snapshot, article)
    code = WikitextParser(snapshot.text)
    box = next(t for t in code.templates if t.name == 'футболист')
    value = box.values()['национальная сборная']
    text = snapshot.text[:value.start]+value.text.replace('спортивная карьера', 'неизвестная карьера', 1)+snapshot.text[value.start+len(value.text):]
    assert plan_article(replace(snapshot, text=text), article).changes[0].status == 'unsupported_structure'


def test_unknown_table_template(snapshot, article):
    text = snapshot.text.replace('{{КлСтат/Лига|', '{{НестандартнаяЛига|', 1)
    assert plan_article(replace(snapshot, text=text), article).changes[0].status == 'unsupported_structure'


def test_revision_changed(snapshot, article):
    result = plan_article(replace(snapshot, revid=snapshot.revid+1), article)
    assert result.changes[0].status == 'revision_conflict'
    assert result.preview == snapshot.text


def test_repeated_apply(snapshot, article):
    first = plan_article(snapshot, article)
    second = plan_article(replace(snapshot, text=first.preview), article)
    assert second.changes[0].status == 'already_applied'
    assert not second.diff


def test_refs_comments_spacing_and_digit_length(snapshot, article):
    row = '{{КлСтат/Сезон|2026/27|3|0|0|0|0|0|0|0}}'
    decorated = '{{КлСтат/Сезон|2026/27| 3 <!-- comment -->|0<ref name="test">Число 0 и 3</ref>|0|0|0|0|0|0}}'
    text = snapshot.text.replace(row, decorated)
    assert text != snapshot.text
    article['operations'][0]['new'] = {'appearances': 10, 'goals': 12}
    plan = plan_article(replace(snapshot, text=text), article)
    assert plan.changes[0].status == 'ready'
    assert '| 10 <!-- comment -->|12<ref name="test">Число 0 и 3</ref>|' in plan.preview
    assert references(text) == references(plan.preview)


def test_main_and_reserve_distinct(client, package):
    p = client.fetch_page('Табакович, Харис')
    table = WikitextParser(p.text).club_table()
    main, reserve = table.seasons[0], next(s for s in table.seasons if 'Янг Бойз II' in s.club)
    assert main.club != reserve.club
    a = package['articles'][0]
    a.update(title=p.title, player='Харис Табакович', base_revid=p.revid)
    op = a['operations'][0]
    op['entity']['wikitext'], op['season'] = main.club, main.season
    op['expected'], op['new'] = {'goals': 0}, {'goals': 1}
    plan = plan_article(p, a)
    assert plan.changes[0].status == 'ready'
    assert len(plan.changes[0].patches) == 1


def test_overlapping_operations_rejected(snapshot, article):
    other = copy.deepcopy(article['operations'][0])
    other['id'] = 'duplicate-cell'
    article['operations'].append(other)
    plan = plan_article(snapshot, article)
    assert [c.status for c in plan.changes] == ['patch_conflict', 'patch_conflict']
    assert plan.preview == snapshot.text


def test_independent_operations_in_one_article(snapshot, article):
    second = copy.deepcopy(article['operations'][0])
    article['operations'].append(second)
    career_operation(snapshot, {'player': article['player'], 'operations': [second]})
    second['id'] = 'national'
    result = plan_article(snapshot, article)
    assert [c.status for c in result.changes] == ['ready', 'ready']
    assert '|7 (5)' in result.preview
    assert '|2026/27|4|1|' in result.preview


def test_source_coverage_conflict(snapshot, article):
    source = copy.deepcopy(article['operations'][0]['sources'][0])
    source['coverage_as_of'] = '2026-10-04'
    article['operations'][0]['sources'].append(source)
    assert plan_article(snapshot, article).changes[0].status == 'source_coverage_mismatch'


def test_source_never_marked_independently_verified(snapshot, article):
    result = plan_article(snapshot, article).changes[0]
    assert result.source_verification == 'user_provided_not_independently_verified'


def test_different_player_rejected(snapshot, article):
    article['player'] = 'Другой игрок'
    assert plan_article(snapshot, article).changes[0].status == 'player_mismatch'


def test_duplicate_parameters_rejected(snapshot, article):
    text = snapshot.text.replace('{{КлСтат/Сезон|2026/27|3|0|', '{{КлСтат/Сезон|2=9|2026/27|3|0|')
    assert plan_article(replace(snapshot, text=text), article).changes[0].status == 'ambiguous_target'


def test_duplicate_season_rejected(snapshot, article):
    row = '{{КлСтат/Сезон|2026/27|3|0|0|0|0|0|0|0}}'
    text = snapshot.text.replace(row, row+'\n'+row)
    assert plan_article(replace(snapshot, text=text), article).changes[0].status == 'unsupported_structure'


@pytest.mark.parametrize('mode', list(Mode))
def test_publication_disabled_every_mode(mode):
    with pytest.raises(UpdateError) as error:
        Publisher().publish(mode=mode)
    assert error.value.code=='publishing_disabled'
