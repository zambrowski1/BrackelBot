"""Update saved drafts using Wikipedia season identities, never football APIs or edits."""
from pathlib import Path
from datetime import datetime, timezone
from types import SimpleNamespace
import argparse, json, sys, hashlib, zipfile, difflib
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wiki_stats.season_links import season_rows, season_key, candidates, prepare, execute
from wiki_stats.wiki_client import WikiClient
from wiki_stats.wikitext_parser import WikitextParser
from wiki_stats.change_validator import references, arithmetic_warnings
from wiki_stats.errors import UpdateError

ROOT=Path(__file__).resolve().parents[1]
LATEST=ROOT/'handoff/2026-10-12/latest'
parser=argparse.ArgumentParser()
parser.add_argument('--refresh-evidence', action='store_true')
parser.add_argument('--check', action='store_true', help='Check idempotence without modifying saved artifacts')
args=parser.parse_args()
day='2026-10-12'
paths=sorted((LATEST/'articles').glob('*готовая статья.wiki'))
titles=set();leagues=set()
for path in paths:
 for club,league,value in season_rows(path.read_text(encoding='utf8')):
  leagues.add(league)
  key=season_key(value.text)
  if 2010<=int(key[:4])<=2026:titles.update(candidates(league,value.text)[0])
print(json.dumps({'candidate_titles':len(titles),'leagues':sorted(leagues)},ensure_ascii=False),flush=True)
cache_path=LATEST/'season-link-evidence.json'
if args.refresh_evidence:
 client=WikiClient(timeout=20)
 identities={};responses=[];ordered=sorted(titles)
 for index in range(0,len(ordered),10):
  batch=ordered[index:index+10]
  response=client._api({'action':'query','prop':'info','redirects':1,'titles':'|'.join(batch)})
  responses.append(response)
  query=response['query'];pages={p['title']:p for p in query['pages']}
  aliases={p['from']:p['to'] for kind in ['normalized','redirects'] for p in query.get(kind,[])}
  for requested in batch:
   title=requested;seen=set()
   while title in aliases and title not in seen:seen.add(title);title=aliases[title]
   page=pages.get(title)
   identities[requested]=page if page and 'missing' not in page and page.get('ns')==0 else None
  print('Wikipedia batch',index//10+1,flush=True)
 cache_path.write_text(json.dumps({'checked_at':datetime.now(timezone.utc).isoformat(),'endpoint':client.endpoint,'identities':identities,'responses':responses},ensure_ascii=False,indent=2),encoding='utf8')
cache=json.loads(cache_path.read_text(encoding='utf8'))
class CachedWiki:
 def get_page_identity(self,title):
  if title not in cache['identities']:raise UpdateError('season_identity_not_cached','No cached Wikipedia answer: '+title)
  page=cache['identities'][title]
  if page is None:raise UpdateError('entity_not_found','Season article missing')
  return page
wiki=CachedWiki();resolver=SimpleNamespace(client=wiki);results=[]
for path in paths:
 before=path.read_text(encoding='utf8');p=WikitextParser(before)
 name=next(t.values()['имя'].text.strip() for t in p.templates if t.name=='футболист')
 op,issues=prepare(before,name,day,wiki)
 after,status=execute(before,name,op,resolver) if op else (before,'already_applied')
 assert references(before)==references(after)
 assert arithmetic_warnings(before)==arithmetic_warnings(after)
 assert prepare(after,name,day,wiki)[0] is None
 if op:assert execute(after,name,op,resolver)==(after,'already_applied')
 # All non-season cells remain byte-identical after replacing season values with a sentinel.
 def without_seasons(text):
  rows=season_rows(text)
  for _,_,value in sorted(rows,key=lambda row:row[2].start,reverse=True):
   text=text[:value.start]+'<SEASON>'+text[value.start+len(value.text):]
  return text
 assert without_seasons(before)==without_seasons(after)
 result={'file':path.name,'status':status,'changed_seasons':len(op['payload']['seasons']) if op else 0,'issues':issues,'references_preserved':True,'non_season_content_preserved':True,'idempotent':True,'before_sha256':hashlib.sha256(before.encode()).hexdigest(),'after_sha256':hashlib.sha256(after.encode()).hexdigest()}
 results.append(result)
 if args.check:
  assert op is None, 'Saved draft still needs season formatting: '+path.name
  print(json.dumps(result,ensure_ascii=False),flush=True)
  continue
 if op:
  diff=''.join(difflib.unified_diff(before.splitlines(True),after.splitlines(True),fromfile=path.name+' before',tofile=path.name+' after'))
  (LATEST/(path.stem+' — seasons.diff')).write_text(diff,encoding='utf8')
 path.write_text(after,encoding='utf8')
 print(json.dumps(result,ensure_ascii=False),flush=True)
if not args.check:
 (LATEST/'season-link-report.json').write_text(json.dumps({'football_provider_requests':0,'wikipedia_edits':0,'wiki_read_batches':len(cache['responses']),'articles':results},ensure_ascii=False,indent=2),encoding='utf8')
 with zipfile.ZipFile(LATEST/'four-articles.zip','w',zipfile.ZIP_DEFLATED) as archive:
  for path in sorted((LATEST/'articles').iterdir()):
   if path.is_file():archive.write(path,path.name)
