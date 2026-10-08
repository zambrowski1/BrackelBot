# SPDX-License-Identifier: MIT
from collections import defaultdict
import mwparserfromhell as mw
from .errors import UpdateError
from .wikitext_parser import WikitextParser


def apply_patches(text, patches, structural=False):
    ordered = sorted(patches, key=lambda p: (p.start, p.end))
    end = 0
    for p in ordered:
        if p.start < end or p.start < 0 or p.end > len(text) or text[p.start:p.end] != p.before:
            raise UpdateError("patch_conflict", "Пересекающиеся правки или неверные границы")
        if not structural and (not p.before.isascii() or not p.before.isdigit() or not p.after.isascii() or not p.after.isdigit()):
            raise UpdateError("integrity_error", "На первом этапе разрешена только замена целых чисел")
        end = p.end
    result = text
    for p in reversed(ordered):
        result = result[:p.start] + p.after + result[p.end:]
    if references(text) != references(result):
        raise UpdateError("integrity_error", "Изменились сноски")
    return result


def references(text):
    return [str(t) for t in mw.parse(text).filter_tags() if str(t.tag).strip().casefold() == "ref"]


def arithmetic_warnings(text):
    parser = WikitextParser(text)
    if not any(t.name == "клстат" for t in parser.templates):
        return []
    try:
        table = parser.club_table()
    except UpdateError as exc:
        return [f"Контроль клубной таблицы: {exc.code}: {exc}"]
    width = 2*len(table.categories)
    sums = defaultdict(lambda: [0]*width)
    for row in table.seasons:
        for i in range(width):
            sums[row.club][i] += row.values[str(i+2)].numeric()[0].value
    warnings = []
    for club, values in table.totals.items():
        if [v.numeric()[0].value for v in values] != sums[club]:
            warnings.append(f"Итог клуба не равен сумме сезонов: {club}")
    grand = [sum(v[i] for v in sums.values()) for i in range(width)]
    if [v.numeric()[0].value for v in table.grand_total] != grand:
        warnings.append("Итог карьеры не равен сумме сезонов")
    return warnings
