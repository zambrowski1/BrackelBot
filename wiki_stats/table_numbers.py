# SPDX-License-Identifier: MIT
"""Unknown table cells propagate into totals instead of becoming zero."""


def sum_known(values):
    values = list(values)
    return None if any(v is None for v in values) else sum(values)


def display(value):
    return '—' if value is None else str(value)


def row_totals(values):
    """Override native #expr totals when an em dash means missing data."""
    if any(v is None for v in values):
        return '|игры='+display(sum_known(values[::2]))+'|голы='+display(sum_known(values[1::2]))
    return ''


def season_totals(row, width):
    """Use an explicit season total when the tournament breakdown is unknown."""
    result=[]
    for j,key in enumerate(('игры','голы')):
        total=sum_known(row[str(2+i)].numeric(signed=bool(i%2))[0].value for i in range(j,width,2))
        if total is None and key in row:
            total=row[key].numeric(signed=bool(j))[0].value
        result.append(total)
    return result


def club_total(values, overall=None):
    if all(v is not None for v in values):
        return '{{КлСтат/ИтогоЗаКлуб||'+'|'.join(map(display,values))+'}}'
    # The native subtotal template does not forward explicit games/goals to
    # КлСтат/Стат. Render its ordinary row to avoid a visible #expr question mark.
    cells=[*values,*(overall if overall is not None else [sum_known(values[::2]),sum_known(values[1::2])])]
    return '|- style="font-weight:bold;"\n! scope="row" colspan="2" | Итого\n| '+' || '.join(map(display,cells))+'\n|-'
