import sys,json,hashlib,re,time,unicodedata,traceback,zipfile
from pathlib import Path
from dataclasses import asdict,replace
from datetime import datetime,timezone
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from wiki_stats.wiki_client import WikiClient
from wiki_stats.sofascore_client import SofascoreClient
from wiki_stats.api_provider import ProviderStore
from wiki_stats.state_store import StateStore
from wiki_stats.sofascore_cycle import prepare_player,LEAGUES,CUPS,EUROPEAN_CUPS
from wiki_stats.entity_resolver import EntityResolver
from wiki_stats.player_registry import check_player_identity
from wiki_stats.wikitext_parser import WikitextParser
from wiki_stats.models import PageSnapshot
from wiki_stats.change_validator import references,arithmetic_warnings
from wiki_stats.sbmatches_editor import read_matches
from wiki_stats.change_planner import plan_article
from wiki_stats.errors import UpdateError
def normalize(text):return ''.join(c for c in unicodedata.normalize('NFKD',str(text)).casefold() if c.isalnum())
def search_candidates(data,name):
    found={}
    def walk(obj):
        if isinstance(obj,dict):
            if type(obj.get('id')) is int and normalize(obj.get('name',''))==normalize(name):found[obj['id']]=obj
            for value in obj.values():walk(value)
        elif isinstance(obj,list):
            for value in obj:walk(value)
    walk(data);return list(found)

import gzip
base=Path(__file__).parent/'work-history'
root=base/'broad-test-v90'
out=base/'schick-cache-v93';out.mkdir(exist_ok=True)
rawcache={r['cache_key']:r for r in json.loads(gzip.decompress((base/'source-cache-modern-v91_final.json.gz').read_bytes()))}
class ReadWiki(WikiClient):
    def _api(self,params,post=False,endpoint=None):
        assert not post
        key=hashlib.sha256(json.dumps([endpoint,params],sort_keys=True).encode()).hexdigest()
        path=root/('wiki-'+key+'.json')
        if path.exists():return json.loads(path.read_text(encoding='utf8'))
        raise UpdateError('wiki_cache_missing','Нет сохранённого ответа идентичности: '+str(params))
class EmptyStore:
    def get(self,*a):return None
class CacheAPI(SofascoreClient):
    def __init__(self):self.store=EmptyStore();self.observed=[];self.case_requests=0;self.requests=0;self.offline=True
    def request(self,path,params,**kwargs):
        key=hashlib.sha256(json.dumps([path,params],sort_keys=True).encode()).hexdigest()
        cached=rawcache.get(key)
        self.observed.append({'path':path,'params':params,'cache':bool(cached)})
        if not cached:raise UpdateError('source_cache_missing','No cached response: '+path+' '+str(params))
        return cached['data']
    def quota_status(self):return {'football_api_requests':0,'mode':'cache_only'}
