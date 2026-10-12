"""Download league identities/types from the saved global Wikidata result."""
from pathlib import Path
import sys,json,gzip,time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from wiki_stats.wiki_client import WikiClient
root=Path(__file__).resolve().parents[1]/'handoff/season-catalogue'
rows=json.loads((root/'wikidata-results.json').read_text(encoding='utf8'))['results']['bindings']
ids={row['league']['value'].rsplit('/',1)[1] for row in rows}
ids.update(row['country']['value'].rsplit('/',1)[1] for row in rows if 'country' in row)
ids.update({'Q623109','Q15991303','Q2736'})
cache=root/'wikidata-entities.json.gz'
entities=json.loads(gzip.decompress(cache.read_bytes())) if cache.exists() else {}
wiki=WikiClient(interval=6,timeout=30)
def fetch(ids):
 todo=sorted(set(ids)-set(entities))
 for i in range(0,len(todo),50):
  data=wiki._api({'action':'wbgetentities','ids':'|'.join(todo[i:i+50]),'props':'claims|labels|sitelinks','languages':'ru|en','sitefilter':'ruwiki'},endpoint='https://www.wikidata.org/w/api.php')
  for qid,entity in data['entities'].items():
   entities[qid]={**entity,'claims':{prop:claims for prop,claims in entity.get('claims',{}).items() if prop in {'P31','P279','P17','P641'}}}
  cache.write_bytes(gzip.compress(json.dumps(entities,ensure_ascii=False).encode()))
  print('entities',len(entities),flush=True)
fetch(ids)
def values(entity,property):
 return [s['mainsnak']['datavalue']['value']['id'] for s in entity.get('claims',{}).get(property,[]) if s['mainsnak'].get('datavalue',{}).get('type')=='wikibase-entityid' and s.get('rank')!='deprecated']
fetch({q for league in {r['league']['value'].rsplit('/',1)[1] for r in rows} for q in values(entities[league],'P17')})
types={q for league in {r['league']['value'].rsplit('/',1)[1] for r in rows} for q in values(entities[league],'P31')}
for depth in range(12):
 fetch(types)
 types.update(q for key in list(types) for q in values(entities[key],'P279'))
 pending=types-set(entities)
 if not pending:break
print(json.dumps({q:entities[q].get('labels',{}) for q in ['Q623109','Q15991303']},ensure_ascii=False),flush=True)
