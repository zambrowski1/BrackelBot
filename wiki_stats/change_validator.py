# SPDX-License-Identifier: MIT
from collections import defaultdict
import mwparserfromhell as mw
import re
from .errors import UpdateError
from .wikitext_parser import WikitextParser


def apply_patches(text, patches, structural=False):
    ordered = sorted(patches, key=lambda p: (p.start, p.end))
    end = 0
    for p in ordered:
        if p.start < end or p.start < 0 or p.end > len(text) or text[p.start:p.end] != p.before:
            raise UpdateError("patch_conflict", "Пересекающиеся правки или неверные границы")
        if not structural and (not re.fullmatch(r"[-−]?[0-9]+|[-—–]", p.before) or not p.after.isascii() or not re.fullmatch(r"-?[0-9]+", p.after)):
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
    from .table_numbers import sum_known
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
            sums[row.club][i] = sum_known([sums[row.club][i],row.values[str(i+2)].numeric(signed=bool(i % 2))[0].value])
    warnings = []
    for club, values in table.totals.items():
        actual=[v.numeric(signed=bool(i % 2))[0].value for i,v in enumerate(values)]
        if any(wanted is not None and actual[i]!=wanted for i,wanted in enumerate(sums[club])):
            warnings.append(f"Итог клуба не равен сумме сезонов: {club}")
    grand = [sum_known(v[i] for v in sums.values()) for i in range(width)]
    if table.grand_total and any(wanted is not None and table.grand_total[i].numeric(signed=bool(i%2))[0].value!=wanted for i,wanted in enumerate(grand)):
        warnings.append("Итог карьеры не равен сумме сезонов")
    for ref in parser.templates:
        if ref.name!='клстат/сезон':continue
        values=ref.values()
        for j,key in enumerate(('игры','голы')):
            wanted=sum_known(values[str(2+i)].numeric(signed=bool(i%2))[0].value for i in range(j,width,2))
            if key in values and wanted is not None and values[key].numeric(signed=bool(j))[0].value!=wanted:
                warnings.append('Общий итог сезона не равен сумме турниров: '+values['1'].text.strip())
    return warnings
