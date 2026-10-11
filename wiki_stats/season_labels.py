# SPDX-License-Identifier: MIT
"""Interpret season labels without changing their original wikitext."""
import re
import mwparserfromhell as mw
from .errors import UpdateError


def normalize_table_season(value):
    code = mw.parse(value)
    for template in code.filter_templates():
        if str(template.name).strip().casefold() != 'аренда' or template.params:
            raise UpdateError('unknown_season', 'Неизвестное оформление сезона')
        code.remove(template)
    label = code.strip_code().strip()
    match = re.fullmatch(r'((?:19|20)\d{2})[/–—-](\d{2}|(?:19|20)\d{2})(?:\s*\(аренда\))?', label)
    if match:
        start, end = int(match[1]), int(match[2])
        if end != (start + 1 if len(match[2]) == 4 else (start + 1) % 100):
            raise UpdateError('unknown_season', 'Непоследовательные годы сезона')
        return f'{start}/{(start+1)%100:02}'
    if re.fullmatch(r'(?:19|20)\d{2}', label):
        return label
    raise UpdateError('unknown_season', 'Не распознан сезон таблицы: '+label)
