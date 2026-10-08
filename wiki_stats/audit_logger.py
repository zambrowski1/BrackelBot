# SPDX-License-Identifier: MIT
import json
import os
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path


def default_audit_path():
    return Path(os.environ.get("LOCALAPPDATA", str(Path.cwd()))) / "WikipediaStatsUpdater" / "audit.jsonl"


class AuditLogger:
    def __init__(self, path=None):
        self.path = Path(path) if path else default_audit_path()

    def log(self, event, **details):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        entry = {"timestamp": datetime.now(timezone.utc).isoformat(), "event": event, **details}
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry, ensure_ascii=False) + "\n")


def make_report(package, plans, failures, mode='dry_run'):
    return {"schema_version": package.get('schema_version','1.0'), "package_id": package["package_id"], "mode": mode,
            "publishing_enabled": mode=='manual', "allowed_publish_title":"Участник:Zambrowski/testbot", "failures": failures,
            "articles": [{"title": p.snapshot.title, "revid": p.snapshot.revid,
                          "revision_timestamp": p.snapshot.timestamp, "warnings": p.warnings,
                          "diff": p.diff, "article_status": 'published' if any(c.status=='published' for c in p.changes) else ('review_required' if any(c.status not in {'ready','already_applied'} for c in p.changes) else 'checked'),
                          "groups":[asdict(g) for g in p.groups],
                          "changes": [asdict(c) for c in p.changes]} for p in plans]}


def audit_report(logger, report):
    for article in report["articles"]:
        for change in article["changes"]:
            logger.log("change_checked", title=article["title"], revid=article["revid"],
                       id=change["id"], status=change["status"], message=change["message"])
    for failure in report["failures"]:
        logger.log("article_failed", **failure)
