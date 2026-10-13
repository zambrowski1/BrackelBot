# SPDX-License-Identifier: MIT
"""Read-only smoke test of both subscriptions and shared cache; no wiki edits."""
import json
from pathlib import Path
from wiki_stats.api_provider import ProviderStore
from wiki_stats.state_store import StateStore
from wiki_stats.sofascore_combined import make_sofascore_client

def main():
    root=Path('/data/project/brackelbot/reports/combined-sofascore-20261013')
    root.mkdir(parents=True,exist_ok=True)
    store=ProviderStore(StateStore(root/'probe.sqlite3'),'sofascore')
    try:
        with store.run_lock():
            api=make_sofascore_client(store)
            profile=api.details(280609)
            stats=api.statistics(280609,35,97464,'overall')
            history=api.request('/players/matches',{'player_id':280609,'span':'last','page':1})
            before=api.quota_status()
            assert api.statistics(280609,35,97464,'overall')==stats
            after=api.quota_status()
            assert before==after,'Repeated seasonal read should not consume quota'
            report={'player':profile['name'],'jersey_number':profile.get('jersey_number'),
                    'season':{'appearances':stats['statistics'].get('appearances'),'goals':stats['statistics'].get('goals')},
                    'match_page_count':len(history['matches']),'cache_replay_without_requests':True,'quotas':after}
            (root/'live-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps(report,ensure_ascii=False),flush=True)
    finally:store.close()

if __name__=='__main__':main()
