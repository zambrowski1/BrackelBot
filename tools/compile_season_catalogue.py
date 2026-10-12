"""Compile saved worldwide discoveries into an exact league-season registry."""
from pathlib import Path
from datetime import datetime,timezone
from urllib.parse import unquote
import sys,json,gzip,re,argparse,csv,collections
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from wiki_stats.season_catalogue import parse_season_title

ROOT=Path(__file__).resolve().parents[1];DATA=ROOT/'handoff/season-catalogue'
cli=argparse.ArgumentParser();cli.add_argument('--allow-incomplete',action='store_true');args=cli.parse_args()
EXCLUDE=re.compile(r'кубк|кубок|суперкуб|\bcup\b|\bcopa\b|nextgen|world football challenge|лига чемпионов|лига европы|лига конференций|лига наций|чемпионат мира|чемпионат европы|квалификац|отбороч|футзал|мини-футбол|пляжн|американск|^Футбол в |^Сезон.*футбольного клуба|\bЖ?ФК\b|сборн\w*.*в сезоне|\bсоставы\b',re.I)
entities=json.loads(gzip.decompress((DATA/'wikidata-entities.json.gz').read_bytes()))
bindings=json.loads((DATA/'wikidata-results.json').read_text(encoding='utf8'))['results']['bindings']
related=DATA/'related-season-results.json'
if related.exists():bindings+=json.loads(related.read_text(encoding='utf8'))['results']['bindings']
def values(qid,prop):
 return [s['mainsnak']['datavalue']['value']['id'] for s in entities.get(qid,{}).get('claims',{}).get(prop,[]) if s['mainsnak'].get('datavalue',{}).get('type')=='wikibase-entityid' and s.get('rank')!='deprecated']
def is_league(qid):
 todo=values(qid,'P31');seen=set()
 while todo:
  key=todo.pop()
  if key in {'Q623109','Q15991303'}:return True
  if key in seen:continue
  seen.add(key);todo.extend(values(key,'P279'))
 return False
def label(qid):
 labels=entities.get(qid,{}).get('labels',{})
 return labels.get('ru',labels.get('en',{})).get('value',qid)
def ru_title(qid):return entities.get(qid,{}).get('sitelinks',{}).get('ruwiki',{}).get('title')
def title_url(value):return unquote(value.split('/wiki/',1)[1]).replace('_',' ')

discovery_path=DATA/'discovery.json'
if discovery_path.exists():discovery=json.loads(discovery_path.read_text(encoding='utf8'))
elif args.allow_incomplete:
 pages={};edges={}
 for path in (DATA/'responses').glob('*.gz'):
  record=json.loads(gzip.decompress(path.read_bytes()));params=record['params']
  if params.get('list')!='categorymembers':continue
  category=params['cmtitle'];edges.setdefault(category,[])
  for member in record['response']['query']['categorymembers']:
   if member['ns']==14:edges[category].append(member['title'])
   elif member['ns']==0:
    row=pages.setdefault(member['title'],{'title':member['title'],'pageid':member['pageid'],'discovered_in':[]})
    row['discovered_in'].append(category)
 discovery={'pages':list(pages.values()),'category_edges':edges,'visited_categories':list(edges),'category_traversal_complete':False}
else:raise SystemExit('Category discovery has not completed; do not publish an incomplete coverage claim.')
END_YEAR=discovery.get('end_year',datetime.now(timezone.utc).year)

# National championship and division categories prove domestic league context.
edges=discovery['category_edges']
strong={cat for cat in edges if 'Национальные футбольные чемпионаты' in cat or 'дивизионов футбольных лиг' in cat or 'высших футбольных лиг' in cat or ('Сезоны чемпионат' in cat and 'футбол' in cat)}
todo=list(strong)
while todo:
 for child in edges.get(todo.pop(),[]):
  if child not in strong:strong.add(child);todo.append(child)
pages={row['title']:row for row in discovery['pages']}
by_title=collections.defaultdict(list)
for row in bindings:by_title[title_url(row['article']['value'])].append(row)
titles=set(pages)|set(by_title);entries=[];aliases=collections.defaultdict(set);leagues={};review=[];excluded=[]
domestic_proofs=collections.defaultdict(set)
for title,source in pages.items():
 parsed=parse_season_title(title,end=END_YEAR)
 if not parsed or EXCLUDE.search(title):continue
 if not strong.intersection(source['discovered_in']):continue
 for row in by_title.get(title,[]):
  qid=row['league']['value'].rsplit('/',1)[1]
  if 'Q2736' in values(qid,'P641') and ru_title(qid)==parsed[1]:domestic_proofs[qid].add(title)
