"""Find seasons of discovered football competitions without requiring a season type."""
from pathlib import Path
from datetime import datetime, timezone
import json
import time
import requests

DATA = Path(__file__).resolve().parents[1] / 'handoff/season-catalogue'
rows = json.loads((DATA / 'wikidata-results.json').read_text(encoding='utf8'))['results']['bindings']
leagues = sorted({row['league']['value'].rsplit('/', 1)[1] for row in rows})
years = '(' + '|'.join(str(y) for y in range(2010, datetime.now(timezone.utc).year + 1)) + ')'
cache = DATA / 'related-season-responses'
cache.mkdir(exist_ok=True)
session = requests.Session()
session.headers['User-Agent'] = 'BrackelBot/0.4 (Wikipedia user Zambrowski; read-only season catalogue)'
bindings = []
for start in range(0, len(leagues), 75):
    ids = leagues[start:start + 75]
    query = '''SELECT DISTINCT ?article ?season ?league WHERE {
 VALUES ?league { IDS }
 ?season wdt:P3450 ?league.
 ?article schema:about ?season; schema:isPartOf <https://ru.wikipedia.org/>.
 FILTER(REGEX(STR(?article), "YEARS"))
}'''.replace('IDS', ' '.join('wd:' + q for q in ids)).replace('YEARS', years)
    path = cache / f'{start:04}.json'
    if path.exists():
        record = json.loads(path.read_text(encoding='utf8'))
        if record['query'] != query:
            raise SystemExit('Saved query differs; preserve the old snapshot before starting a new one.')
    else:
        response = session.get('https://query.wikidata.org/sparql',
                               params={'query': query, 'format': 'json'}, timeout=55)
        response.raise_for_status()
        record = {'query': query, 'checked_at': datetime.now(timezone.utc).isoformat(),
                  'response': response.json()}
        assert isinstance(record['response']['results']['bindings'], list)
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf8')
        time.sleep(10)
    bindings.extend(record['response']['results']['bindings'])
    print(json.dumps({'competitions': min(start + 75, len(leagues)), 'bindings': len(bindings)}), flush=True)
(DATA / 'related-season-results.json').write_text(
    json.dumps({'results': {'bindings': bindings}}, ensure_ascii=False, indent=2), encoding='utf8')
