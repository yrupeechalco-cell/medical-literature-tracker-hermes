from __future__ import annotations

import json
import gzip
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


USER_AGENT = "MedLitTracker/0.1 (research literature monitoring; contact: local-user)"


def request_bytes(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    timeout: int = 45,
    attempts: int = 3,
) -> bytes:
    if params:
        query = urlencode({key: value for key, value in params.items() if value is not None})
        url = f"{url}{'&' if '?' in url else '?'}{query}"

    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            request = Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json, application/xml, text/xml, */*",
                },
            )
            with urlopen(request, timeout=timeout) as response:
                return response.read()
        except (HTTPError, URLError, TimeoutError) as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"Request failed after {attempts} attempts: {url}: {last_error}")


def request_json(url: str, *, params: dict[str, Any] | None = None) -> Any:
    return json.loads(request_bytes(url, params=params).decode("utf-8"))


def archive_raw(raw_root: Path, source: str, run_id: str, name: str, payload: bytes) -> Path:
    target_dir = raw_root / run_id[:10] / source
    target_dir.mkdir(parents=True, exist_ok=True)
    if len(payload) >= 10_000:
        path = target_dir / f"{name}.gz"
        path.write_bytes(gzip.compress(payload, compresslevel=6))
    else:
        path = target_dir / name
        path.write_bytes(payload)
    return path
