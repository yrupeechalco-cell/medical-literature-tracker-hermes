from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS records (
    canonical_id TEXT PRIMARY KEY,
    primary_source TEXT NOT NULL,
    record_type TEXT NOT NULL,
    title TEXT NOT NULL,
    abstract TEXT NOT NULL DEFAULT '',
    authors_json TEXT NOT NULL DEFAULT '[]',
    journal TEXT NOT NULL DEFAULT '',
    publication_date TEXT,
    updated_date TEXT,
    study_type TEXT NOT NULL DEFAULT '',
    publication_types_json TEXT NOT NULL DEFAULT '[]',
    mesh_terms_json TEXT NOT NULL DEFAULT '[]',
    peer_reviewed INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',
    url TEXT NOT NULL,
    score INTEGER NOT NULL DEFAULT 0,
    match_reasons_json TEXT NOT NULL DEFAULT '[]',
    payload_hash TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    last_changed_at TEXT
);

CREATE TABLE IF NOT EXISTS identifiers (
    scheme TEXT NOT NULL,
    value TEXT NOT NULL,
    canonical_id TEXT NOT NULL REFERENCES records(canonical_id) ON DELETE CASCADE,
    PRIMARY KEY (scheme, value)
);

CREATE TABLE IF NOT EXISTS record_sources (
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source_version TEXT NOT NULL DEFAULT '',
    canonical_id TEXT NOT NULL REFERENCES records(canonical_id) ON DELETE CASCADE,
    raw_path TEXT,
    payload_hash TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    PRIMARY KEY (source, source_id, source_version)
);

CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    stats_json TEXT NOT NULL DEFAULT '{}',
    errors_json TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS delivery_batches (
    batch_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    delivered_at TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    run_id TEXT
);

CREATE TABLE IF NOT EXISTS delivery_items (
    batch_id TEXT NOT NULL REFERENCES delivery_batches(batch_id) ON DELETE CASCADE,
    canonical_id TEXT NOT NULL REFERENCES records(canonical_id) ON DELETE CASCADE,
    record_version INTEGER NOT NULL,
    event_key TEXT NOT NULL DEFAULT 'initial',
    PRIMARY KEY (batch_id, canonical_id, record_version)
);

