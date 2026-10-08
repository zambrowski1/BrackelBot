# SPDX-License-Identifier: MIT
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4
from dataclasses import asdict
from .errors import UpdateError
from .models import ChangeResult, ArticlePlan, TransactionGroup
from .diff_generator import generate_diff
from .transactions import minimal_patches, plan_fingerprint


def default_backup_dir():
    return Path(os.environ.get('LOCALAPPDATA',str(Path.cwd())))/'WikipediaStatsUpdater'/'backups'


def save_backup(snapshot, directory=None):
    if snapshot.title!='Участник:Zambrowski/testbot':
        raise UpdateError('page_not_allowed','Резервные копии публикации допустимы только для тестовой страницы')
    path=Path(directory) if directory else default_backup_dir()
    path.mkdir(parents=True,exist_ok=True)
    data=asdict(snapshot)
    data['format']='brackelbot-backup-1'
    data['sha256']=hashlib.sha256(snapshot.text.encode('utf-8')).hexdigest()
    file=path/f'testbot-rev-{snapshot.revid}-{uuid4().hex[:12]}.json'
    with file.open('x',encoding='utf-8') as stream:
        json.dump(data,stream,ensure_ascii=False,indent=2)
    return file


def plan_restore(client, backup_path):
    try:
        path=Path(backup_path)
        if path.stat().st_size>10_000_000:
            raise ValueError('Размер файла')
        data=json.loads(path.read_text(encoding='utf-8'))
        if data.get('format')!='brackelbot-backup-1' or data.get('title')!='Участник:Zambrowski/testbot':
            raise ValueError('Не резервная копия тестовой страницы')
        text=data['text']
        if not isinstance(text,str) or hashlib.sha256(text.encode('utf-8')).hexdigest()!=data['sha256']:
            raise ValueError('Контрольная сумма не совпала')
    except (OSError,ValueError,KeyError,TypeError):
        raise UpdateError('invalid_backup','Некорректная резервная копия тестовой страницы') from None
    snapshot=client.fetch_page('Участник:Zambrowski/testbot')
    if snapshot.title!='Участник:Zambrowski/testbot' or snapshot.namespace!=2:
        raise UpdateError('page_not_allowed','Получена страница вне белого списка')
    diff=generate_diff(snapshot.text,text,snapshot.title)
    patches=minimal_patches(snapshot.text,text,'restore')
    status='ready' if patches else 'already_applied'
    operation={'id':'restore','type':'restore','backup_revid':data['revid'],'backup_sha256':data['sha256']}
    change=ChangeResult('restore',operation,status,'Восстановление всей тестовой страницы из локальной копии',patches,diff,group_id='restore')
    group=TransactionGroup('restore',['restore'],patches,diff,status)
    plan=ArticlePlan(snapshot,[change],text,diff,[],bool(patches),{},'1.1',[group],kind='restore')
    plan.content_fingerprint=plan_fingerprint(plan)
    return plan
