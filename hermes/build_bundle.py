#!/usr/bin/env python3
"""Build a portable Hermes-only source bundle without runtime data or secrets."""

from __future__ import annotations

import argparse
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VERSION = "0.3.0"
EXCLUDED_PARTS = {
    ".git",
    ".pytest_cache",
    "__pycache__",
    "data",
    "dist",
    "logs",
    "raw",
    "reports",
    "payload",
    "venv",
    ".venv",
}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".sqlite", ".sqlite3", ".db"}
EXCLUDED_NAMES = {".env", ".env.local"}


def included_files() -> list[Path]:
    files: list[Path] = []
    for path in ROOT.rglob("*"):
        relative = path.relative_to(ROOT)
        if not path.is_file():
            continue
        if any(part in EXCLUDED_PARTS for part in relative.parts):
            continue
        if path.name in EXCLUDED_NAMES or path.suffix.lower() in EXCLUDED_SUFFIXES:
            continue
        files.append(path)
    return sorted(files, key=lambda item: item.as_posix())


def build(version: str, output: Path | None = None) -> Path:
    destination = output or ROOT / "dist" / f"medical-literature-tracker-hermes-{version}.zip"
    destination.parent.mkdir(parents=True, exist_ok=True)
    prefix = f"medical-literature-tracker-hermes-{version}"
    files = included_files()
    manifest = {
        "name": "medical-literature-tracker-hermes",
        "version": version,
        "runtime": "hermes-only",
        "provider": "deepseek",
        "model": "deepseek-v4-pro",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "files": [path.relative_to(ROOT).as_posix() for path in files],
    }
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in files:
            archive.write(path, f"{prefix}/{path.relative_to(ROOT).as_posix()}")
        archive.writestr(
            f"{prefix}/BUNDLE_MANIFEST.json",
            json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
        )
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default=DEFAULT_VERSION)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    print(build(args.version, args.output).resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