CREATE TABLE IF NOT EXISTS delivered_versions (
    canonical_id TEXT NOT NULL,
    record_version INTEGER NOT NULL,
    delivered_at TEXT NOT NULL,
    PRIMARY KEY (canonical_id, record_version)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS delivered_events (
    canonical_id TEXT NOT NULL,
    event_key TEXT NOT NULL,
    delivered_at TEXT NOT NULL,
    PRIMARY KEY (canonical_id, event_key)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS source_cursors (
    source TEXT PRIMARY KEY,
    last_success_at TEXT NOT NULL
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_records_score ON records(score DESC);
CREATE INDEX IF NOT EXISTS idx_records_seen ON records(first_seen_at DESC);
CREATE INDEX IF NOT EXISTS idx_delivery_status ON delivery_batches(status, created_at);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class Database:
    def __init__(self, path: Path, cache_kib: int = 2048):
        self.path = path
        self.cache_kib = max(256, int(cache_kib))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            record_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(records)")
            }
            if "last_changed_at" not in record_columns:
                connection.execute("ALTER TABLE records ADD COLUMN last_changed_at TEXT")
            connection.execute(
                "UPDATE records SET last_changed_at=first_seen_at WHERE last_changed_at IS NULL"
            )
            batch_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(delivery_batches)")
            }
            if "run_id" not in batch_columns:
                connection.execute("ALTER TABLE delivery_batches ADD COLUMN run_id TEXT")
            item_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(delivery_items)")
            }
            if "event_key" not in item_columns:
                connection.execute(
                    "ALTER TABLE delivery_items ADD COLUMN event_key TEXT NOT NULL DEFAULT 'initial'"
                )
            connection.execute(
                """
                INSERT OR IGNORE INTO delivered_versions(canonical_id,record_version,delivered_at)
                SELECT di.canonical_id,di.record_version,COALESCE(db.delivered_at,db.created_at)
                FROM delivery_items di
                JOIN delivery_batches db ON db.batch_id=di.batch_id
                WHERE db.status='delivered'
                """
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO delivered_events(canonical_id,event_key,delivered_at)
                SELECT canonical_id,'initial',MIN(delivered_at)
                FROM delivered_versions
                GROUP BY canonical_id
                """
            )

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA cache_size=-{self.cache_kib}")
        connection.execute("PRAGMA temp_store=FILE")
        connection.execute("PRAGMA mmap_size=0")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def start_run(self, run_id: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO runs(run_id, started_at, status) VALUES (?, ?, 'running')",
                (run_id, utc_now()),
            )

    def finish_run(self, run_id: str, status: str, stats: dict, errors: list[str]) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE runs SET finished_at=?, status=?, stats_json=?, errors_json=? WHERE run_id=?",
                (utc_now(), status, json.dumps(stats), json.dumps(errors), run_id),
            )

    def _find_existing_ids(
        self, connection: sqlite3.Connection, identifiers: Iterable[tuple[str, str]]
    ) -> list[str]:
        found: list[str] = []
        for scheme, value in identifiers:
            row = connection.execute(
                "SELECT canonical_id FROM identifiers WHERE scheme=? AND value=?",
                (scheme, value),
            ).fetchone()
            if row and row[0] not in found:
                found.append(row[0])
        return found

    def _new_canonical_id(self, identifiers: list[tuple[str, str]]) -> str:
        seed = "|".join(f"{scheme}:{value}" for scheme, value in identifiers)
        return "rec_" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:20]

    def upsert_record(self, record: dict[str, Any], raw_path: str | None = None) -> str:
        identifiers = [(k.lower(), str(v).strip().lower()) for k, v in record["identifiers"] if v]
        if not identifiers:
            raise ValueError("A record must have at least one identifier")

        now = utc_now()
        payload_hash = stable_hash(record)
        with self.connect() as connection:
            existing_ids = self._find_existing_ids(connection, identifiers)
            canonical_id = existing_ids[0] if existing_ids else self._new_canonical_id(identifiers)

            existing = connection.execute(
                """
                SELECT primary_source,payload_hash,version,first_seen_at,last_changed_at
                FROM records WHERE canonical_id=?
                """,
                (canonical_id,),
            ).fetchone()
            source_rank = {"medrxiv": 10, "europe_pmc": 20, "clinical_trials": 30, "pubmed": 40}
            should_replace = not existing or source_rank.get(record["source"], 0) >= source_rank.get(existing["primary_source"], 0)
            version = 1 if not existing else int(existing["version"])
            changed = bool(existing and should_replace and existing["payload_hash"] != payload_hash)
            if changed:
                version += 1

            values = (
                canonical_id,
                record["source"],
                record["record_type"],
                record["title"],
                record.get("abstract", ""),
                json.dumps(record.get("authors", []), ensure_ascii=False),
                record.get("journal", ""),
                record.get("publication_date"),
                record.get("updated_date"),
                record.get("study_type", ""),
                json.dumps(record.get("publication_types", []), ensure_ascii=False),
                json.dumps(record.get("mesh_terms", []), ensure_ascii=False),
                int(bool(record.get("peer_reviewed"))),
                record.get("status", "active"),
                record["url"],
                int(record.get("score", 0)),
                json.dumps(record.get("match_reasons", []), ensure_ascii=False),
                payload_hash,
                version,
                existing["first_seen_at"] if existing else now,
                now,
                now if not existing or changed else existing["last_changed_at"],
            )
            if should_replace:
                connection.execute(
                    """
                INSERT INTO records VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(canonical_id) DO UPDATE SET
                    primary_source=excluded.primary_source,
                    record_type=excluded.record_type, title=excluded.title,
                    abstract=excluded.abstract, authors_json=excluded.authors_json,
                    journal=excluded.journal, publication_date=excluded.publication_date,
                    updated_date=excluded.updated_date, study_type=excluded.study_type,
                    publication_types_json=excluded.publication_types_json,
                    mesh_terms_json=excluded.mesh_terms_json,
                    peer_reviewed=excluded.peer_reviewed, status=excluded.status,
                    url=excluded.url, score=excluded.score,
                    match_reasons_json=excluded.match_reasons_json,
                    payload_hash=excluded.payload_hash, version=excluded.version,
                    last_seen_at=excluded.last_seen_at,
                    last_changed_at=excluded.last_changed_at
                """,
                    values,
                )
            else:
                connection.execute(
                    "UPDATE records SET last_seen_at=? WHERE canonical_id=?",
                    (now, canonical_id),
                )

            for scheme, value in identifiers:
                connection.execute(
                    "INSERT OR IGNORE INTO identifiers(scheme,value,canonical_id) VALUES (?,?,?)",
                    (scheme, value, canonical_id),
                )

            connection.execute(
                """
                INSERT INTO record_sources(
                    source,source_id,source_version,canonical_id,raw_path,payload_hash,first_seen_at,last_seen_at
                ) VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(source,source_id,source_version) DO UPDATE SET
                    canonical_id=excluded.canonical_id, raw_path=excluded.raw_path,
                    payload_hash=excluded.payload_hash, last_seen_at=excluded.last_seen_at
                """,
                (
                    record["source"],
                    record["source_id"],
                    str(record.get("source_version", "")),
                    canonical_id,
                    raw_path,
                    payload_hash,
                    now,
                    now,
                ),
            )
        return "updated" if changed else ("existing" if existing else "new")

    def create_pending_batch(self, limit: int = 30) -> dict[str, Any] | None:
        with self.connect() as connection:
            existing = connection.execute(
                "SELECT batch_id FROM delivery_batches WHERE status='pending' ORDER BY created_at LIMIT 1"
            ).fetchone()
            if existing:
                return self.get_batch(existing["batch_id"], connection=connection)

            latest_run = connection.execute(
                """
                SELECT run_id,started_at FROM runs
                WHERE status IN ('ok','partial')
                ORDER BY started_at DESC LIMIT 1
                """
            ).fetchone()
            if latest_run:
                already_batched = connection.execute(
                    "SELECT 1 FROM delivery_batches WHERE run_id=? LIMIT 1",
                    (latest_run["run_id"],),
                ).fetchone()
                if already_batched:
                    return None

            query = """
                WITH candidate_events AS (
                    SELECT r.*,
                        CASE
                            WHEN NOT EXISTS (
                                SELECT 1 FROM delivered_events de
                                WHERE de.canonical_id=r.canonical_id
                                  AND de.event_key='initial'
                            ) THEN 'initial'
                            WHEN r.status IN ('corrected','expression_of_concern','retracted')
                              AND NOT EXISTS (
                                SELECT 1 FROM delivered_events de
                                WHERE de.canonical_id=r.canonical_id
                                  AND de.event_key='status:' || r.status
                            ) THEN 'status:' || r.status
                            ELSE NULL
                        END AS delivery_event
                    FROM records r
                )
                SELECT * FROM candidate_events
                WHERE delivery_event IS NOT NULL
                """
            parameters: list[Any] = []
            if latest_run:
                query += " AND last_changed_at >= ?"
                parameters.append(latest_run["started_at"])
            query += """
                ORDER BY score DESC, last_changed_at DESC
                LIMIT ?
                """
            parameters.append(limit)
            rows = connection.execute(query, parameters).fetchall()
            if not rows:
                return None
            batch_id = "batch_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            connection.execute(
                """
                INSERT INTO delivery_batches(batch_id,created_at,status,run_id)
                VALUES (?,?,'pending',?)
                """,
                (batch_id, utc_now(), latest_run["run_id"] if latest_run else None),
            )
            connection.executemany(
                """
                INSERT INTO delivery_items(batch_id,canonical_id,record_version,event_key)
                VALUES (?,?,?,?)
                """,
                [
                    (batch_id, row["canonical_id"], row["version"], row["delivery_event"])
                    for row in rows
                ],
            )
            return self.get_batch(batch_id, connection=connection)

    def get_batch(
        self, batch_id: str, *, connection: sqlite3.Connection | None = None
    ) -> dict[str, Any]:
        owns_connection = connection is None
        if owns_connection:
            connection = sqlite3.connect(self.path)
            connection.row_factory = sqlite3.Row
        assert connection is not None
        try:
            batch = connection.execute(
                "SELECT * FROM delivery_batches WHERE batch_id=?", (batch_id,)
            ).fetchone()
            rows = connection.execute(
                """
                SELECT r.*,di.event_key AS delivery_event FROM records r
                JOIN delivery_items di ON di.canonical_id=r.canonical_id
                WHERE di.batch_id=? AND di.record_version=r.version
                ORDER BY r.score DESC, r.first_seen_at DESC
                """,
                (batch_id,),
            ).fetchall()
            return {
                "batch_id": batch_id,
                "status": batch["status"] if batch else "missing",
                "records": [self.row_to_record(row) for row in rows],
            }
        finally:
            if owns_connection:
                connection.close()

    def mark_delivered(self, batch_id: str) -> None:
        with self.connect() as connection:
            now = utc_now()
            connection.execute(
                """
                INSERT OR IGNORE INTO delivered_versions(canonical_id,record_version,delivered_at)
                SELECT canonical_id,record_version,? FROM delivery_items WHERE batch_id=?
                """,
                (now, batch_id),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO delivered_events(canonical_id,event_key,delivered_at)
                SELECT canonical_id,event_key,? FROM delivery_items WHERE batch_id=?
                """,
                (now, batch_id),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO delivered_events(canonical_id,event_key,delivered_at)
                SELECT di.canonical_id,'status:' || r.status,?
                FROM delivery_items di
                JOIN records r ON r.canonical_id=di.canonical_id
                WHERE di.batch_id=?
                  AND di.event_key='initial'
                  AND r.status IN ('corrected','expression_of_concern','retracted')
                """,
                (now, batch_id),
            )
            connection.execute(
                "UPDATE delivery_batches SET status='delivered', delivered_at=? WHERE batch_id=?",
                (now, batch_id),
            )

    def requeue_batch(self, batch_id: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                DELETE FROM delivered_versions
                WHERE (canonical_id,record_version) IN (
                    SELECT canonical_id,record_version FROM delivery_items WHERE batch_id=?
                )
                """,
                (batch_id,),
            )
            connection.execute(
                """
                DELETE FROM delivered_events
                WHERE (canonical_id,event_key) IN (
                    SELECT canonical_id,event_key FROM delivery_items WHERE batch_id=?
                )
                """,
                (batch_id,),
            )
            connection.execute(
                "UPDATE delivery_batches SET status='pending', delivered_at=NULL WHERE batch_id=?",
                (batch_id,),
            )

    def get_source_cursor(self, source: str) -> str | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT last_success_at FROM source_cursors WHERE source=?", (source,)
            ).fetchone()
            return row[0] if row else None

    def set_source_cursor(self, source: str, timestamp: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO source_cursors(source,last_success_at) VALUES (?,?)
                ON CONFLICT(source) DO UPDATE SET last_success_at=excluded.last_success_at
                """,
                (source, timestamp),
            )

    def maintain(self, *, run_history_days: int = 90, delivery_detail_days: int = 30) -> dict[str, int]:
        with self.connect() as connection:
            removed_runs = connection.execute(
                "DELETE FROM runs WHERE julianday(started_at) < julianday('now', ?)",
                (f"-{max(1, int(run_history_days))} days",),
            ).rowcount
            removable_batches = [
                row[0]
                for row in connection.execute(
                    """
                    SELECT batch_id FROM delivery_batches
                    WHERE status='delivered'
                      AND julianday(delivered_at) < julianday('now', ?)
                    """,
                    (f"-{max(1, int(delivery_detail_days))} days",),
                )
            ]
            removed_items = 0
            removed_batches = 0
            for batch_id in removable_batches:
                removed_items += connection.execute(
                    "DELETE FROM delivery_items WHERE batch_id=?", (batch_id,)
                ).rowcount
                removed_batches += connection.execute(
                    "DELETE FROM delivery_batches WHERE batch_id=?", (batch_id,)
                ).rowcount
            connection.execute("PRAGMA optimize")
            return {
                "runs": removed_runs,
                "delivery_items": removed_items,
                "delivery_batches": removed_batches,
            }

    def status(self) -> dict[str, Any]:
        with self.connect() as connection:
            totals = {
                row["record_type"]: row["count"]
                for row in connection.execute(
                    "SELECT record_type, COUNT(*) AS count FROM records GROUP BY record_type"
                )
            }
            last_run = connection.execute(
                "SELECT * FROM runs ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
            pending = connection.execute(
                "SELECT COUNT(*) FROM delivery_batches WHERE status='pending'"
            ).fetchone()[0]
            compact_history = connection.execute(
                "SELECT COUNT(*) FROM delivered_versions"
            ).fetchone()[0]
            delivered_events = connection.execute(
                "SELECT COUNT(*) FROM delivered_events"
            ).fetchone()[0]
            cursors = {
                row["source"]: row["last_success_at"]
                for row in connection.execute(
                    "SELECT source,last_success_at FROM source_cursors ORDER BY source"
                )
            }
            return {
                "records": totals,
                "pending_batches": pending,
                "delivered_versions": compact_history,
                "delivered_events": delivered_events,
                "source_cursors": cursors,
                "last_run": dict(last_run) if last_run else None,
            }

    @staticmethod
    def row_to_record(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        for key in ("authors_json", "publication_types_json", "mesh_terms_json", "match_reasons_json"):
            value[key.removesuffix("_json")] = json.loads(value.pop(key))
        value["peer_reviewed"] = bool(value["peer_reviewed"])
        return value
