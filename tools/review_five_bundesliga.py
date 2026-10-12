"""Review five unmodified live article snapshots using recorded football replies."""
import argparse
import gzip
import hashlib
import html
import json
import sys
from collections import Counter
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.replay_four_infoboxes import CachedAPI, ReadWiki, card
from wiki_stats.sofascore_cycle import prepare_player
from wiki_stats.models import PageSnapshot
from wiki_stats.entity_resolver import EntityResolver
from wiki_stats.change_validator import arithmetic_warnings, references
from wiki_stats.wikitext_parser import WikitextParser
from wiki_stats.wiki_client import WikiClient
from wiki_stats.errors import UpdateError

OUT=ROOT/'handoff/2026-10-13/five-bundesliga-review'
CASES=[('Кобель',556866),('Кариус',174135),('Раум',856076),('Орбан',136924),('Штиллер',901882)]


def load_cache(path):
    raw=path.read_bytes()
    data=json.loads(gzip.decompress(raw) if path.suffix=='.gz' else raw)
    if isinstance(data,list):return {r['cache_key']:r['data'] for r in data}
    return {k:v['data'] if isinstance(v,dict) and set(v)>={'at','data'} else v for k,v in data.items()}


class RecordedAPI(CachedAPI):
    def __init__(self,cache):
        super().__init__(cache);self.used={}
    def request(self,path,params,**kwargs):
        value=super().request(path,params,**kwargs)
        key=hashlib.sha256(json.dumps([path,params],sort_keys=True).encode()).hexdigest()
        self.used[key]=value
        return value


