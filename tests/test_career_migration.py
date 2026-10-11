# SPDX-License-Identifier: MIT
from copy import deepcopy
from datetime import datetime,timezone
import pytest
from tests.stage23_helpers import MINI,snapshot
from tests.test_sofascore import Wiki,API,Resolver
from wiki_stats.career_migration import migration_operation,migrate
from wiki_stats.errors import UpdateError
from wiki_stats.sofascore_cycle import prepare_player
from wiki_stats.schema_validator import validate_package

PLAYER='Кальвин Браккельман'
OLD=MINI.replace('Спортивная карьера','футбольная карьера').replace('спортивная карьера','футбольная карьера')


def test_only_card_template_names_change_and_reapplying_is_idempotent():
    text=OLD+'\n{{футбольная карьера|2026|клуб вне карточки|5 (2)}}\n'
    op=migration_operation(text,PLAYER,'2026-10-09')
    result,status=migrate(text,PLAYER,op)
    expected=OLD.replace('футбольная карьера','спортивная карьера')+text[len(OLD):]
    assert status=='ready' and result==expected
    assert migrate(result,PLAYER,op)==(result,'already_applied')


def test_signed_goals_loans_reserves_and_empty_youth_cells_are_preserved():
    text=OLD.replace('4 (1)','4 (-54)').replace('| клубы =','| молодёжные клубы = {{футбольная карьера||{{аренда}} клуб|}}\n| клубы =')
    op=migration_operation(text,PLAYER,'2026-10-09')
    result,_=migrate(text,PLAYER,op)
    assert result==text.replace('футбольная карьера','спортивная карьера')
    assert op['payload']['fields']==['молодёжные клубы','клубы','национальная сборная']


@pytest.mark.parametrize('change',[
    lambda x:x.replace('{{футбольная карьера','{{неизвестная карьера',1),
    lambda x:x.replace('{{футбольная карьера','{{футбольная карьера|неизвестный=42',1),
    lambda x:x.replace('|10 (1)','',1),
])
def test_unsupported_structure_cannot_be_silently_migrated(change):
    with pytest.raises(UpdateError): migration_operation(change(OLD),PLAYER,'2026-10-09')


def test_duplicate_infobox_parameter_stops_migration():
    text=OLD.replace('| номер =','| клубы = {{футбольная карьера|2026|клуб|1 (0)}}\n| номер =')
    with pytest.raises(UpdateError) as exc:migration_operation(text,PLAYER,'2026-10-09')
    assert exc.value.code=='ambiguous_target'


def test_whitespace_and_crlf_are_preserved():
    text=OLD.replace('{{футбольная карьера','{{  ФУТБОЛЬНАЯ КАРЬЕРА  ').replace('\n','\r\n')
    op=migration_operation(text,PLAYER,'2026-10-09')
    result,_=migrate(text,PLAYER,op)
    assert result==text.replace('ФУТБОЛЬНАЯ КАРЬЕРА','спортивная карьера')


def test_modern_template_needs_no_migration():
    assert migration_operation(MINI,PLAYER,'2026-10-09') is None


def test_migration_can_be_prepared_when_statistics_are_blocked_and_birthdate_is_not_added():
    text=OLD.replace('4 (1)','4 (-54)')
    mapping={'api_id':1,'qid':'Q114406918','title':'Игрок','wiki_name':PLAYER,'birth_date':'1999-08-22'}
    pkg,plan,evidence=prepare_player(snapshot(text),mapping,API(),Wiki(),Resolver(),now=datetime(2026,10,9,tzinfo=timezone.utc))
    assert [c.operation['type'] for c in plan.changes]==['migrate_career_template']
    assert plan.preview==text.replace('футбольная карьера','спортивная карьера')
    assert 'дата рождения' not in plan.preview
    assert evidence['review'][0]['code']=='goal_metric_mismatch'
    validate_package(pkg)


def test_migration_requires_revision_and_exact_player():
    op=migration_operation(OLD,PLAYER,'2026-10-09')
    pkg={'schema_version':'1.1','package_id':'migration-test','generated_at':'2026-10-09T00:00:00Z',
         'articles':[{'title':snapshot().title,'player':PLAYER,'operations':[op]}]}
    with pytest.raises(UpdateError):validate_package(pkg)
    pkg['articles'][0]['base_revid']=100
    validate_package(pkg)
    bad=deepcopy(op);bad['entity']['name']='Другой игрок'
    with pytest.raises(UpdateError):migrate(OLD,PLAYER,bad)
