# SPDX-License-Identifier: MIT
from .errors import UpdateError
from .models import Mode
from .transactions import plan_fingerprint, approval_fingerprint, clear_approvals
from .change_validator import apply_patches, arithmetic_warnings
from .change_planner import plan_article
from .schema_validator import validate_package
from .backup import save_backup


class Publisher:
    def __init__(self, logger=None, backup_dir=None):
        self.logger = logger
        self.backup_dir = backup_dir

    def _validate_mode(self, mode):
        if mode != Mode.MANUAL:
            raise UpdateError('publishing_disabled','Публикация доступна только в Manual; Dry Run и Automatic не публикуют')

    def _gate(self, plan):
        if plan.snapshot.title != 'Участник:Zambrowski/testbot':
            raise UpdateError('page_not_allowed','Публикация в основном пространстве запрещена. Разрешена только Участник:Zambrowski/testbot')

    def _backup(self, snapshot):
        return str(save_backup(snapshot,self.backup_dir))

    def _save(self, client, current, text, summary):
        return client.save_test_page(current,text,summary)

    def _summary(self, types):
        return 'BrackelBot: '+', '.join(types)+' (тест)'

    def publish(self, plan=None, client=None, *, mode=Mode.DRY_RUN, resolver=None):
        self._validate_mode(mode)
        if plan is None or client is None:
            raise UpdateError('publishing_disabled','Нет проверенного плана публикации')
        # Literal non-configurable gate, independent of GUI/JSON/configuration.
        self._gate(plan)
        if plan_fingerprint(plan) != plan.content_fingerprint:
            clear_approvals(plan)
            raise UpdateError('plan_modified','План изменился; выполните новую проверку и подтвердите diff')
        if not plan.publishable:
            raise UpdateError('plan_not_publishable','План не готов к публикации: проверьте данные и итоги заново')
        selected=[]
        for group in plan.groups:
            members=[c for c in plan.changes if c.id in group.change_ids]
            changed=[c for c in members if c.status=='ready']
            if not any(c.decision=='approved' for c in members):
                continue
            if group.status!='ready' or any(c.status not in {'ready','already_applied'} for c in members):
                raise UpdateError('group_blocked','В связанной группе есть ошибка проверки')
            if any(c.decision!='approved' or c.review_fingerprint!=approval_fingerprint(plan,c) for c in changed):
                raise UpdateError('approval_required','Подтвердите каждую изменяемую операцию связанной группы; частичная публикация запрещена')
            if changed:
                selected.append(group)
        if not selected:
            raise UpdateError('approval_required','Нет индивидуально подтверждённых изменений')
        client.verify_authenticated()
        current=client.fetch_page(plan.snapshot.title)
        if current.title!=plan.snapshot.title or current.namespace!=plan.snapshot.namespace:
            raise UpdateError('page_not_allowed','API вернул страницу вне жёсткого белого списка')
        if current.revid!=plan.snapshot.revid or current.text!=plan.snapshot.text:
            clear_approvals(plan)
            plan.publishable=False
            raise UpdateError('revision_conflict','Страница изменилась. Загрузите текущую версию и подтвердите новый diff.')
        if plan.kind=='restore':
            if len(selected)!=1:
                raise UpdateError('approval_invalid','Восстановление должно быть отдельной операцией')
            text=plan.preview
        else:
            ids={i for g in selected for i in g.change_ids}
            article={**plan.article,'operations':[op for op in plan.article['operations'] if op['id'] in ids]}
            date=max(op['as_of'] for op in article['operations'])
            validate_package({'schema_version':plan.schema_version,'package_id':'publication-recheck',
                              'generated_at':date+'T23:59:59Z','articles':[article]})
            if resolver is None and plan.schema_version=='1.1':
                from .entity_resolver import EntityResolver
                resolver=EntityResolver(client)
            rechecked=plan_article(current,article,plan.schema_version,resolver)
            actual={g.id:g for g in rechecked.groups if g.status=='ready'}
            for group in selected:
                if group.id not in actual or actual[group.id].patches!=group.patches or actual[group.id].diff!=group.diff:
                    clear_approvals(plan)
                    raise UpdateError('recheck_failed','Повторная проверка изменила план; требуется новый diff и подтверждение')
            text=apply_patches(current.text,[p for g in selected for p in g.patches],structural=True)
            if arithmetic_warnings(text):
                raise UpdateError('totals_mismatch','Публикация заблокирована: арифметика или структура таблицы не согласованы')
        if text==current.text:
            raise UpdateError('already_applied','Новых изменений нет')
        self._gate(plan)  # Fresh kill switch immediately before any write.
        backup_path=self._backup(current)
        types=sorted({c.operation['type'] for c in plan.changes if any(c.id in g.change_ids for g in selected)})
        summary=self._summary(types)
        if self.logger:
            self.logger.log('publication_attempt',title=current.title,baserevid=current.revid,
                            operation_ids=sorted(i for g in selected for i in g.change_ids),backup=str(backup_path))
        try:
            self._gate(plan)
            result=self._save(client,current,text,summary)
        except UpdateError as exc:
            clear_approvals(plan)
            plan.publishable=False
            if self.logger:
                self.logger.log('publication_failed',title=current.title,status=exc.code)
            raise
        result['backup']=str(backup_path)
        for c in plan.changes:
            if any(c.id in g.change_ids for g in selected) and c.status=='ready':
                c.status='published';c.message='Википедия подтвердила сохранение';c.review_fingerprint=''
        plan.publishable=False
        if self.logger:
            try:
                self.logger.log('publication_succeeded',**result)
            except OSError:
                result['audit_warning']='Правка сохранена, но запись результата в журнал не удалась; не повторяйте публикацию'
        return result
