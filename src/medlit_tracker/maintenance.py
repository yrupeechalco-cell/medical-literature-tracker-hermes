from __future__ import annotations

import shutil
from datetime import date, timedelta
from pathlib import Path


def prune_dated_directories(root: Path, retention_days: int) -> dict[str, int]:
    cutoff = date.today() - timedelta(days=max(1, int(retention_days)))
    removed_dirs = 0
    removed_bytes = 0
    if not root.exists():
        return {"directories": 0, "bytes": 0}
    for child in root.iterdir():
        if not child.is_dir():
            continue
        try:
            child_date = date.fromisoformat(child.name)
        except ValueError:
            continue
        if child_date >= cutoff:
            continue
        removed_bytes += sum(path.stat().st_size for path in child.rglob("*") if path.is_file())
        shutil.rmtree(child)
        removed_dirs += 1
    return {"directories": removed_dirs, "bytes": removed_bytes}


def run_file_maintenance(root: Path, storage: dict) -> dict:
    return {
        "raw": prune_dated_directories(
            root / "raw", int(storage.get("raw_retention_days", 7))
        ),
        "reports": prune_dated_directories(
            root / "reports", int(storage.get("report_retention_days", 90))
        ),
    }
