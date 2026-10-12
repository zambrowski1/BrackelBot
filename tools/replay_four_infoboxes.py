"""Replay the real collector on four original articles, without football API calls."""
import hashlib
import json
import sys
import zipfile
import requests
import argparse
from urllib.parse import quote
from dataclasses import replace, asdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from wiki_stats.sofascore_client import SofascoreClient
from wiki_stats.sofascore_cycle import prepare_player
from wiki_stats.wiki_client import WikiClient
from wiki_stats.entity_resolver import EntityResolver
from wiki_stats.models import PageSnapshot
from wiki_stats.errors import UpdateError
from wiki_stats.wikitext_parser import WikitextParser
from wiki_stats.change_validator import arithmetic_warnings, references
from tools.recheck_saved_four import CASES

BASE = ROOT/'handoff/2026-10-12/latest'
OUT = ROOT/'handoff/2026-10-13/automatic-card-recheck'
EXPECTED = {'Грифо': '227 (60)', 'Крамарич': '310 (135)', 'Центнер': '210 (-314)', 'Шик': '154 (85)'}


class EmptyStore:
    def get(self, *args): return None


class CachedAPI(SofascoreClient):
    def __init__(self, cache):
        self.store = EmptyStore(); self.cache = cache; self.missing = []
    def request(self, path, params, **kwargs):
        key = hashlib.sha256(json.dumps([path, params], sort_keys=True).encode()).hexdigest()
        if key not in self.cache:
            self.missing.append({'path': path, 'params': params})
            raise UpdateError('source_cache_missing', 'Нет сохранённого ответа: '+path)
        return self.cache[key]
    def quota_status(self): return {'football_api_requests': 0, 'mode': 'cache_only'}


class ReadWiki(WikiClient):
    def __init__(self, refresh=False):
        super().__init__(interval=1.5, timeout=15)
        self.refresh = refresh
        self.paths = {p.name: p for p in (ROOT/'handoff/2026-10-12/work-history').rglob('wiki-*.json')}
        evidence_path = OUT/'wikimedia-read-evidence.json'
        self.fresh = json.loads(evidence_path.read_text(encoding='utf8')) if evidence_path.exists() else {}
        self.reads = 0; self.total_reads = 0; self.entities = {}; self.pages = {}; self.aliases = {}; self.attempts = []
        def absorb(value):
            if not isinstance(value, dict): return
            for qid, entity in value.get('entities', {}).items():
                self.entities.setdefault(qid, {}).update(entity)
            query = value.get('query', {})
            for p in query.get('pages', []):
                if isinstance(p, dict): self.pages.setdefault(p['title'], {}).update(p)
            for kind in ('normalized', 'redirects'):
                for alias in query.get(kind, []): self.aliases[alias['from']] = alias['to']
            for key in ('wiki','wikidata'):
                if key in value: absorb(value[key])
        for p in self.paths.values(): absorb(json.loads(p.read_text(encoding='utf8')))
        for value in self.fresh.values(): absorb(value)
        with zipfile.ZipFile(BASE/'latest-evidence.zip') as archive:
            for name in ('club-flag-live-identities.json','mainz-reserve-identity.json','football-reserve-class.json'):
                absorb(json.loads(archive.read(name)))
        for p in (ROOT/'handoff/2026-10-12/practical-policy').glob('*identities*.json'):
            data = json.loads(p.read_text(encoding='utf8'))
            for value in data if isinstance(data,list) else [data]: absorb(value)
        for p in OUT.glob('*-entity.json'): absorb(json.loads(p.read_text(encoding='utf8')))
        for p in OUT.glob('*-page.json'):
            value = json.loads(p.read_text(encoding='utf8'))
            if value.get('wikibase_item'):
                self.pages[value['title']] = {'title':value['title'],'ns':value['namespace']['id'], 'pageid':value['pageid'], 'pageprops':{'wikibase_item':value['wikibase_item']}}
    def _api(self, params, post=False, endpoint=None):
        try: return self._read_api(params, post=post, endpoint=endpoint)
        except requests.RequestException:
            raise UpdateError('wiki_source_unavailable', 'Дополнительный источник Wikimedia недоступен') from None
    def _read_api(self, params, post=False, endpoint=None):
        assert not post, 'Wikipedia writes forbidden'
        key = hashlib.sha256(json.dumps([endpoint, params], sort_keys=True).encode()).hexdigest()
        name = 'wiki-'+key+'.json'
        if name in self.paths: return json.loads(self.paths[name].read_text(encoding='utf8'))
        if key in self.fresh: return self.fresh[key]
        if params.get('action') == 'wbgetentities' and params.get('ids'):
            ids = params['ids'].split('|')
            if all(qid in self.entities for qid in ids): return {'entities': {qid:self.entities[qid] for qid in ids}}
        if params.get('action') == 'query' and params.get('titles') and params.get('prop') in ('pageprops','info','info|pageprops'):
            titles = params['titles'].split('|'); pages = []; redirects = []
            for title in titles:
                target = self.aliases.get(title,title)
                if target not in self.pages: break
                page = self.pages[target]
                if 'pageprops' in params['prop'] and 'pageprops' not in page and 'missing' not in page: break
                pages.append(page)
                if target != title: redirects.append({'from':title,'to':target})
            else: return {'query': {'pages':pages,'redirects':redirects}}
        if not self.refresh: raise UpdateError('wiki_cache_missing', 'Нет сохранённого ответа Wikimedia; используйте --refresh-wiki')
        if self.reads >= 20: raise UpdateError('wiki_cache_missing', 'Лимит дополнительных проверок Wikimedia')
        self.reads += 1
        self.total_reads += 1
        self.attempts.append({'params':params,'endpoint':endpoint})
        if params.get('action') == 'wbgetentities' and params.get('ids') and '|' not in params['ids']:
            response = requests.get('https://www.wikidata.org/wiki/Special:EntityData/'+params['ids']+'.json', timeout=15)
            response.raise_for_status(); result = response.json()
        elif params.get('action') == 'query' and params.get('prop') == 'pageprops' and params.get('titles') and '|' not in params['titles']:
            host = (endpoint or self.endpoint).split('/w/')[0]
            response = requests.get(host+'/api/rest_v1/page/summary/'+quote(params['titles'].replace(' ','_'),safe=''),timeout=15)
            if response.status_code == 404: result = {'query':{'pages':[{'title':params['titles'],'missing':True}]}}
            else:
                response.raise_for_status(); value = response.json()
                if not value.get('wikibase_item'): raise UpdateError('wiki_cache_missing','REST не подтвердил QID')
                result = {'query':{'pages':[{'title':value['title'],'pageid':value['pageid'],'ns':value['namespace']['id'],'pageprops':{'wikibase_item':value['wikibase_item']}}]}}
        else: result = super()._api(params, post=False, endpoint=endpoint)
        self.fresh[key] = result
        return result


