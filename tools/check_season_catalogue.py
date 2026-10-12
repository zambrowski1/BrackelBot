"""Verify the shipped catalogue against saved public discovery evidence, offline."""
from pathlib import Path
from urllib.parse import unquote
import gzip
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from wiki_stats.season_catalogue import parse_season_title, lookup, load_catalogue

DATA = ROOT / 'handoff/season-catalogue'
catalogue = load_catalogue()
bindings = json.loads((DATA / 'wikidata-results.json').read_text(encoding='utf8'))['results']['bindings']
related = DATA / 'related-season-results.json'
if related.exists():
    bindings += json.loads(related.read_text(encoding='utf8'))['results']['bindings']
entities = json.loads(gzip.decompress((DATA / 'wikidata-entities.json.gz').read_bytes()))
wiki_pages = {}
for path in (DATA / 'responses').glob('*.json.gz'):
    record = json.loads(gzip.decompress(path.read_bytes()))
    for member in record['response'].get('query', {}).get('categorymembers', []):
        if member['ns'] == 0:
            wiki_pages[member['title']] = member['pageid']
wd_pages = {}
for binding in bindings:
    title = unquote(binding['article']['value'].split('/wiki/', 1)[1]).replace('_', ' ')
    wd_pages.setdefault(title, set()).add(binding['season']['value'].rsplit('/', 1)[1])

leagues = {row['key']: row for row in catalogue['leagues']}
redirect_file = DATA / 'league-redirects.json'
redirects = json.loads(redirect_file.read_text(encoding='utf8'))['redirects'] if redirect_file.exists() else {}
observed_redirects = {}
for path in (DATA / 'league-redirect-responses').glob('*.json'):
    record = json.loads(path.read_text(encoding='utf8'))
    for page in record['response']['query']['pages']:
        if page.get('ns') == 0 and 'missing' not in page and 'redirect' not in page:
            observed_redirects.setdefault(page['title'], set()).update(r['title'] for r in page.get('redirects', []))
for title, aliases in redirects.items():
    assert set(aliases) <= observed_redirects.get(title, set()), ('unverified redirect', title)

def values(qid, prop):
    return [statement['mainsnak']['datavalue']['value']['id']
            for statement in entities.get(qid, {}).get('claims', {}).get(prop, [])
            if statement.get('rank') != 'deprecated'
            and statement['mainsnak'].get('datavalue', {}).get('type') == 'wikibase-entityid']

for key in leagues:
    if not key.startswith('Q'):
        continue
    assert 'Q2736' in values(key, 'P641'), ('not association football', key)
    ancestors = set()
    pending = values(key, 'P31')
    while pending:
        ancestor = pending.pop()
        if ancestor not in ancestors:
            ancestors.add(ancestor)
            pending.extend(values(ancestor, 'P279'))
    if not ancestors & {'Q623109', 'Q15991303'}:
        proof_titles = leagues[key]['classification']['national_season_titles']
        matches = [row for row in catalogue['entries'] if row['title'] in proof_titles
                   and key in row['league_keys']
                   and any('национальные футбольные чемпионаты' in cat.casefold()
                           or ('сезоны чемпионат' in cat.casefold() and 'футбол' in cat.casefold())
                           for cat in row['proof']['wikipedia_categories'])]
        assert matches, ('neither league type nor national season category proven', key)

titles = set()
for row in catalogue['entries']:
    assert 'составы' not in row['title'].casefold(), ('squad appendix is not a season article', row['title'])
    assert row['title'] not in titles, ('duplicate title', row['title'])
    titles.add(row['title'])
    parsed = parse_season_title(row['title'], catalogue['start_year'], catalogue['end_year'])
    assert parsed and parsed[0] == row['season'], ('season mismatch', row['title'])
    assert row['league_keys'] and set(row['league_keys']) <= leagues.keys()
    proof = row['proof']
    assert proof['wikipedia_categories'] or proof['wikidata_season_qids']
    if 'pageid' in row:
        assert wiki_pages.get(row['title']) == row['pageid'], ('Wikipedia page mismatch', row['title'])
    if proof['wikidata_season_qids']:
        assert set(proof['wikidata_season_qids']) <= wd_pages.get(row['title'], set())
    assert set(row['country_qids']) <= catalogue['countries'].keys()
for alias, keys in catalogue['aliases'].items():
    assert set(keys) <= leagues.keys(), ('unknown alias key', alias)
    for key in keys:
        assert alias in leagues[key]['aliases']
        hints = {parse_season_title(row['title'], end=catalogue['end_year'])[1]
                 for row in catalogue['entries'] if key in row['league_keys']}
        assert alias in hints | {leagues[key]['title']} | set(redirects.get(leagues[key]['title'], [])), ('alias lacks evidence', alias, key)
    for season in {row['season'] for row in catalogue['entries']}:
        found = lookup(alias, season)
        assert len(found) <= 1, ('ambiguous selection', alias, season)
        if found:
            assert found[0] in titles

assert catalogue['coverage']['included_season_articles'] == len(titles)
print(json.dumps({'verified_titles': len(titles), 'league_groups': len(leagues),
                  'category_traversal_complete': catalogue['coverage']['category_traversal_complete'],
                  'network_requests': 0, 'wikipedia_writes': 0}, ensure_ascii=False))
