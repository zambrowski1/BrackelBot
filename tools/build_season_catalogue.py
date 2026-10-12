"""Discover worldwide ruwiki league seasons through complete category pagination.

Read-only Wikimedia requests. Cache every response for resumable, offline rebuilds.
"""
from pathlib import Path
from datetime import datetime,timezone
import sys,json,gzip,hashlib,re,argparse,time
from email.utils import parsedate_to_datetime
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from wiki_stats.wiki_client import WikiClient

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'handoff/season-catalogue'
DATA.mkdir(parents=True,exist_ok=True)
CACHE=DATA/'responses';CACHE.mkdir(exist_ok=True)
YEAR=re.compile(r'(?<!\d)((?:19|20)\d{2})(?:[/–—-]((?:19|20)\d{2}|\d{2}))?(?!\d)')
EXCLUDE=re.compile(r'кубк|кубок|суперкуб|лига чемпионов|лига европы|лига конференций|лига наций|чемпионат мира|чемпионат европы|квалификац|отбороч|футзал|мини-футбол|пляжн|футбольн\w*\s+клуб|сезоны.*клуб|Сезоны (?:Ж?ФК)\b|американск|^Категория:(?:Шаблоны|Изображения|Файлы|Википедия):',re.I)
cli=argparse.ArgumentParser();cli.add_argument('--offline',action='store_true');cli.add_argument('--interval',type=float,default=10);cli.add_argument('--end-year',type=int,default=datetime.now(timezone.utc).year);args=cli.parse_args()
END_YEAR=args.end_year
client=WikiClient(interval=max(6.5,args.interval),timeout=40);network=0;cache_hits=0

def read_api(params):
 # Only this read-only discovery job retries throttled reads. Publication policy
 # and the production WikiClient are deliberately unchanged.
 for attempt in range(5):
  time.sleep(max(0,client.interval-(time.monotonic()-client.last_request)))
  client.last_request=time.monotonic()
  response=client.session.get(client.endpoint,params={'format':'json','formatversion':2,'maxlag':5,**params},timeout=client.timeout)
  if response.status_code==429:
   retry=response.headers.get('Retry-After','60')
   try:delay=max(60,float(retry))
   except ValueError:
    try:delay=max(60,(parsedate_to_datetime(retry)-datetime.now(timezone.utc)).total_seconds())
    except (ValueError,TypeError):delay=60
   print(json.dumps({'throttled':True,'retry_after_seconds':delay,'attempt':attempt+1}),flush=True)
   if attempt==4:raise RuntimeError('Read discovery rate limit persisted after five delayed attempts')
   deadline=time.monotonic()+delay
   while time.monotonic()<deadline:time.sleep(max(0,min(30,deadline-time.monotonic())))
   continue
  response.raise_for_status();data=response.json()
  if 'error' in data:raise RuntimeError('Discovery API error: '+data['error'].get('code','unknown'))
  return data

def api(params):
 global network,cache_hits
 key=hashlib.sha256(json.dumps(params,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
 path=CACHE/(key+'.json.gz')
 if path.exists():cache_hits+=1;return json.loads(gzip.decompress(path.read_bytes()))['response']
 if args.offline:raise RuntimeError('Missing cached query: '+str(params))
 try:response=read_api(params)
 except Exception as exc:
  (DATA/'last-error.json').write_text(json.dumps({'params':params,'code':getattr(exc,'code',type(exc).__name__),'checked_at':datetime.now(timezone.utc).isoformat()},ensure_ascii=False,indent=2),encoding='utf8')
  raise
 path.write_bytes(gzip.compress(json.dumps({'params':params,'checked_at':datetime.now(timezone.utc).isoformat(),'response':response},ensure_ascii=False).encode()))
 network+=1
 return response

def all_categories(prefix):
 result=[];continuation={};seen=set()
 while True:
  response=api({'action':'query','list':'allcategories','acprefix':prefix,'aclimit':500,**continuation})
  result.extend(p['category'] for p in response['query']['allcategories'])
  continuation=response.get('continue',{})
  if not continuation:break
  token=json.dumps(continuation,sort_keys=True)
  if token in seen:raise RuntimeError('Repeated category continuation')
  seen.add(token)
 return result

def in_range(title):
 matches=list(YEAR.finditer(title))
 return not matches or any(2010<=int(m[1])<=END_YEAR for m in matches)

seasons=all_categories('Сезоны')
national=all_categories('Национальные футбольные чемпионаты')
seeds={'Категория:'+name for name in national if in_range(name)}
for name in seasons:
 if EXCLUDE.search(name):continue
 if 'футбол' in name.lower() or name in {'Сезоны MLS','Сезоны Бундеслиги','Сезоны A-лиги','Сезоны Джей-лиги','Сезоны Джей-лиги 1','Сезоны Джей-лиги 2','Сезоны Джей-лиги 3'}:
  if in_range(name):seeds.add('Категория:'+name)
queue=sorted(seeds);visited=set();pages={};excluded={};edges={};position=0
print(json.dumps({'seed_categories':len(seeds)},ensure_ascii=False),flush=True)
while position<len(queue):
 category=queue[position];position+=1
 if category in visited:continue
 visited.add(category);continuation={};seen=set();members=[]
 while True:
  response=api({'action':'query','list':'categorymembers','cmtitle':category,'cmlimit':500,'cmtype':'page|subcat',**continuation})
  members.extend(response['query']['categorymembers']);continuation=response.get('continue',{})
  if not continuation:break
  token=json.dumps(continuation,sort_keys=True)
  if token in seen:raise RuntimeError('Repeated member continuation')
  seen.add(token)
 edges[category]=[]
 for member in members:
  title=member['title']
  if member['ns']==14:
   if EXCLUDE.search(title) or not in_range(title):continue
   edges[category].append(title)
   if title not in visited:queue.append(title)
  elif member['ns']==0 and any(2010<=int(m[1])<=END_YEAR for m in YEAR.finditer(title)):
   if EXCLUDE.search(title):excluded[title]='cup/international/futsal/club category';continue
   row=pages.setdefault(title,{'pageid':member['pageid'],'title':title,'discovered_in':[]})
   if category not in row['discovered_in']:row['discovered_in'].append(category)
 if len(visited)%20==0:
  print(json.dumps({'categories':len(visited),'pending':len(queue)-position,'pages':len(pages),'new_wiki_requests':network}),flush=True)
  (DATA/'progress.json').write_text(json.dumps({'categories':len(visited),'pages':len(pages),'pending':len(queue)-position}),encoding='utf8')

result={'checked_at':datetime.now(timezone.utc).isoformat(),'start_year':2010,'end_year':END_YEAR,
 'endpoint':client.endpoint,'seed_categories':sorted(seeds),'visited_categories':sorted(visited),
 'category_edges':edges,'pages':sorted(pages.values(),key=lambda p:p['title']),'excluded':excluded,
 'category_traversal_complete':True,'new_wiki_requests_this_run':network,'cache_hits':cache_hits,
 'football_provider_requests':0,'wikipedia_writes':0}
(DATA/'discovery.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
(DATA/'progress.json').write_text(json.dumps({'categories':len(visited),'pages':len(pages),'pending':0,'done':True}),encoding='utf8')
(DATA/'last-error.json').unlink(missing_ok=True)
print(json.dumps({'done':True,'categories':len(visited),'pages':len(pages),'network':network}),flush=True)
