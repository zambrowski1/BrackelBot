# SPDX-License-Identifier: MIT
"""Explicit community task approval and per-page allowlists; default is no writes."""
import os
from dataclasses import asdict
from datetime import datetime, timezone
from .errors import UpdateError
from .publisher import Publisher
from .models import Mode
from .transactions import TEST_TITLE, digest, plan_fingerprint


LOW_RISK = {'update_stats','update_totals','update_date'}


class PublicationPolicy:
    def __init__(self, store, mode='dry_run', enabled=None):
        if mode not in {'dry_run','manual','test','automatic'}:
            raise UpdateError('invalid_mode','Неизвестный режим публикации')
        self.store, self.mode = store, mode
        self.enabled = (lambda:os.environ.get('BRACKELBOT_PUBLICATION')=='1') if enabled is None else enabled

    def approval(self):
        return self.store.get('policy','community_approval',{})

    def gate_page(self, title):
        if self.mode == 'dry_run' or not self.enabled() or self.store.get('policy','kill_switch',True):
            raise UpdateError('publishing_disabled','Публикация отключена режимом, окружением или аварийным выключателем')
        if title==TEST_TITLE and self.mode in {'test','manual'}: return
        if self.mode=='test': raise UpdateError('page_not_allowed','Test разрешает только Участник:Zambrowski/testbot')
        approval=self.approval()
        if (not approval.get('community_approved') or not approval.get('operator_enabled')
            or title not in approval.get('titles',[]) or not title or ':' in title
            or not approval.get('approval_url','').startswith('https://ru.wikipedia.org/wiki/')
            or approval.get('league')!=78):
            raise UpdateError('page_not_allowed','Статья не включена в явно одобренную ботозадачу')

    def gate_plan(self, plan):
        self.gate_page(plan.snapshot.title)
        if plan.snapshot.title==TEST_TITLE and self.mode in {'test','manual'}: return
        approval=self.approval()
        if plan.kind!='update' or any(c.operation['type'] not in approval.get('operations',[]) for c in plan.changes):
            raise UpdateError('task_not_approved','Тип правки не разрешён в одобренной задаче')
        # Structural/high-risk edits are manual even after generic task approval.
        if self.mode=='automatic' and any(c.operation['type'] not in LOW_RISK for c in plan.changes):
            raise UpdateError('needs_review','Повышенный риск требует индивидуального подтверждения')
        evidence=self.store.get('plan_evidence',plan.content_fingerprint)
        if not evidence or evidence.get('plan_hash')!=plan_fingerprint(plan) or evidence.get('league')!=78:
            raise UpdateError('source_unverified','Нет подтверждённого серверного происхождения плана')
        age=(datetime.now(timezone.utc)-datetime.fromisoformat(evidence['fetched_at'])).total_seconds()
        if not 0<=age<=86400 or evidence.get('season') not in approval.get('seasons',[]):
            raise UpdateError('source_stale','Покрытие сезона не одобрено или данные старше суток')
        mapping=self.store.get('players',evidence['player_id'])
        if evidence.get('kind')=='transfer':
            if self.mode!='manual' or not mapping or digest(mapping)!=evidence['mapping_hash']:
                raise UpdateError('identity_changed','Цепочка переходов требует ручного подтверждения неизменной связи игрока')
            if any(digest(self.store.get('clubs',tid))!=expected for tid,expected in evidence['club_hashes'].items()):
                raise UpdateError('identity_changed','Связь исторического клуба изменилась после diff')
            if any(c.status not in {'ready','already_applied'} for c in plan.changes):
                raise UpdateError('needs_review','Непроверенная операция в цепочке переходов')
            return
        if evidence.get('manual'):
            if self.mode!='manual' or not mapping or digest(mapping)!=evidence['mapping_hash']:
                raise UpdateError('source_unverified','Ручная аттестация не разрешает Automatic или изменённую связь игрока')
            return
        anchor=self.store.get('anchors',evidence['anchor_key'])
        if not mapping or not anchor or digest(mapping)!=evidence['mapping_hash'] or digest(anchor)!=evidence['anchor_hash']:
            raise UpdateError('identity_changed','Справочник или подтверждённая база изменились после diff')
        for c in plan.changes:
            if c.status not in {'ready','already_applied'}:
                raise UpdateError('needs_review','Непроверенная операция в серверном плане')
            if c.operation['type']=='update_stats':
                if any(c.operation['new'][k]<c.operation['expected'][k] for k in c.operation['new']):
                    raise UpdateError('needs_review','Снижение статистики требует ручной проверки')


class ServerPublisher(Publisher):
    """The existing publisher runs the same recheck, patch and revision logic."""
    def __init__(self, policy, logger):
        super().__init__(logger)
        self.policy=policy

    def _gate(self, plan): self.policy.gate_plan(plan)
    def _validate_mode(self, mode):
        if mode!=Mode.MANUAL: raise UpdateError('publishing_disabled','Нужен проверенный и утверждённый план')
    def _save(self,client,current,text,summary): return client.save_allowed_page(current,text,summary)
    def _summary(self,types):
        labels={'update_stats':'обновление статистики','update_totals':'пересчёт итогов','update_date':'дата статистики',
                'add_club':'добавление клуба','update_current_club':'текущий клуб','update_career_period':'период карьеры',
                'add_season':'добавление сезона','add_competition':'соревнование','add_national_team':'добавление сборной','restore':'восстановление'}
        return 'BrackelBot: '+', '.join(labels.get(t,t) for t in types)+(' (тест)' if self.policy.mode=='test' else '')
    def _backup(self,snapshot):
        import hashlib
        key=f'{digest(snapshot.title)[:12]}-{snapshot.revid}'
        self.policy.store.put('backups',key,{**asdict(snapshot),'format':'brackelbot-backup-1',
                                           'sha256':hashlib.sha256(snapshot.text.encode('utf-8')).hexdigest()})
        return 'state:'+key
