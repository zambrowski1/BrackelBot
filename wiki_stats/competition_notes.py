# SPDX-License-Identifier: MIT
"""Column notes name the tournaments actually included in the new table."""
from .competition_labels import category_label

# Sofascore IDs are provider-specific, not interchangeable with API-Football.
LINKS = {
    217: '[[Кубок Германии по футболу|Кубок Германии]]',
    19: '[[Кубок Англии по футболу|Кубок Англии]]',
    21: '[[Кубок Английской футбольной лиги]]',
    328: '[[Кубок Италии по футболу|Кубок Италии]]',
    329: '[[Кубок Испании по футболу|Кубок Испании]]',
    96: '[[Кубок Турции по футболу|Кубок Турции]]',
    7: '[[Лига чемпионов УЕФА]]',
    679: '[[Лига Европы УЕФА]]',
    17015: '[[Лига конференций УЕФА]]',
    799: '[[Суперкубок Германии по футболу|Суперкубок Германии]]',
}


def column_notes(records, clubs, categories):
    included={(block['entity']['wikidata_id'],row['season'])
              for block in clubs for row in block['rows']}
    notes={}
    for record in records:
        key=(record['resolved'].qid,record['season'])
        category=record['category']
        if key not in included or category not in categories:
            continue
        if category_label(category) not in {'Кубки','Кубок','Национальный кубок','Кубок Германии','Кубок лиги','Еврокубки','Прочие'}:
            continue
        block=next(b for b in clubs if b['entity']['wikidata_id']==key[0])
        row=next(r for r in block['rows'] if r['season']==key[1])
        if row['categories'].get(category) is None:
            continue
        link=LINKS.get(record['tournament_id'])
        if link and link not in notes.setdefault(category,[]):
            notes[category].append(link)
    return {category:sorted(links) for category,links in notes.items() if links}
