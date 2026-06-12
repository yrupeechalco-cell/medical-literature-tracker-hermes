#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python hermes/mark_delivered.py <batch-id>", file=sys.stderr)
        return 2
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    command = [
        sys.executable,
        "-m",
        "medlit_tracker",
        "--topic",
        str(ROOT / "config" / "topic_glp1_obesity.json"),
        "--db",
        str(ROOT / "data" / "medlit_tracker.sqlite3"),
        "mark-delivered",
        "--batch-id",
        sys.argv[1],
    ]
    return subprocess.run(command, cwd=ROOT, env=env).returncode


if __name__ == "__main__":
    raise SystemExit(main())
