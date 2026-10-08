# SPDX-License-Identifier: MIT
from .errors import UpdateError
from .models import ChangeResult, ArticlePlan
from .schema_validator import validate_package
from .wikitext_parser import WikitextParser
from .statistics_mapper import map_statistics
from .change_validator import apply_patches, arithmetic_warnings
from .diff_generator import generate_diff


def _plan_article_v1(snapshot, article):
    results = []
    all_patches = []
    parser = WikitextParser(snapshot.text)
    for op in article["operations"]:
        try:
            if snapshot.title != article["title"]:
                raise UpdateError("title_mismatch", "API вернул другое название страницы")
            if article.get("base_revid", snapshot.revid) != snapshot.revid:
                raise UpdateError("revision_conflict", "Статья изменена после подготовки пакета")
            if op["type"] != "update_stats":
                raise UpdateError("operation_not_implemented", "Этап 1 поддерживает только update_stats; структурные операции отключены")
            if any(s["coverage_as_of"] != op["as_of"] for s in op["sources"]):
                raise UpdateError("source_coverage_mismatch", "Источники заявляют разный охват; требуется ручная проверка")
            numbers = map_statistics(parser, article["player"], op)
            fields = op["expected"]
            if all(numbers[f].value == op["new"][f] for f in fields):
                results.append(ChangeResult(op["id"], op, "already_applied", "Новые значения уже присутствуют"))
                continue
            if any(numbers[f].value != value for f, value in fields.items()):
                raise UpdateError("old_value_mismatch", "Ожидаемые старые значения не совпадают с викитекстом")
            patches = [numbers[f].patch(op["new"][f], f) for f in fields if numbers[f].value != op["new"][f]]
            # No silent deduplication: even equal overlapping requests need review.
            preview = apply_patches(snapshot.text, patches)
            results.append(ChangeResult(op["id"], op, "ready", "Старые значения и структура проверены; статистический источник не проверен",
                                        patches, generate_diff(snapshot.text, preview, snapshot.title)))
        except UpdateError as exc:
            results.append(ChangeResult(op["id"], op, exc.code, str(exc)))
    for i, change in enumerate(results):
        if change.status != "ready":
            continue
        for other in results[i+1:]:
            if other.status != "ready":
                continue
            if any(a.start < b.end and b.start < a.end for a in change.patches for b in other.patches):
                change.status = other.status = "patch_conflict"
                change.message = other.message = "Операции изменяют одну и ту же ячейку"
    all_patches = [p for r in results if r.status == "ready" for p in r.patches]
    preview = apply_patches(snapshot.text, all_patches)
    warnings = arithmetic_warnings(preview)
    warnings.append("Dry Run: публикация отключена. Даты актуальности, итоги и связанные поля автоматически не изменяются.")
    return ArticlePlan(snapshot, results, preview, generate_diff(snapshot.text, preview, snapshot.title), warnings)


