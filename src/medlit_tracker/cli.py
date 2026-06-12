from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .db import Database
from .pipeline import load_topic, run_collection
from .report import compact_record, render_batch
from .maintenance import run_file_maintenance


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TOPIC = ROOT / "config" / "topic_glp1_obesity.json"
DEFAULT_DB = ROOT / "data" / "medlit_tracker.sqlite3"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Medical literature surveillance")
    parser.add_argument("--topic", type=Path, default=DEFAULT_TOPIC)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("collect")
    subparsers.add_parser("run")
    subparsers.add_parser("report")
    subparsers.add_parser("status")
    subparsers.add_parser("maintain")
    pending = subparsers.add_parser("pending")
    pending.add_argument("--json", action="store_true")
    pending.add_argument("--limit", type=int, default=20)
    delivered = subparsers.add_parser("mark-delivered")
    delivered.add_argument("--batch-id", required=True)
    requeue = subparsers.add_parser("requeue")
    requeue.add_argument("--batch-id", required=True)
    return parser


def _pending_payload(db: Database, topic: dict, limit: int = 20) -> dict:
    batch = db.create_pending_batch(limit=limit)
    if not batch:
        return {"status": "empty", "topic": topic["name_zh"], "records": []}
    paths = render_batch(batch, topic, ROOT / "reports")
    return {
        "status": "ok",
        "batch_id": batch["batch_id"],
        "topic": topic["name_zh"],
        "topic_description": topic["description_zh"],
        "records": [compact_record(record) for record in batch["records"]],
        "report_paths": paths,
        "mark_delivered_command": f'python hermes/mark_delivered.py {batch["batch_id"]}',
    }


def main() -> None:
    args = build_parser().parse_args()
    topic = load_topic(args.topic)
    storage = topic.get("storage", {})
    db = Database(args.db, cache_kib=int(storage.get("sqlite_cache_kib", 2048)))

    if args.command == "collect":
        print(json.dumps(run_collection(db=db, topic=topic, raw_root=ROOT / "raw"), ensure_ascii=False, indent=2))
    elif args.command == "run":
        collection = run_collection(db=db, topic=topic, raw_root=ROOT / "raw")
        maintenance = {
            "files": run_file_maintenance(ROOT, storage),
            "database": db.maintain(
                run_history_days=int(storage.get("run_history_days", 90)),
                delivery_detail_days=int(storage.get("delivery_detail_days", 30)),
            ),
        }
        payload = _pending_payload(db, topic)
        payload["collection"] = collection
        payload["maintenance"] = maintenance
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif args.command in {"report", "pending"}:
        payload = _pending_payload(db, topic, getattr(args, "limit", 30))
        if getattr(args, "json", False) or args.command == "pending":
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(payload.get("report_paths", {}).get("markdown", "No pending records"))
    elif args.command == "status":
        print(json.dumps(db.status(), ensure_ascii=False, indent=2))
    elif args.command == "maintain":
        print(
            json.dumps(
                {
                    "files": run_file_maintenance(ROOT, storage),
                    "database": db.maintain(
                        run_history_days=int(storage.get("run_history_days", 90)),
                        delivery_detail_days=int(storage.get("delivery_detail_days", 30)),
                    ),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    elif args.command == "mark-delivered":
        db.mark_delivered(args.batch_id)
        print(json.dumps({"status": "delivered", "batch_id": args.batch_id}, ensure_ascii=False))
    elif args.command == "requeue":
        db.requeue_batch(args.batch_id)
        print(json.dumps({"status": "pending", "batch_id": args.batch_id}, ensure_ascii=False))


if __name__ == "__main__":
    main()
