#!/usr/bin/env python3
"""Install, validate, and test the tracker on a Hermes Agent host."""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
JOB_NAME = "医学文献每日追踪"
MODEL = "deepseek-v4-pro"
PROVIDER = "deepseek"
BASE_URL = "https://api.deepseek.com/v1"
ALLOWED_TOOLSETS = ["terminal", "file"]


def hermes_home() -> Path:
    configured = os.getenv("HERMES_HOME")
    if configured:
        return Path(configured).expanduser().resolve()
    if os.name == "nt" and os.getenv("LOCALAPPDATA"):
        return (Path(os.environ["LOCALAPPDATA"]) / "hermes").resolve()
    return Path.home() / ".hermes"


def hermes_command() -> str:
    candidates = [
        hermes_home() / "hermes-agent" / "venv" / "Scripts" / "hermes.exe",
        hermes_home() / "hermes-agent" / "venv" / "bin" / "hermes",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    executable = shutil.which("hermes")
    if executable:
        return executable
    raise RuntimeError("Hermes CLI was not found on PATH or in HERMES_HOME")


def jobs_path() -> Path:
    return hermes_home() / "cron" / "jobs.json"


def load_jobs() -> dict[str, Any]:
    path = jobs_path()
    if not path.exists():
        return {"jobs": []}
    return json.loads(path.read_text(encoding="utf-8"))


def write_jobs(payload: dict[str, Any]) -> None:
    path = jobs_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp"
    ) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        temp_path = Path(handle.name)
    os.replace(temp_path, path)


def find_job(payload: dict[str, Any], job_id: str | None = None) -> dict[str, Any] | None:
    for job in payload.get("jobs", []):
        if job_id and job.get("id") == job_id:
            return job
        if not job_id and job.get("name") == JOB_NAME and job.get("workdir") == str(ROOT):
            return job
    return None


def discover_feishu_targets() -> list[str]:
    path = hermes_home() / "channel_directory.json"
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    targets = payload.get("platforms", {}).get("feishu", [])
    values = []
    for target in targets:
        target_id = target.get("id") if isinstance(target, dict) else None
        if target_id:
            values.append(f"feishu:{target_id}")
    return values


def discover_feishu_target() -> str | None:
    targets = discover_feishu_targets()
    return targets[0] if targets else None


def install_skill() -> Path:
    target = hermes_home() / "skills" / "research" / "medical-literature-tracker"
    target.mkdir(parents=True, exist_ok=True)
    source_file = ROOT / "hermes" / "SKILL.md"
    target_file = target / "SKILL.md"
    if not target_file.exists() or not os.path.samefile(source_file, target_file):
        shutil.copy2(source_file, target_file)
    return target


def lock_job(job: dict[str, Any], *, deliver: str | None = None) -> None:
    job["name"] = JOB_NAME
    job["prompt"] = (ROOT / "hermes" / "CRON_PROMPT.md").read_text(encoding="utf-8")
    job["workdir"] = str(ROOT)
    job["model"] = MODEL
    job["provider"] = PROVIDER
    job["base_url"] = BASE_URL
    job["enabled_toolsets"] = ALLOWED_TOOLSETS
    job["skills"] = []
    job["skill"] = None
    if deliver:
        job["deliver"] = deliver
    job["medical_literature_tracker"] = {
        "schema": 1,
        "runtime": "hermes-only",
        "model": MODEL,
        "project": str(ROOT),
    }


