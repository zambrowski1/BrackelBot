from dataclasses import replace
from copy import deepcopy
import pytest
from tests.stage23_helpers import MINI,snapshot,package
from tests.test_sofascore import Resolver
from wiki_stats.club_flags import prepare,execute,flagged_label
from wiki_stats.schema_validator import validate_package
from wiki_stats.change_planner import plan_article
from wiki_stats.change_validator import references,arithmetic_warnings
from wiki_stats.errors import UpdateError


def missing_flags():return MINI.replace('{{КлСтат/Клуб|{{Флаг Германии|20px}} ','{{КлСтат/Клуб|')


def test_old_header_gets_verified_flag_without_changing_statistics_refs_or_labels():
    text=missing_flags();op,issues=prepare(text,'Кальвин Браккельман','2026-10-12',Resolver())
    assert op and not issues
    pkg=package([op]);pkg['generated_at']='2026-10-12T00:00:00Z';pkg['articles'][0]['base_revid']=snapshot(text).revid;validate_package(pkg)
    plan=plan_article(snapshot(text),pkg['articles'][0],'1.1',Resolver())
    assert plan.changes[0].status=='ready' and plan.preview==MINI
    assert references(text)==references(plan.preview) and not arithmetic_warnings(plan.preview)
    assert not plan_article(replace(snapshot(text),text=plan.preview),pkg['articles'][0],'1.1',Resolver()).diff
    assert prepare(plan.preview,'Кальвин Браккельман','2026-10-12',Resolver())==(None,[])


@pytest.mark.parametrize('old',['{{Флаг Франции|20px}} [[Клуб]]','{{Флаг Германии|20px}}{{Флаг Германии|20px}} [[Клуб]]','{{флаг|GER}} [[Клуб]]'])
def test_wrong_duplicate_and_generic_flags_are_normalized(old):
    assert flagged_label(old,'Германии')=='{{Флаг Германии|20px}} [[Клуб]]'


def test_loan_and_editor_annotations_are_preserved():
    label='{{аренда}} [[Клуб|Подпись]]<!--заметка--><ref>Источник</ref>'
    assert flagged_label(label,'Германии')=='{{Флаг Германии|20px}} '+label


def test_changed_country_or_club_refuses_stale_proposal():
    text=missing_flags();op,_=prepare(text,'Кальвин Браккельман','2026-10-12',Resolver())
    bad=deepcopy(op);bad['payload']['clubs'][0]['flag']='Франции'
    with pytest.raises(UpdateError):execute(text,'Кальвин Браккельман',bad,Resolver())
    with pytest.raises(UpdateError):execute(text.replace('«[[Аугсбург','«[[Другой клуб'),'Кальвин Браккельман',op,Resolver())


def test_unverified_country_is_reported_and_does_not_change_old_header():
    class Unverified(Resolver):
        def resolve(self,entity):raise UpdateError('flag_unverified','Country unknown')
    text=missing_flags();op,issues=prepare(text,'Кальвин Браккельман','2026-10-12',Unverified())
    assert op is None and issues[0]['code']=='club_flag_unproven'


def test_full_sofascore_cycle_flags_old_retained_headers_and_replays():
    from tests.test_sofascore import prepare as full_cycle
    text=missing_flags()
    pkg,plan,report=full_cycle(text)
    assert any(op['type']=='normalize_club_flags' for op in pkg['articles'][0]['operations'])
    assert all(c.status in {'ready','already_applied'} for c in plan.changes)
    assert '{{КлСтат/Клуб|{{Флаг Германии|20px}}' in plan.preview
    assert not full_cycle(plan.preview.replace('| дата рождения = 22.08.1999\n',''))[1].diff


@pytest.mark.parametrize('marker',['аренда','фарм-клуб'])
@pytest.mark.parametrize('label',['{{MARKER}} {{Флаг Германии|20px}} [[Клуб|Подпись]]',
    '{{Флаг Германии|20px}} [[Клуб|Подпись]] {{MARKER}}',
    '{{Флаг Германии|20px}} {{MARKER}}[[Клуб|Подпись]]'])
def test_flag_then_role_marker_then_club_for_every_original_marker_position(marker,label):
    old=label.replace('MARKER',marker)+'<!--сохранить--><ref>Источник</ref>'
    expected='{{Флаг Германии|20px}} {{'+marker+'}} [[Клуб|Подпись]]<!--сохранить--><ref>Источник</ref>'
    new=flagged_label(old,'Германии')
    prefix=expected[:expected.index('<!--')]
    assert new.startswith(prefix) and new[len(prefix):].strip()=='<!--сохранить--><ref>Источник</ref>'
    assert references(new)==references(old)
    assert flagged_label(new,'Германии')==new
