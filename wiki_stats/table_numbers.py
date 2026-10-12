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


def club_total(values):
    if all(v is not None for v in values):
        return '{{КлСтат/ИтогоЗаКлуб||'+'|'.join(map(display,values))+'}}'
    # The native subtotal template does not forward explicit games/goals to
    # КлСтат/Стат. Render its ordinary row to avoid a visible #expr question mark.
    cells=[*values,sum_known(values[::2]),sum_known(values[1::2])]
    return '|- style="font-weight:bold;"\n! scope="row" colspan="2" | Итого\n| '+' || '.join(map(display,cells))+'\n|-'
