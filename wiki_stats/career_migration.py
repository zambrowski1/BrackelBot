# SPDX-License-Identifier: MIT
"""Lossless migration of the verified football-career template redirect."""
from .errors import UpdateError
from .models import Patch
from .wikitext_parser import WikitextParser, only_template
from .change_validator import apply_patches

FIELDS = ('молодёжные клубы', 'клубы', 'национальная сборная')


def migration_operation(text, player, day):
    parser = WikitextParser(text)
    box = parser.infobox(player)
    fields = []
    for field in FIELDS:
        if field not in box:
            continue
        ref = only_template(box[field], 'спортивная карьера')
        if ref.name == 'футбольная карьера':
            parser.career(field, player)  # Reject unknown named or missing positional parameters.
            fields.append(field)
    if not fields:
        return None
    return {'id':'migrate-career-template','type':'migrate_career_template',
        'group_id':'career-template-migration',
        'entity':{'name':player,'wikitext':'{{Футболист}}'},
        'target':{'structure':'career'},'as_of':day,
        'sources':[{'url':'https://ru.wikipedia.org/wiki/Шаблон:Футбольная_карьера',
                    'provenance':'user_provided','coverage_as_of':day,
                    'note':'Футбольная карьера перенаправляет на Спортивную карьеру; меняется только имя шаблона'}],
        'payload':{'fields':fields}}


def migrate(text, player, op):
    if op['entity']['name'] != player:
        raise UpdateError('player_mismatch', 'Замена шаблона относится к другой карточке')
    fields = op['payload']['fields']
    if not fields or len(fields) != len(set(fields)) or any(f not in FIELDS for f in fields):
        raise UpdateError('invalid_input', 'Нужен точный список полей карьеры')
    parser = WikitextParser(text)
    box = parser.infobox(player)
    patches = []
    for field in fields:
        if field not in box:
            raise UpdateError('unsupported_structure', 'Указанное поле карьеры отсутствует')
        ref = only_template(box[field], 'спортивная карьера')
        parser.career(field, player)
        if ref.name == 'спортивная карьера':
            continue
        raw_name = str(ref.node.name)
        start = ref.start + 2 + len(raw_name) - len(raw_name.lstrip())
        old_name = raw_name.strip()
        patches.append(Patch(start,start+len(old_name),old_name,'спортивная карьера','template_name'))
    if not patches:
        return text,'already_applied'
    return apply_patches(text,patches,structural=True),'ready'
