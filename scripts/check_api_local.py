# SPDX-License-Identifier: MIT
"""Interactive account/season check. The key stays in this process only."""
import getpass
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from wiki_stats.api_football_client import ApiFootballClient
from wiki_stats.state_store import StateStore
from wiki_stats.errors import UpdateError


def main():
    if hasattr(sys.stdout,'reconfigure'): sys.stdout.reconfigure(encoding='utf-8')
    print('Проверка API-Football: только чтение /status и /leagues. Правок Википедии нет.')
    key=getpass.getpass('API-ключ (ввод скрыт; на диск не сохраняется): ')
    store=StateStore('local-api-check.sqlite3')
    try:
        api=ApiFootballClient(store,key=key)
        key=None
        print(json.dumps(api.status(),ensure_ascii=False,indent=2))
        print(json.dumps(api.leagues(2026)['data'],ensure_ascii=False,indent=2))
        return 0
    except UpdateError as exc:
        print(str(exc));return 1
    finally:
        key=None;store.close()


if __name__=='__main__': raise SystemExit(main())