def card(text, player):
    parser = WikitextParser(text)
    values = next(t.values() for t in parser.templates if t.name == 'футболист')
    result = {key: values[key].text.strip() for key in ('текущий клуб', 'клуб', 'номер', 'обновление данных о клубе', 'обновление данных о сборной') if key in values}
    for field in ('клубы', 'национальная сборная'):
        try: result[field] = [[period, club, value.text.strip()] for period, club, value in parser.career(field, player)]
        except UpdateError as exc: result[field] = {'error': exc.code}
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--refresh-wiki', action='store_true', help='Allow at most 20 metadata reads per player; football remains offline')
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True); wiki = ReadWiki(args.refresh_wiki); results = []
    with zipfile.ZipFile(BASE/'latest-evidence.zip') as archive:
        read = lambda name: json.loads(archive.read(name))
        common = read('bundesliga-three-2026-10-12/cache.json')
        schick = read('schick-sbmatches-api-2026-10-12/seed-cache.json')
        schick.update(read('schick-sbmatches-api-2026-10-12/fresh-cache.json'))
        for short, pid, *_ in CASES:
            wiki.reads = 0
            mapping = read('schick-sbmatches-api-2026-10-12/mapping.json') if short == 'Шик' else read(f'bundesliga-three-2026-10-12/{pid}/final/report.json')['mapping']
            before = archive.read('schick-ready-2026-10-12/before.wiki' if short == 'Шик' else f'bundesliga-three-2026-10-12/{pid}/before.wiki').decode('utf-8-sig')
            api = CachedAPI(schick if short == 'Шик' else common)
            snapshot = PageSnapshot(mapping['title'], 1, '2026-10-12T00:00:00Z', before, 0)
            result = {'player': mapping['wiki_name'], 'football_api_requests': 0, 'input': 'original_before_snapshot', 'before_card': card(before, mapping['wiki_name'])}
            try:
                package, plan, evidence = prepare_player(snapshot, mapping, api, wiki, EntityResolver(wiki), statistics_since=0, max_requests=500, now=datetime(2026,10,13,tzinfo=timezone.utc))
                (OUT/f'{short}-automatic.wiki').write_text(plan.preview, encoding='utf8')
                (OUT/f'{short}-automatic.diff').write_text(plan.diff, encoding='utf8')
                (OUT/f'{short}-package.json').write_text(json.dumps(package,ensure_ascii=False,indent=2),encoding='utf8')
                _, second, _ = prepare_player(replace(snapshot,text=plan.preview), mapping, api, wiki, EntityResolver(wiki), statistics_since=0, max_requests=500, now=datetime(2026,10,13,tzinfo=timezone.utc))
                result.update(after_card=card(plan.preview,mapping['wiki_name']), idempotent=second.preview == plan.preview,
                    references_preserved=all(r in references(plan.preview) for r in references(before)),
                    new_arithmetic_errors=[e for e in arithmetic_warnings(plan.preview) if e not in arithmetic_warnings(before)],
                    changes=[asdict(c) for c in plan.changes], review=evidence.get('review',[]))
            except Exception as exc:
                result.update(error=str(exc),code=getattr(exc,'code',type(exc).__name__))
            result['missing_cached_requests'] = api.missing
            result['expected_current_league_total'] = EXPECTED[short]
            careers = result.get('after_card', {}).get('клубы')
            actual = careers[-1][-1].replace('−','-') if isinstance(careers,list) and careers else None
            result['current_club_card_passed'] = actual == EXPECTED[short]
            results.append(result)
            print(json.dumps({k:v for k,v in result.items() if k not in ('changes','review','before_card','missing_cached_requests')},ensure_ascii=False),flush=True)
    (OUT/'report.json').write_text(json.dumps({'wikipedia_edits':0,'football_api_requests':0,'wikimedia_reads':wiki.total_reads,'wiki_read_attempts':wiki.attempts,'articles':results},ensure_ascii=False,indent=2),encoding='utf8')
    (OUT/'wikimedia-read-evidence.json').write_text(json.dumps(wiki.fresh,ensure_ascii=False,indent=2),encoding='utf8')
    return 0 if all(a['current_club_card_passed'] for a in results) else 1


if __name__ == '__main__': sys.exit(main())
