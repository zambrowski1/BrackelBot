# SPDX-License-Identifier: MIT
from dataclasses import dataclass, field
from enum import Enum


class Mode(str, Enum):
    DRY_RUN = "dry_run"
    MANUAL = "manual"
    AUTOMATIC = "automatic"


@dataclass(frozen=True)
class PageSnapshot:
    title: str
    revid: int
    timestamp: str
    text: str
    namespace: int = 0
    starttimestamp: str | None = None


@dataclass(frozen=True)
class Patch:
    start: int
    end: int
    before: str
    after: str
    field: str


@dataclass
class ChangeResult:
    id: str
    operation: dict
    status: str
    message: str
    patches: list[Patch] = field(default_factory=list)
    diff: str = ""
    source_verification: str = "user_provided_not_independently_verified"
    decision: str = "pending"
    group_id: str = ""
    review_fingerprint: str = ""
    error_layer: str = "data"


@dataclass
class ArticlePlan:
    snapshot: PageSnapshot
    changes: list[ChangeResult]
    preview: str
    diff: str
    warnings: list[str]
    publishable: bool = False
    article: dict = field(default_factory=dict)
    schema_version: str = "1.0"
    groups: list = field(default_factory=list)
    content_fingerprint: str = ""
    kind: str = "update"


@dataclass
class TransactionGroup:
    id: str
    change_ids: list[str]
    patches: list[Patch]
    diff: str
    status: str
