# SPDX-License-Identifier: MIT
import re
import mwparserfromhell as mw
from .errors import UpdateError


def visible_code(text):
    code=mw.parse(text)
    for node in code.filter_comments():code.remove(node)
    for node in reversed(code.filter_tags()):
        if str(node.tag).strip().casefold() in {'ref','references'}:code.remove(node)
    return code


def is_goalkeeper(parser, player):
    box = parser.infobox(player)
    for field in ('позиция','амплуа'):
        value=box.get(field)
        position=visible_code(value.text).strip_code().strip() if value else ''
        if position:
            return bool(re.search(r'\b(?:вратарь|вратар[ьяею]|goalkeeper|goalie)\b', position, re.IGNORECASE))
    # Old infoboxes may omit position. Accept an explicit goalkeeper role in
    # this player's lead, never a stray mention elsewhere or negative numbers.
    ref = next(t for t in parser.templates if t.name=='футболист')
    tail = str(parser.code)[ref.start+len(str(ref.node)):].lstrip()
    lead = str(visible_code(re.split(r'\r?\n\s*\r?\n',tail,maxsplit=1)[0]))
    if not lead.startswith("'''"+player+"'''"):
        return False
    return bool(re.search(r'—[^\n]*?\[\[футбол(?:ист)?(?:\|[^\]]+)?\]\](?:ист)?\s*,\s*'
                          r'\[\[Вратарь\s*\(футбол\)(?:\|[^\]]+)?\]\]',lead,re.IGNORECASE))


def validate_goal_values(parser, player, value):
    """Conceded goals need an explicit goalkeeper role in the card or lead."""
    keeper = is_goalkeeper(parser, player)
    def visit(item):
        if isinstance(item, dict):
            for key, child in item.items():
                if key in {'expected', 'expected_match'}:
                    continue
                if key == 'goals' and type(child) is int:
                    if child < 0 and not keeper or child > 0 and keeper:
                        raise UpdateError('goal_metric_mismatch', 'Вратарю нужны пропущенные голы с минусом; полевому игроку — забитые без минуса')
                else:
                    visit(child)
        elif isinstance(item, list):
            for child in item: visit(child)
    visit(value)


def decreased(new, old):
    """A larger conceded magnitude is progress, not a statistical correction."""
    if old.get('goals', 0) < 0 or new.get('goals', 0) < 0:
        return new.get('appearances', 0) < old.get('appearances', 0) or new.get('goals', 0) > old.get('goals', 0)
    return any(new[k] < old[k] for k in new)
