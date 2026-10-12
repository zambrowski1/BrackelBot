"""Save verified Wikipedia redirects to catalogue leagues; read-only, resumable."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from wiki_stats.wiki_client import WikiClient

DATA = ROOT / 'handoff/season-catalogue'
cache = DATA / 'league-redirect-responses'
cache.mkdir(exist_ok=True)
catalogue = json.loads((ROOT / 'wiki_stats/season_catalogue.json').read_text(encoding='utf8'))
titles = sorted({row['title'] for row in catalogue['leagues']})
wiki = WikiClient(interval=10, timeout=40)
redirects = {}
for start in range(0, len(titles), 10):
    batch = titles[start:start + 10]
    continuation = {}
    seen = set()
    while True:
        params = {'action': 'query', 'prop': 'redirects', 'titles': '|'.join(batch),
                  'rdnamespace': 0, 'rdprop': 'title', 'rdlimit': 500, **continuation}
        key = hashlib.sha256(json.dumps(params, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        path = cache / (key + '.json')
        if path.exists():
            response = json.loads(path.read_text(encoding='utf8'))['response']
        else:
            response = wiki._api(params)
            path.write_text(json.dumps({'params': params, 'checked_at': datetime.now(timezone.utc).isoformat(),
                                        'response': response}, ensure_ascii=False, indent=2), encoding='utf8')
        for page in response['query']['pages']:
            if page.get('ns') != 0 or 'missing' in page or 'redirect' in page or page['title'] not in batch:
                continue
            aliases = redirects.setdefault(page['title'], set())
            aliases.update(row['title'] for row in page.get('redirects', []))
        continuation = response.get('continue', {})
        if not continuation:
            break
        token = json.dumps(continuation, sort_keys=True)
        if token in seen:
            raise RuntimeError('Repeated redirect continuation')
        seen.add(token)
    print(json.dumps({'league_titles_checked': min(start + 10, len(titles)),
                      'redirect_aliases': sum(map(len, redirects.values()))}), flush=True)
(DATA / 'league-redirects.json').write_text(
    json.dumps({'redirects': {title: sorted(aliases) for title, aliases in sorted(redirects.items())}},
               ensure_ascii=False, indent=2), encoding='utf8')
