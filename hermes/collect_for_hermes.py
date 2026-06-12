#!/usr/bin/env python3
"""Hermes cron pre-run script. All persistent files remain on F:."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV = os.environ.copy()
ENV["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + ENV.get("PYTHONPATH", "")


def main() -> int:
    command = [
        sys.executable,
        "-m",
        "medlit_tracker",
        "--topic",
        str(ROOT / "config" / "topic_glp1_obesity.json"),
        "--db",
        str(ROOT / "data" / "medlit_tracker.sqlite3"),
        "run",
    ]
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=ENV,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=600,
    )
    if completed.stderr:
        print(completed.stderr, file=sys.stderr)
    print(completed.stdout)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())