def create_job(schedule: str, deliver: str) -> str:
    prompt = (ROOT / "hermes" / "CRON_PROMPT.md").read_text(encoding="utf-8")
    result = subprocess.run(
        [
            hermes_command(),
            "cron",
            "create",
            schedule,
            prompt,
            "--name",
            JOB_NAME,
            "--deliver",
            deliver,
            "--workdir",
            str(ROOT),
        ],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    match = re.search(r"Created job:\s*([A-Za-z0-9_-]+)", result.stdout)
    if not match:
        raise RuntimeError(f"Could not read the new Hermes job ID: {result.stdout}")
    return match.group(1)


def install(args: argparse.Namespace) -> int:
    hermes_command()
    home = hermes_home()
    if not home.exists():
        raise RuntimeError(f"HERMES_HOME does not exist: {home}")
    payload = load_jobs()
    job = find_job(payload)
    deliver = args.deliver or (job.get("deliver") if job else None) or discover_feishu_target()
    if not deliver:
        raise RuntimeError("No delivery target found. Pass --deliver platform:chat_id")
    skill = install_skill()
    if job is None:
        job_id = create_job(args.schedule, deliver)
        payload = load_jobs()
        job = find_job(payload, job_id)
        if job is None:
            raise RuntimeError("Hermes created the job but it was not found in jobs.json")
    lock_job(job, deliver=deliver)
    write_jobs(payload)
    print(json.dumps({"status": "installed", "job_id": job["id"], "skill": str(skill)}, ensure_ascii=False))
    return doctor(argparse.Namespace(json=False))


def doctor(args: argparse.Namespace) -> int:
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": ok, "detail": detail})

    add("python", sys.version_info >= (3, 10), platform.python_version())
    try:
        executable = hermes_command()
    except RuntimeError as exc:
        executable = None
        add("hermes_cli", False, str(exc))
    else:
        add("hermes_cli", True, executable)
    home = hermes_home()
    add("hermes_home", home.is_dir(), str(home))
    add("project_writable", os.access(ROOT, os.W_OK), str(ROOT))

    try:
        payload = load_jobs()
        job = find_job(payload)
    except Exception as exc:
        payload, job = {}, None
        add("jobs_json", False, str(exc))
    else:
        add("jobs_json", True, str(jobs_path()))
        add("job_installed", job is not None, job.get("id", "missing") if job else "missing")
        if job:
            add("model_lock", job.get("model") == MODEL, str(job.get("model")))
            add("provider_lock", job.get("provider") == PROVIDER, str(job.get("provider")))
            add("base_url_lock", job.get("base_url") == BASE_URL, str(job.get("base_url")))
            add(
                "tool_isolation",
                job.get("enabled_toolsets") == ALLOWED_TOOLSETS,
                json.dumps(job.get("enabled_toolsets"), ensure_ascii=False),
            )
            add("workdir", Path(job.get("workdir", "")).resolve() == ROOT, job.get("workdir", ""))

    skill = home / "skills" / "research" / "medical-literature-tracker" / "SKILL.md"
    add("skill_installed", skill.is_file(), str(skill))
    add("collector", (ROOT / "hermes" / "collect_for_hermes.py").is_file(), "local deterministic collector")
    add("database", (ROOT / "data" / "medlit_tracker.sqlite3").is_file(), "SQLite persistent history")

    ok = all(check["ok"] for check in checks)
    if args.json:
        print(json.dumps({"ok": ok, "checks": checks}, ensure_ascii=False, indent=2))
    else:
        for check in checks:
            print(f"[{'OK' if check['ok'] else 'FAIL'}] {check['name']}: {check['detail']}")
    return 0 if ok else 1


def test_runtime(args: argparse.Namespace) -> int:
    if doctor(argparse.Namespace(json=False)) != 0:
        return 1
    payload = load_jobs()
    job = find_job(payload)
    assert job is not None
    before = job.get("last_run_at")
    subprocess.run(
        [hermes_command(), "cron", "run", job["id"]],
        check=True,
        timeout=30,
    )
    deadline = time.time() + args.timeout
    while time.time() < deadline:
        time.sleep(5)
        current = find_job(load_jobs(), job["id"])
        if current and current.get("last_run_at") != before:
            result = {
                "last_run_at": current.get("last_run_at"),
                "last_status": current.get("last_status"),
                "last_error": current.get("last_error"),
                "last_delivery_error": current.get("last_delivery_error"),
            }
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if current.get("last_status") == "ok" and not current.get("last_delivery_error") else 1
    print("Timed out waiting for the Hermes cron job", file=sys.stderr)
    return 1


def uninstall(_: argparse.Namespace) -> int:
    payload = load_jobs()
    job = find_job(payload)
    if job:
        subprocess.run([hermes_command(), "cron", "remove", job["id"]], check=False)
    skill = hermes_home() / "skills" / "research" / "medical-literature-tracker"
    if skill.exists():
        if skill.resolve() == (ROOT / "hermes").resolve():
            skill.rmdir()
        else:
            shutil.rmtree(skill)
    print(json.dumps({"status": "uninstalled", "data_preserved": str(ROOT / "data")}, ensure_ascii=False))
    return 0


def target(args: argparse.Namespace) -> int:
    targets = discover_feishu_targets()
    payload = {"status": "ok" if targets else "waiting", "targets": targets}
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.json else None))
    return 0 if targets else 2


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Hermes-only portable installer")
    sub = root.add_subparsers(dest="command", required=True)
    install_parser = sub.add_parser("install")
    install_parser.add_argument("--schedule", default="every 1440m")
    install_parser.add_argument("--deliver")
    doctor_parser = sub.add_parser("doctor")
    doctor_parser.add_argument("--json", action="store_true")
    test_parser = sub.add_parser("test")
    test_parser.add_argument("--timeout", type=int, default=600)
    target_parser = sub.add_parser("target")
    target_parser.add_argument("--json", action="store_true")
    sub.add_parser("uninstall")
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        return {
            "install": install,
            "doctor": doctor,
            "test": test_runtime,
            "target": target,
            "uninstall": uninstall,
        }[args.command](args)
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
