#!/usr/bin/env python3
"""Build a Windows kit with pinned Hermes, CC Switch, and uv artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
import time
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import build_bundle


ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "dist" / ".bundle-cache"
VERSION = "0.3.0"
HERMES_TAG = "v2026.6.5"
CC_SWITCH_TAG = "v3.16.2"
UV_VERSION = "0.11.21"
ASSETS = {
    f"hermes-agent-{HERMES_TAG}.zip": f"https://github.com/NousResearch/hermes-agent/archive/refs/tags/{HERMES_TAG}.zip",
    f"CC-Switch-{CC_SWITCH_TAG}-Windows-Portable.zip": (
        f"https://github.com/farion1231/cc-switch/releases/download/{CC_SWITCH_TAG}/"
        f"CC-Switch-{CC_SWITCH_TAG}-Windows-Portable.zip"
    ),
    f"uv-{UV_VERSION}-x86_64-pc-windows-msvc.zip": (
        f"https://github.com/astral-sh/uv/releases/download/{UV_VERSION}/"
        f"uv-x86_64-pc-windows-msvc.zip"
    ),
    f"uv-{UV_VERSION}-aarch64-pc-windows-msvc.zip": (
        f"https://github.com/astral-sh/uv/releases/download/{UV_VERSION}/"
        f"uv-aarch64-pc-windows-msvc.zip"
    ),
}
LICENSES = {
    "hermes-agent-LICENSE.txt": f"https://raw.githubusercontent.com/NousResearch/hermes-agent/{HERMES_TAG}/LICENSE",
    "cc-switch-LICENSE.txt": f"https://raw.githubusercontent.com/farion1231/cc-switch/{CC_SWITCH_TAG}/LICENSE",
    "uv-LICENSE-MIT.txt": f"https://raw.githubusercontent.com/astral-sh/uv/{UV_VERSION}/LICENSE-MIT",
    "uv-LICENSE-APACHE.txt": f"https://raw.githubusercontent.com/astral-sh/uv/{UV_VERSION}/LICENSE-APACHE",
}


def download(url: str, destination: Path, *, attempts: int = 4) -> None:
    cached = CACHE / destination.name
    if cached.is_file() and cached.stat().st_size:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(cached, destination)
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    CACHE.mkdir(parents=True, exist_ok=True)
    error: Exception | None = None
    for attempt in range(1, attempts + 1):
        partial = destination.with_suffix(destination.suffix + ".part")
        partial.unlink(missing_ok=True)
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": "medical-literature-tracker-builder"}
            )
            with urllib.request.urlopen(request, timeout=180) as response, partial.open("wb") as output:
                shutil.copyfileobj(response, output)
            partial.replace(destination)
            shutil.copy2(destination, cached)
            return
        except Exception as exc:
            error = exc
            partial.unlink(missing_ok=True)
            if attempt < attempts:
                time.sleep(2 ** (attempt - 1))
    assert error is not None
    raise error


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build(output: Path | None = None) -> Path:
    destination = output or ROOT / "dist" / f"medical-literature-tracker-full-windows-{VERSION}.zip"
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temp:
        staging = Path(temp) / "staging"
        staging.mkdir()
        source_zip = build_bundle.build(VERSION, Path(temp) / "source.zip")
        with zipfile.ZipFile(source_zip) as archive:
            archive.extractall(staging)
        bundle_root = next(staging.iterdir())
        payload = bundle_root / "payload"
        asset_rows = []
        for name, url in ASSETS.items():
            path = payload / name
            download(url, path)
            asset_rows.append(
                {"name": name, "url": url, "bytes": path.stat().st_size, "sha256": sha256(path)}
            )
        for name, url in LICENSES.items():
            download(url, payload / "licenses" / name)
        manifest = {
            "schema": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "tracker_version": VERSION,
            "hermes_tag": HERMES_TAG,
            "cc_switch_tag": CC_SWITCH_TAG,
            "uv_version": UV_VERSION,
            "assets": asset_rows,
        }
        (payload / "PAYLOAD_MANIFEST.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for path in sorted(bundle_root.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(staging).as_posix())
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    print(build(args.output).resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
