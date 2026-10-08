# SPDX-License-Identifier: MIT
import hashlib
import json
from difflib import SequenceMatcher
from dataclasses import asdict
from .models import Patch
from .errors import UpdateError


TEST_TITLE = "Участник:Zambrowski/testbot"


def minimal_patches(before, after, field="structure"):
    # Diff only modified lines, then only their changed characters. Unrelated
    # blocks are never reserialized or replaced by an editor operation.
    a, b = before.splitlines(keepends=True), after.splitlines(keepends=True)
    offsets = [0]
    for line in a:
        offsets.append(offsets[-1] + len(line))
    patches = []
    for kind, x, y, u, v in SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if kind == "equal":
            continue
        old, new = "".join(a[x:y]), "".join(b[u:v])
        for tag, i, j, k, l in SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
            if tag != "equal":
                patches.append(Patch(offsets[x]+i, offsets[x]+j, old[i:j], new[k:l], field))
    return patches


def overlap(a, b):
    if a.start == a.end and b.start == b.end:
        return a.start == b.start
    if a.start == a.end:
        return b.start <= a.start <= b.end
    if b.start == b.end:
        return a.start <= b.start <= a.end
    return a.start < b.end and b.start < a.end


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def plan_fingerprint(plan):
    return digest({"snapshot": asdict(plan.snapshot), "article": plan.article,
                   "schema_version": plan.schema_version, "preview": plan.preview,
                   "groups": [asdict(g) for g in plan.groups], "kind": plan.kind,
                   "changes": [{"id": c.id, "operation": c.operation, "status": c.status,
                                "patches": [asdict(p) for p in c.patches], "diff": c.diff,
                                "group_id": c.group_id} for c in plan.changes]})


def approval_fingerprint(plan, change):
    return digest([plan_fingerprint(plan), change.id, change.diff])


def approve_change(plan, change_id):
    change = next((c for c in plan.changes if c.id == change_id), None)
    if change is None or change.status != "ready" or plan_fingerprint(plan) != plan.content_fingerprint:
        raise UpdateError("approval_invalid", "Подтверждение допустимо только для неизменённого проверенного плана")
    change.decision = "approved"
    change.review_fingerprint = approval_fingerprint(plan, change)


def reject_change(plan, change_id):
    change = next(c for c in plan.changes if c.id == change_id)
    change.decision = "rejected"
    change.review_fingerprint = ""


def clear_approvals(plan):
    for c in plan.changes:
        c.decision = "pending"
        c.review_fingerprint = ""