for title in sorted(titles):
 parsed=parse_season_title(title,end=END_YEAR)
 if not parsed:
  review.append({'title':title,'reason':'outside_range_or_nonstandard_season'});continue
 if EXCLUDE.search(title):excluded.append({'title':title,'reason':'cup/international/futsal'});continue
 season,hint=parsed;source=pages.get(title);rows=by_title.get(title,[])
 linked={r['league']['value'].rsplit('/',1)[1] for r in rows}
 proven={qid for qid in linked if is_league(qid) or qid in domestic_proofs}
 domestic=bool(source and strong.intersection(source['discovered_in']))
 if not proven and not domestic:
  review.append({'title':title,'reason':'league_type_unproven','related_items':sorted(linked)});continue
 keys=proven or {'title:'+hint};countries=set()
 for qid in proven:countries.update(values(qid,'P17'))
 for row in rows:
  if row['league']['value'].rsplit('/',1)[1] in proven and 'country' in row:countries.add(row['country']['value'].rsplit('/',1)[1])
 entry={'title':title,'season':season,'league_keys':sorted(keys),'country_qids':sorted(countries),
  'proof':{'wikipedia_categories':sorted(set(source['discovered_in'])) if source else [],'wikidata_season_qids':sorted({r['season']['value'].rsplit('/',1)[1] for r in rows})}}
 if source:entry['pageid']=source['pageid']
 entries.append(entry)
 for key in keys:
  canonical=ru_title(key) if key.startswith('Q') else hint
  leagues.setdefault(key,{'key':key,'title':canonical or label(key),'country_qids':sorted(countries),'aliases':set(),
   'classification':{'wikidata_league_type':is_league(key),'national_season_titles':sorted(domestic_proofs.get(key,[]))}})
  for alias in {hint,canonical} - {None,''}:
   aliases[alias].add(key);leagues[key]['aliases'].add(alias)
redirect_file=DATA/'league-redirects.json'
redirects=json.loads(redirect_file.read_text(encoding='utf8'))['redirects'] if redirect_file.exists() else {}
for key,league in leagues.items():
 for alias in redirects.get(league['title'],[]):
  aliases[alias].add(key);league['aliases'].add(alias)
 league['aliases']=sorted(league['aliases'])
catalogue={'schema_version':1,'checked_at':datetime.now(timezone.utc).isoformat(),'start_year':2010,'end_year':END_YEAR,
 'coverage':{'category_traversal_complete':discovery['category_traversal_complete'],'categories':len(discovery['visited_categories']),
 'discovered_titles':len(titles),'included_season_articles':len(entries),'league_entities_or_title_groups':len(leagues),
 'countries_with_wikidata_identity':len({q for row in entries for q in row['country_qids']}),
 'limitation':'Complete traversal of discovered category roots plus Wikidata league-season query; uncategorized or untyped pages cannot be guaranteed.'},
 'aliases':{key:sorted(value) for key,value in sorted(aliases.items())},'leagues':sorted(leagues.values(),key=lambda r:r['title']),
 'countries':{q:label(q) for q in sorted({q for row in entries for q in row['country_qids']})},'entries':entries}
(ROOT/'wiki_stats/season_catalogue.json').write_text(json.dumps(catalogue,ensure_ascii=False,indent=2),encoding='utf8')
(DATA/'review.json').write_text(json.dumps({'review':review,'excluded':excluded},ensure_ascii=False,indent=2),encoding='utf8')
with (DATA/'catalogue.csv').open('w',encoding='utf-8-sig',newline='') as stream:
 writer=csv.writer(stream);writer.writerow(['Сезон','Статья','Лига/группы','Страны Wikidata','Основание'])
 for row in entries:writer.writerow([row['season'],row['title'],'; '.join(leagues[k]['title'] for k in row['league_keys']),'; '.join(label(q) for q in row['country_qids']),'categories' if row.get('pageid') else 'Wikidata sitelink'])
with (DATA/'coverage.csv').open('w',encoding='utf-8-sig',newline='') as stream:
 writer=csv.writer(stream);writer.writerow(['Лига','Год начала','Найденные статьи (пусто — не доказано отсутствие)'])
 for key,league in sorted(leagues.items(),key=lambda pair:pair[1]['title']):
  own=[row for row in entries if key in row['league_keys']]
  for year in range(2010,END_YEAR+1):writer.writerow([league['title'],year,'; '.join(row['title'] for row in own if row['season'].startswith(str(year)))])
print(json.dumps(catalogue['coverage'],ensure_ascii=False),flush=True)
