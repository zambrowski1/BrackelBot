# SPDX-License-Identifier: MIT
import argparse
import json
import sys
from pathlib import Path
from .audit_logger import AuditLogger, make_report, audit_report
from .change_planner import plan_package
from .errors import UpdateError
from .json_loader import load_package
from .wiki_client import WikiClient, FixtureClient


def main(argv=None):
    parser = argparse.ArgumentParser(description="Wikipedia Stats Updater — только Dry Run")
    parser.add_argument("package", help="Файл JSON")
    parser.add_argument("--fixtures", help="Каталог локальных копий (вместо сети)")
    parser.add_argument("--report", default="report.json")
    parser.add_argument("--audit", help="Путь к журналу JSONL")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        package = load_package(args.package)
        logger = AuditLogger(args.audit)
        logger.log("package_loaded", package_id=package["package_id"], offline=bool(args.fixtures))
        client = FixtureClient(args.fixtures) if args.fixtures else WikiClient()
        plans, failures = plan_package(package, client, print)
        report = make_report(package, plans, failures)
        report["offline"] = bool(args.fixtures)
        audit_report(logger, report)
        Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        for p in plans:
            for c in p.changes:
                print(f"{p.snapshot.title}: {c.id}: {c.status}: {c.message}")
            print(p.diff)
        print(f"Отчёт: {args.report}. Публикация отключена.")
        return 1 if failures or any(c.status not in {"ready", "already_applied"} for p in plans for c in p.changes) else 0
    except (UpdateError, OSError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
