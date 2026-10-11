# SPDX-License-Identifier: MIT
"""Make a source-verified, implicit article name explicit in an old card."""
import hashlib
from .wikitext_parser import WikitextParser
from .models import Patch
from .errors import UpdateError
from .change_validator import apply_patches


def unnamed_card(text, player):
    cards = [t for t in WikitextParser(text).templates if t.name == 'футболист']
    if len(cards) != 1:
        raise UpdateError('unsupported_structure', 'Нужна единственная карточка Футболист')
    values = cards[0].values()
    if 'имя' in values:
        WikitextParser(text).infobox(player)
        return None
    return cards[0]


def operation(text, mapping, day):
    card = unnamed_card(text, mapping['wiki_name'])
    if card is None:
        return None
    return {'id':'bind-card-name','type':'bind_infobox_name','group_id':'sofascore-reconciliation',
        'entity':{'name':mapping['wiki_name'],'wikidata_id':mapping['qid'],'wikitext':'{{Футболист}}'},
        'target':{'structure':'career'},'as_of':day,
        'sources':[{'url':'https://www.wikidata.org/wiki/'+mapping['qid'],
                    'provenance':'user_provided','coverage_as_of':day,
                    'note':'Статья и профиль игрока проверены по QID и дате рождения; дата в карточке не меняется'}],
        'payload':{'expected_sha256':hashlib.sha256(str(card.node).encode()).hexdigest()}}


def bind(text, player, op):
    if op['entity']['name'] != player or any(c in player for c in '|{}[]\r\n'):
        raise UpdateError('player_mismatch', 'Недопустимое имя карточки')
    card = unnamed_card(text, player)
    if card is None:
        return text, 'already_applied'
    if hashlib.sha256(str(card.node).encode()).hexdigest() != op['payload']['expected_sha256']:
        raise UpdateError('old_value_mismatch', 'Карточка изменилась после проверки связи игрока')
    at = card.start + 2 + len(str(card.node.name))
    newline = '\r\n' if '\r\n' in text else '\n'
    addition = '| имя = '+player+newline
    return apply_patches(text, [Patch(at, at, '', addition, 'infobox_name')], structural=True), 'ready'
