# SPDX-License-Identifier: MIT
"""Persisted immutable review plans with per-operation approval hashes."""
from dataclasses import asdict
from .models import ArticlePlan, ChangeResult, TransactionGroup, PageSnapshot, Patch
from .transactions import plan_fingerprint, approve_change
from .errors import UpdateError


def serialize_plan(plan): return asdict(plan)


def deserialize_plan(value):
    value=dict(value)
    value['snapshot']=PageSnapshot(**value['snapshot'])
    value['changes']=[ChangeResult(**{**c,'patches':[Patch(**p) for p in c['patches']]}) for c in value['changes']]
    value['groups']=[TransactionGroup(**{**g,'patches':[Patch(**p) for p in g['patches']]}) for g in value['groups']]
    return ArticlePlan(**value)


def queue_plan(store, plan, run_id):
    if plan_fingerprint(plan)!=plan.content_fingerprint:
        raise UpdateError('plan_modified','План не соответствует контрольной сумме')
    key=plan.content_fingerprint
    existing=store.get('plans',key)
    if not existing:
        store.put('plans',key,{'run_id':run_id,'status':'pending','plan':serialize_plan(plan)})
    return key


def approve_stored(store, plan_hash, operation_id):
    item=store.get('plans',plan_hash)
    if not item or item['status']!='pending':
        raise UpdateError('approval_invalid','Нет ожидающего плана с этой контрольной суммой')
    plan=deserialize_plan(item['plan'])
    approve_change(plan,operation_id)
    store.put('plans',plan_hash,{**item,'plan':serialize_plan(plan)})
    return plan.changes