def plan_article(snapshot, article, schema_version='1.0', resolver=None):
    from copy import deepcopy
    from collections import OrderedDict
    from .models import TransactionGroup
    from .transactions import minimal_patches, overlap, plan_fingerprint, TEST_TITLE
    from .structural_editor import execute_operation
    if schema_version == '1.0':
        plan = _plan_article_v1(snapshot,article)
        plan.warnings = arithmetic_warnings(plan.preview)
        plan.groups = []
        for c in plan.changes:
            c.group_id = c.id
            if c.status == 'ready':
                plan.groups.append(TransactionGroup(c.id,[c.id],c.patches,c.diff,'ready'))
            else:
                c.error_layer = 'source' if c.status.startswith('source_') else 'data'
        plan.article = deepcopy(article)
        plan.schema_version = '1.0'
        plan.publishable = snapshot.title == TEST_TITLE and bool(plan.groups) and not plan.warnings
        plan.content_fingerprint = plan_fingerprint(plan)
        return plan
    groups = OrderedDict()
    for op in article['operations']:
        groups.setdefault(op.get('group_id') or op['id'],[]).append(op)
    results, transactions = [], []
    for group_id, operations in groups.items():
        staged = snapshot.text
        changes = []
        failure = None
        for op in operations:
            before = staged
            try:
                if snapshot.title != article['title']:
                    raise UpdateError('title_mismatch','API вернул другое название страницы')
                if article.get('base_revid',snapshot.revid)!=snapshot.revid:
                    raise UpdateError('revision_conflict','Статья изменилась после подготовки пакета')
                if any(s['coverage_as_of']!=op['as_of'] for s in op['sources']):
                    raise UpdateError('source_coverage_mismatch','Источники заявляют несовместимый охват')
                if len({x['as_of'] for x in operations})!=1:
                    raise UpdateError('source_coverage_mismatch','Связанные операции должны иметь одну дату охвата')
                staged,status = execute_operation(staged,article['player'],op,resolver)
                c = ChangeResult(op['id'],deepcopy(op),status,
                                 'Структура и данные проверены; источники статистики предоставлены пользователем',
                                 minimal_patches(before,staged),generate_diff(before,staged,snapshot.title),group_id=group_id)
                changes.append(c)
            except UpdateError as exc:
                failure = exc
                changes.append(ChangeResult(op['id'],deepcopy(op),exc.code,str(exc),group_id=group_id,
                                            error_layer='source' if exc.code.startswith('source_') else 'data'))
        if failure:
            for c in changes:
                if c.status in {'ready','already_applied'}:
                    c.status = 'group_blocked'
                    c.message = f'Вся группа {group_id} отклонена: {failure.code}'
                c.patches = []
                # Keep a supplied structural proposal visible, never executable.
                if c.status=='structure_change_required':
                    c.diff = c.operation.get('payload',{}).get('structure_proposal','Предложение: добавить категорию '+c.operation.get('competition',{}).get('category',''))
                else:
                    c.diff = ''
            transactions.append(TransactionGroup(group_id,[c.id for c in changes],[],'','blocked'))
        else:
            patches = minimal_patches(snapshot.text,staged)
            diff = generate_diff(snapshot.text,staged,snapshot.title)
            transaction = TransactionGroup(group_id,[c.id for c in changes],patches,diff,'ready' if patches else 'already_applied')
            transactions.append(transaction)
            # Each member shows both its contribution and the complete atomic diff.
            for c in changes:
                if len(changes)>1 and c.status=='ready':
                    c.diff += '\nСвязанная группа (публикуется целиком):\n'+diff
        results.extend(changes)
    conflicts = set()
    for i,g in enumerate(transactions):
        if g.status!='ready': continue
        for h in transactions[i+1:]:
            if h.status=='ready' and any(overlap(a,b) for a in g.patches for b in h.patches):
                conflicts.update([g.id,h.id])
    for g in transactions:
        if g.id in conflicts:
            g.status='blocked'
            for c in results:
                if c.group_id==g.id:
                    c.status='patch_conflict'; c.message='Группы пересекаются. Свяжите эти операции одним group_id.'; c.patches=[];c.diff=''
    ready_patches=[p for g in transactions if g.status=='ready' for p in g.patches]
    preview=apply_patches(snapshot.text,ready_patches,structural=True)
    warnings=arithmetic_warnings(preview)
    plan=ArticlePlan(snapshot,results,preview,generate_diff(snapshot.text,preview,snapshot.title),warnings,
                     snapshot.title==TEST_TITLE and any(g.status=='ready' for g in transactions) and not warnings,
                     deepcopy(article),schema_version,transactions)
    plan.content_fingerprint=plan_fingerprint(plan)
    return plan


def plan_package(package, client, on_progress=None):
    validate_package(package)  # No API request before full validation.
    plans, failures = [], []
    for index, article in enumerate(package["articles"]):
        if on_progress:
            on_progress(f"Проверка {index+1}/{len(package['articles'])}: {article['title']}")
        try:
            from .entity_resolver import EntityResolver
            resolver = EntityResolver(client) if package['schema_version']=='1.1' else None
            plans.append(plan_article(client.fetch_page(article["title"]), article,package['schema_version'],resolver))
        except UpdateError as exc:
            failures.append({"title": article["title"], "status": exc.code, "message": str(exc)})
            if exc.code in {"authorization_error", "rate_limited"}:
                break
    return plans, failures
