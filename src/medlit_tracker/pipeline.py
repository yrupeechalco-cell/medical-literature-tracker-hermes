from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .collectors import COLLECTORS
from .db import Database


def make_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")


def load_topic(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def run_collection(
    *, db: Database, topic: dict[str, Any], raw_root: Path, run_id: str | None = None
) -> dict[str, Any]:
    run_id = run_id or make_run_id()
    db.start_run(run_id)
    stats: dict[str, Any] = {"run_id": run_id, "sources": {}, "new": 0, "updated": 0, "existing": 0}
    errors: list[str] = []

    try:
        for source, collector in COLLECTORS.items():
            if not topic.get("sources", {}).get(source, False):
                continue
            try:
                source_topic = dict(topic)
                cursor = db.get_source_cursor(source)
                if cursor:
                    overlap = int(topic.get("poll_overlap_days", 1))
                    since = datetime.fromisoformat(cursor).date() - timedelta(days=overlap)
                    source_topic["_since_date"] = since.isoformat()
                records, source_errors = collector(source_topic, raw_root, run_id)
                source_stats = {"collected": len(records), "new": 0, "updated": 0, "existing": 0}
                for record in records:
                    raw_path = record.pop("raw_path", None)
                    outcome = db.upsert_record(record, raw_path=raw_path)
                    source_stats[outcome] += 1
                    stats[outcome] += 1
                stats["sources"][source] = source_stats
                stats["sources"][source]["queried_since"] = source_topic.get(
                    "_since_date", f"initial-{topic.get('lookback_days', 14)}d"
                )
                db.set_source_cursor(source, datetime.now(timezone.utc).isoformat())
                errors.extend(f"{source}: {error}" for error in source_errors)
            except Exception as exc:
                errors.append(f"{source}: {type(exc).__name__}: {exc}")
                stats["sources"][source] = {"collected": 0, "error": str(exc)}

        status = "ok" if not errors else "partial"
        db.finish_run(run_id, status, stats, errors)
        return {"status": status, "stats": stats, "errors": errors}
    except Exception as exc:
        errors.append(f"pipeline: {type(exc).__name__}: {exc}")
        db.finish_run(run_id, "error", stats, errors)
        raise