api=CacheAPI();wiki=ReadWiki(interval=1.5,timeout=12)
known={9:280609}
discovery=json.loads((root/'discovery.json').read_text())
selected=[row for row in discovery if not row.get('error') and row['number'] in (9,)]
results=[]
for item in selected:
    n=item['number'];folder=out/f'{n:02}';folder.mkdir(exist_ok=True)
    before=(root/f'{n:02}-before.wiki').read_text(encoding='utf-8')
    snapshot=PageSnapshot(item['title'],item['revid'],item['timestamp'],before,0,item.get('starttimestamp'))
    row={'number':n,'title':item['title'],'revid':item['revid'],'input':item,'wiki_edits':0,'release_sha256':'local-development'}
    api.case_requests=0;api.observed=[];api.offline=True
    preview=before;diff='';package=None;plan=None
    try:
        name=item.get('wiki_name') or ' '.join(reversed(item['title'].split(', ')))
        mapping={'qid':item['qid'],'title':item['title'],'wiki_name':name,'birth_date':item['birth_claims'][0]}
        # Structure is tested on the unmodified original, including cards which omit name.
        pass # Name omission is handled after verified binding
        if n in known:pid=known[n]
        else:
            found=search_candidates(api.search_players(item['en_name']),item['en_name'])
            row['search_candidate_ids']=found
            if len(found)!=1:raise UpdateError('test_identity_ambiguous','Поиск не дал единственного точного кандидата; автоматическая привязка запрещена')
            pid=found[0]
        profile=api.details(pid)
        if profile.get('date_of_birth')!=mapping['birth_date']:
            raise UpdateError('identity_mismatch','Дата рождения найденного игрока не совпадает с Wikidata')
        mapping['api_id']=pid;row['mapping']=mapping
        row['source_profile']={'id':pid,'name':profile.get('name'),'date_of_birth':profile.get('date_of_birth'),'position':profile.get('position'),'team':profile.get('team')}
        check_player_identity(wiki,mapping)
        package,plan,evidence=prepare_player(snapshot,mapping,api,wiki,EntityResolver(wiki),statistics_since=2020)
        row['collection']=evidence;row['changes']=[asdict(c) for c in plan.changes];row['warnings']=plan.warnings
        preview=plan.preview;diff=plan.diff
        api.offline=True
        _,second,second_evidence=prepare_player(replace(snapshot,text=preview),mapping,api,wiki,EntityResolver(wiki),statistics_since=2020)
        row['replay']={'diff_empty':not second.diff,'preview_equal':second.preview==preview,'statuses':{c.id:c.status for c in second.changes}}
    except Exception as exc:
        row.update(error=str(exc),code=getattr(exc,'code',type(exc).__name__))
        if not isinstance(exc,UpdateError):row['traceback']=traceback.format_exc()
    audit_baseline=before
    # Compare old numeric cells after a guarded lossless conversion. Raw
    # tables have no KlStat nodes, and club-spell merging changes row order.
    from wiki_stats.legacy_club_table import migration_operation,migrate
    from wiki_stats.klstat_normalization import operation as normalize_op,execute as normalize_table
    legacy=migration_operation(before,name,'2026-10-12')
    if legacy:audit_baseline=migrate(before,name,legacy)[0]
    normalized=normalize_op(audit_baseline,name,'2026-10-12')
    if normalized:audit_baseline=normalize_table(audit_baseline,name,normalized)[0]
    parser_before=WikitextParser(audit_baseline);parser_after=WikitextParser(preview)
    oldrows=lambda p:[str(t.node) for t in p.templates if t.name=='клстат/сезон' and re.search(r'20\d{2}',t.values().get('1').text if t.values().get('1') else '') and int(re.search(r'20\d{2}',t.values()['1'].text)[0])<2020]
    birthdays=lambda p:[v.text for t in p.templates if t.name=='футболист' for k,v in t.values().items() if k=='дата рождения']
    refs_before=references(before);refs_after=iter(references(preview))
    sb=[];sb_errors=[]
    for t in parser_after.templates:
        if t.name=='сбматчи':
            try:sb.append(len(read_matches(t)[1]))
            except Exception as exc:sb_errors.append(str(exc))
    arithmetic_before=arithmetic_warnings(before);arithmetic_after=arithmetic_warnings(preview)
    row['audit']={'refs_preserved':all(any(ref==other for other in refs_after) for ref in refs_before),
        'comments_preserved':re.findall('<!--.*?-->',before,re.S)==re.findall('<!--.*?-->',preview,re.S),
        'birthday_unchanged':birthdays(parser_before)==birthdays(parser_after),'old_seasons_preserved':sorted(oldrows(parser_before))==sorted(oldrows(parser_after)),
        'sbmatches_valid':not sb_errors,'sbmatches_counts':sb,'arithmetic_before':arithmetic_before,'arithmetic_after':arithmetic_after,
        'new_arithmetic_errors':[s for s in arithmetic_after if s not in arithmetic_before],
        'idempotent':row.get('replay',{}).get('diff_empty') is True and row.get('replay',{}).get('preview_equal') is True if plan else None}
    row['changed']=preview!=before;row['football_api_requests']=api.case_requests;row['source_observations']=api.observed
    if row.get('error'):row['verdict']='refused_or_failed'
    elif any(not row['audit'][k] for k in ['refs_preserved','comments_preserved','birthday_unchanged','old_seasons_preserved','sbmatches_valid','idempotent']) or row['audit']['new_arithmetic_errors']:row['verdict']='audit_failed'
    else:row['verdict']='partial_preview' if evidence.get('review') or any(c.status not in {'ready','already_applied'} for c in plan.changes) else 'preview_without_reported_gaps'
    (folder/'before.wiki').write_text(before,encoding='utf-8')
    (folder/'after.wiki').write_text(preview,encoding='utf-8')
    (folder/'changes.diff').write_text(diff,encoding='utf-8')
    if package:(folder/'package.json').write_text(json.dumps(package,ensure_ascii=False,indent=2),encoding='utf-8')
    (folder/'report.json').write_text(json.dumps(row,ensure_ascii=False,indent=2),encoding='utf-8')
    results.append(row)
    (out/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'n':n,'title':row['title'],'verdict':row['verdict'],'code':row.get('code'),'changed':row['changed'],'requests':api.case_requests,'total_requests':api.requests,'sb':sb},ensure_ascii=False),flush=True)
    pass




