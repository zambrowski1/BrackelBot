# SPDX-License-Identifier: MIT
"""Check catalogue identities in Wikimedia APIs, without a statistics API key."""
import argparse
import json
from pathlib import Path
from wiki_stats.entity_resolver import EntityResolver
from wiki_stats.wiki_client import WikiClient
from wiki_stats.errors import UpdateError
from wiki_stats.api_football_client import utcnow


def main():
    parser=argparse.ArgumentParser(description='Проверить статьи клубов и связи Wikidata')
    parser.add_argument('--tier',type=int,choices=(1,2,3))
    parser.add_argument('--report',default='catalogue-verification.json')
    args=parser.parse_args()
    resolver=EntityResolver(WikiClient())
    report={'checked_at':utcnow(),'clubs':[]}
    failures=0
    for club in resolver.catalogue:
        if args.tier and club.get('league_tier')!=args.tier: continue
        try:
            resolved=resolver.resolve({'name':club['name'],'kind':'club','wikidata_id':club['qid'],'page_title':club['title']})
            item={'qid':club['qid'],'name':club['name'],'status':'verified','wikitext':resolved.wikitext}
        except UpdateError as exc:
            item={'qid':club['qid'],'name':club['name'],'status':exc.code,'message':str(exc)}
            failures+=1
        report['clubs'].append(item)
        print(club['qid'],item['status'],flush=True)
    Path(args.report).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return int(failures>0)


if __name__=='__main__': raise SystemExit(main())