class ReviewWiki(ReadWiki):
    def __init__(self,refresh):
        super().__init__(refresh)
        self.recorded=json.loads((OUT/'wikimedia-evidence.json').read_text(encoding='utf8')) if (OUT/'wikimedia-evidence.json').exists() else {}
        self.fresh.update(self.recorded)
    def _api(self,params,post=False,endpoint=None):
        try:value=super()._api(params,post=post,endpoint=endpoint)
        except UpdateError as exc:
            if not self.refresh or exc.code!='wiki_source_unavailable':raise
            value=WikiClient._api(self,params,post=False,endpoint=endpoint)
        key=hashlib.sha256(json.dumps([endpoint,params],sort_keys=True).encode()).hexdigest()
        self.recorded[key]=value
        self.fresh[key]=value
        return value


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--cache',type=Path,default=OUT/'used-football-cache.json.gz')
    parser.add_argument('--extra-cache',type=Path)
    parser.add_argument('--refresh-wiki',action='store_true')
    args=parser.parse_args()
    OUT.mkdir(exist_ok=True,parents=True)
    cache=load_cache(args.cache)
    if args.extra_cache:cache.update(load_cache(args.extra_cache))
    api=RecordedAPI(cache);wiki=ReviewWiki(args.refresh_wiki);results=[]
    now=datetime(2026,10,13,tzinfo=timezone.utc)
    for short,pid in CASES:
        wiki.reads=0;api.missing=[]
        snap=PageSnapshot(**json.loads((OUT/f'{short}-snapshot.json').read_text(encoding='utf8')))
        (OUT/f'{short}-до.wiki').write_text(snap.text,encoding='utf8')
        row={'player':short,'title':snap.title,'revision':snap.revid,'revision_timestamp':snap.timestamp,'football_api_requests':0}
        preview=snap.text;diff=''
        try:
            profile=api.details(pid)
            identity=wiki.get_page_identity(snap.title)
            box=next(t.values() for t in WikitextParser(snap.text).templates if t.name=='футболист')
            name=box['имя'].text.strip() if 'имя' in box and box['имя'].text.strip() else ' '.join(reversed(snap.title.split(', ')))
            mapping={'api_id':pid,'title':snap.title,'qid':identity['qid'],'wiki_name':name,'birth_date':profile['date_of_birth']}
            row.update(mapping=mapping,source_team=profile.get('team'),before_card=card(snap.text,name))
            package,plan,evidence=prepare_player(snap,mapping,api,wiki,EntityResolver(wiki),statistics_since=0,max_requests=150,now=now)
            preview=plan.preview;diff=plan.diff
            (OUT/f'{short}-package.json').write_text(json.dumps(package,ensure_ascii=False,indent=2),encoding='utf8')
            _,second,_=prepare_player(replace(snap,text=preview),mapping,api,wiki,EntityResolver(wiki),statistics_since=0,max_requests=150,now=now)
            old_refs=iter(references(preview))
            row.update(after_card=card(preview,name),changes=[asdict(c) for c in plan.changes],review=evidence.get('review',[]),
                       idempotent=second.preview==preview,references_preserved=all(any(ref==other for other in old_refs) for ref in references(snap.text)),
                       new_arithmetic_errors=[v for v in arithmetic_warnings(preview) if v not in arithmetic_warnings(snap.text)])
            if short in {'Кариус','Раум'}:
                parsed=WikitextParser(preview);table=parsed.club_table()
                checks={'club_table_operations_passed':all(c.status in {'ready','already_applied'} for c in plan.changes if c.operation.get('target',{}).get('structure')=='club_table')}
                if short=='Кариус':
                    pair=parsed.career('клубы',name)[-1][2].numeric(pair=True)
                    checks['winter_arrival_total']=[n.value for n in pair]==[39,-35]
                    checks['promoted_season_present']=any('Шальке' in r.club and r.season.endswith('2026/2027]]') or 'Шальке' in r.club and r.season=='2026/27' for r in table.seasons)
                else:
                    pair=parsed.career('национальная сборная',name)[-1][2].numeric(pair=True)
                    checks['senior_card_total']=[n.value for n in pair]==[40,1]
                    checks['senior_card_date']=row['after_card']['обновление данных о сборной']=='04.10.2026'
                    old_tables=[str(n) for n in WikitextParser(snap.text).code.filter_tags() if str(n.tag)=='table']
                    checks['original_goals_table_preserved']=all(t in preview for t in old_tables)
                    checks['no_duplicate_sbmatches']=sum(t.name=='сбматчи' for t in parsed.templates)==sum(t.name=='сбматчи' for t in WikitextParser(snap.text).templates)
                row['regression_checks']=checks
        except Exception as exc:
            row.update(error=str(exc),code=getattr(exc,'code',type(exc).__name__))
        row['changed']=preview!=snap.text;row['missing_cached_requests']=api.missing
        (OUT/f'{short}-после.wiki').write_text(preview,encoding='utf8')
        (OUT/f'{short}-изменения.diff').write_text(diff,encoding='utf8')
        results.append(row)
        (OUT/'report.json').write_text(json.dumps({'wikipedia_edits':0,'articles':results},ensure_ascii=False,indent=2),encoding='utf8')
        (OUT/'wikimedia-evidence.json').write_text(json.dumps(wiki.recorded,ensure_ascii=False),encoding='utf8')
        print(json.dumps({k:row.get(k) for k in ['player','changed','error','idempotent','references_preserved','new_arithmetic_errors']},ensure_ascii=False),flush=True)
    (OUT/'used-football-cache.json.gz').write_bytes(gzip.compress(json.dumps(api.used,ensure_ascii=False).encode('utf8')))
    lines=['# Пять статей Бундеслиги — вывод бота','',
           'Исходники скачаны из живых статей Википедии 13 октября 2026 года. Тексты после обработки не исправлялись вручную. Публикации в Википедию нет. Футбольные данные — сохранённые ответы API; у Штиллера новый ограниченный сбор описан в collection.json.','',
           'Для каждой статьи есть исходный текст «до», полный текст «после» и diff. Пропуски и отказы перечислены ниже и в report.json.','']
    for row in results:
        short=row['player'];lines+=['## '+row['title'],'',f"Ревизия исходника: {row['revision']}. Изменён: {'да' if row['changed'] else 'нет'}.",'']
        for label,key in [('До','before_card'),('После','after_card')]:
            value=row.get(key,{})
            lines += [label+':','```json',json.dumps(value,ensure_ascii=False,indent=2),'```','']
        if row.get('error'):lines+=['Отказ: '+row['error'],'']
        blocked=[{'operation':v['operation']['type'],'status':v['status'],'reason':v['message']} for v in row.get('changes',[]) if v['status'] not in {'ready','already_applied'}]
        if blocked:lines+=['Отклонённые действия:','```json',json.dumps(blocked,ensure_ascii=False,indent=2),'```','']
        review=Counter(v['code'] for v in row.get('review',[]))
        if review:lines+=['Замечания источников (полные причины в report.json): '+', '.join(f'{k}: {v}' for k,v in review.items()),'']
        lines += [f"Повторный запуск: {row.get('idempotent')}; сноски сохранены: {row.get('references_preserved')}; новые арифметические ошибки: {row.get('new_arithmetic_errors')}.",'']
    (OUT/'README.md').write_text('\n'.join(lines),encoding='utf8')
    with ZipFile(OUT/'five-articles.zip','w',ZIP_DEFLATED) as archive:
        for p in sorted(OUT.iterdir()):
            if p.suffix in {'.wiki','.diff','.md'} or p.name in {'report.json','collection.json'}:archive.write(p,p.name)
    return 0 if all(not r.get('error') and r.get('idempotent') and r.get('references_preserved') and not r.get('new_arithmetic_errors') and all(r.get('regression_checks',{}).values()) for r in results) else 1


if __name__=='__main__':sys.exit(main())
