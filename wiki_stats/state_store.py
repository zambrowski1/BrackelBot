# SPDX-License-Identifier: MIT
"""Persistent KV state. ToolsDB in production, SQLite for local development.

No secret values belong in records. SQL table names are fixed; all values bound.
The run lock is held on a dedicated connection for ToolsDB, or an OS file lock
for local SQLite. It is released by the OS/database if the process dies.
"""
import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from .errors import UpdateError


class StateStore:
    def __init__(self, path=None, *, connection=None, mysql=False):
        self.mysql = mysql
        self.path = Path(path or 'brackelbot.sqlite3').resolve()
        if connection is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = connection or sqlite3.connect(self.path, timeout=10)
        self.marker = '%s' if mysql else '?'
        cursor = self.db.cursor()
        ddl=('CREATE TABLE IF NOT EXISTS bb_state ('
             'bucket VARCHAR(64) NOT NULL, item_key VARCHAR(191) NOT NULL, '
             'payload LONGTEXT NOT NULL, PRIMARY KEY(bucket,item_key))')
        if mysql:
            ddl=ddl.replace('VARCHAR(64)','VARCHAR(64) CHARACTER SET ascii').replace('VARCHAR(191)','VARCHAR(191) CHARACTER SET ascii')
            ddl=ddl.replace('payload LONGTEXT','payload LONGTEXT CHARACTER SET utf8mb4')+' ENGINE=InnoDB'
        cursor.execute(ddl)
        self.db.commit()

    @classmethod
    def from_env(cls):
        if os.environ.get('BRACKELBOT_STORAGE', 'sqlite') != 'toolsdb':
            return cls(os.environ.get('BRACKELBOT_STATE', 'brackelbot.sqlite3'))
        import pymysql
        database = os.environ.get('BRACKELBOT_DB_NAME')
        user = os.environ.get('TOOL_TOOLSDB_USER')
        password = os.environ.get('TOOL_TOOLSDB_PASSWORD')
        if not user or not password:
            # Official shared-storage runtimes also expose these credentials in
            # the tool's replica.my.cnf. Never copy them into project state.
            import configparser
            config=configparser.ConfigParser(interpolation=None)
            config.read(Path(os.environ.get('TOOL_DATA_DIR',str(Path.home())))/'replica.my.cnf')
            user=user or config.get('client','user',fallback=None)
            password=password or config.get('client','password',fallback=None)
        if not database or not user or not password:
            raise UpdateError('storage_configuration', 'Для ToolsDB нужны BRACKELBOT_DB_NAME и системные TOOL_TOOLSDB_*')
        try:
            conn = pymysql.connect(host='tools.db.svc.wikimedia.cloud', user=user,
                                   password=password, database=database, charset='utf8mb4',
                                   connect_timeout=15, read_timeout=30, write_timeout=30)
            return cls(connection=conn, mysql=True)
        except pymysql.MySQLError:
            raise UpdateError('storage_unavailable', 'Соединение с ToolsDB не установлено') from None

    def get(self, bucket, key, default=None):
        cursor = self.db.cursor()
        cursor.execute(f'SELECT payload FROM bb_state WHERE bucket={self.marker} AND item_key={self.marker}', (bucket, str(key)))
        row = cursor.fetchone()
        self.db.commit()  # fresh reads of the kill switch, no stale snapshot
        return json.loads(row[0]) if row else default

    def put(self, bucket, key, value):
        from .schema_validator import reject_credentials
        reject_credentials(value)
        cursor = self.db.cursor()
        cursor.execute(f'REPLACE INTO bb_state(bucket,item_key,payload) VALUES({self.marker},{self.marker},{self.marker})',
                       (bucket, str(key), json.dumps(value, ensure_ascii=False)))
        self.db.commit()

    def items(self, bucket):
        cursor = self.db.cursor()
        cursor.execute(f'SELECT item_key,payload FROM bb_state WHERE bucket={self.marker} ORDER BY item_key', (bucket,))
        rows = cursor.fetchall()
        self.db.commit()
        return [(key, json.loads(value)) for key, value in rows]

    @contextmanager
    def run_lock(self):
        if self.mysql:
            cursor = self.db.cursor()
            # Named lock isolated by DB; holds across commits on this connection.
            cursor.execute('SELECT GET_LOCK(CONCAT(DATABASE(), ":brackelbot-cycle"), 0)')
            if cursor.fetchone()[0] != 1:
                raise UpdateError('run_in_progress', 'Другой цикл BrackelBot уже выполняется')
            try:
                yield
            finally:
                cursor.execute('SELECT RELEASE_LOCK(CONCAT(DATABASE(), ":brackelbot-cycle"))')
        else:
            file = self.path.with_suffix('.lock').open('a+b')
            file.seek(0); file.write(b'0'); file.flush(); file.seek(0)
            try:
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                file.close()
                raise UpdateError('run_in_progress', 'Другой цикл BrackelBot уже выполняется') from None
            try:
                yield
            finally:
                file.seek(0)
                if os.name == 'nt': msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
                else: fcntl.flock(file.fileno(), fcntl.LOCK_UN)
                file.close()

    def close(self):
        self.db.close()


class StoreAudit:
    def __init__(self, store, run_id):
        self.store, self.run_id = store, run_id

    def log(self, event, **details):
        from datetime import datetime, timezone
        from uuid import uuid4
        self.store.put('audit', uuid4().hex, {'run_id':self.run_id,
                       'timestamp':datetime.now(timezone.utc).isoformat(), 'event':event, **details})
