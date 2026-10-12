"""Check the latest four drafts locally; no network or publication."""
from pathlib import Path
import sys
import mwparserfromhell as mw
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT.parents[2]))
from wiki_stats.wikitext_parser import WikitextParser
from wiki_stats.change_validator import arithmetic_warnings
from wiki_stats.sbmatches_editor import read_matches

for path in sorted((ROOT/'articles').glob('*готовая статья.wiki')):
    text=path.read_text(encoding='utf8');parser=WikitextParser(text)
    assert not arithmetic_warnings(text),arithmetic_warnings(text)
    clubs=[t for t in parser.templates if t.name=='клстат/клуб']
    assert clubs and sum(t.name=='клстат/итого' for t in parser.templates)<=1
    parser.club_table() # Includes a validated closure for a partial table.
    for t in clubs:
        label=t.values()['1'].text.strip();code=mw.parse(label)
        flags=[p for p in code.filter_templates(recursive=False) if str(p.name).strip().startswith('Флаг ')]
        assert len(flags)==1 and label.startswith(str(flags[0]))
        links=code.filter_wikilinks(recursive=False)
        for marker in code.filter_templates(recursive=False):
            if str(marker.name).strip().lower() in {'аренда','фарм-клуб'}:
                assert label.index(str(flags[0]))<label.index(str(marker))<label.index(str(links[0]))
    nationals=[]
    for t in parser.templates:
        if t.name=='сбматчи':
            values,rows=read_matches(t)
            nationals.append((len(rows),sum(int(r['голы']) for r in rows)))
    print(path.name,'clubs=',len(clubs),'national=',nationals)
print('OK: four articles; no network requests or Wikipedia edits')
