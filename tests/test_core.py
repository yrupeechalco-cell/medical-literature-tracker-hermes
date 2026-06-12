from __future__ import annotations

import json
import importlib.util
import tempfile
import unittest
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from medlit_tracker.db import Database
from medlit_tracker.scoring import matches_topic, score_record
from medlit_tracker.maintenance import prune_dated_directories


def load_portable_module():
    path = Path(__file__).resolve().parents[1] / "hermes" / "portable.py"
    spec = importlib.util.spec_from_file_location("hermes_portable", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TOPIC = {
    "required_term_groups": [["semaglutide", "tirzepatide"], ["obesity", "overweight"]],
    "outcome_terms": ["cardiovascular", "renal", "safety"],
    "priority_terms": {"randomized controlled trial": 5, "retraction": 10},
}


def record(**overrides):
    value = {
        "source": "pubmed",
        "source_id": "123",
        "source_version": "1",
        "identifiers": [("pmid", "123"), ("doi", "10.1/example")],
        "record_type": "paper",
        "title": "Semaglutide for obesity and cardiovascular outcomes",
        "abstract": "A randomized controlled trial reporting cardiovascular safety.",
        "authors": ["A Author"],
        "journal": "Example Journal",
        "publication_date": "2026-01-01",
        "updated_date": "2026-01-02",
        "study_type": "Randomized Controlled Trial",
        "publication_types": ["Randomized Controlled Trial"],
        "mesh_terms": ["Obesity"],
        "peer_reviewed": True,
        "status": "active",
        "url": "https://pubmed.ncbi.nlm.nih.gov/123/",
        "score": 10,
        "match_reasons": ["test"],
    }
    value.update(overrides)
    return value


class ScoringTests(unittest.TestCase):
    def test_topic_requires_drug_and_population(self):
        self.assertTrue(matches_topic(record(), TOPIC))
        self.assertFalse(matches_topic(record(title="Semaglutide cardiovascular outcomes", abstract=""), TOPIC))

    def test_preprint_penalty_and_rct_bonus(self):
        score, reasons = score_record(record(record_type="preprint"), TOPIC)
        self.assertGreaterEqual(score, 5)
        self.assertIn("preprint -2", reasons)


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / "tracker.sqlite3")

    def tearDown(self):
        self.temp.cleanup()

    def test_doi_merges_pubmed_and_medrxiv_sources(self):
        self.assertEqual(self.db.upsert_record(record()), "new")
        preprint = record(
            source="medrxiv",
            source_id="10.1101/preprint",
            identifiers=[("doi", "10.1101/preprint"), ("doi", "10.1/example")],
            record_type="preprint",
            peer_reviewed=False,
            url="https://medrxiv.org/example",
        )
        self.db.upsert_record(preprint)
        status = self.db.status()
        self.assertEqual(sum(status["records"].values()), 1)

    def test_pending_batch_repeats_until_marked(self):
        self.db.upsert_record(record())
        first = self.db.create_pending_batch()
        second = self.db.create_pending_batch()
        self.assertEqual(first["batch_id"], second["batch_id"])
        self.db.mark_delivered(first["batch_id"])
        self.assertIsNone(self.db.create_pending_batch())
        self.db.requeue_batch(first["batch_id"])
        self.assertEqual(self.db.create_pending_batch()["batch_id"], first["batch_id"])

    def test_lower_priority_source_does_not_overwrite_pubmed(self):
        self.db.upsert_record(record())
        lower_priority = record(
            source="europe_pmc",
            source_id="123",
            title="Shorter metadata title",
            abstract="",
        )
        self.assertEqual(self.db.upsert_record(lower_priority), "existing")
        batch = self.db.create_pending_batch()
        self.assertEqual(batch["records"][0]["title"], record()["title"])
        self.assertEqual(batch["records"][0]["version"], 1)

    def test_record_update_increments_version_and_becomes_pending(self):
        self.db.upsert_record(record())
        first = self.db.create_pending_batch()
        self.db.mark_delivered(first["batch_id"])
        self.assertEqual(self.db.upsert_record(record(abstract="Changed abstract")), "updated")
        second = self.db.create_pending_batch()
        self.assertIsNotNone(second)
        self.assertEqual(second["records"][0]["version"], 2)

    def test_delivery_history_survives_detail_pruning(self):
        self.db.upsert_record(record())
        batch = self.db.create_pending_batch()
        self.db.mark_delivered(batch["batch_id"])
        with self.db.connect() as connection:
            connection.execute(
                "UPDATE delivery_batches SET delivered_at='2000-01-01T00:00:00+00:00' WHERE batch_id=?",
                (batch["batch_id"],),
            )
        removed = self.db.maintain(delivery_detail_days=1)
        self.assertEqual(removed["delivery_batches"], 1)
        self.assertIsNone(self.db.create_pending_batch())

    def test_source_cursor_is_disk_backed(self):
        self.db.set_source_cursor("pubmed", "2026-06-12T00:00:00+00:00")
        reopened = Database(Path(self.temp.name) / "tracker.sqlite3")
        self.assertEqual(
            reopened.get_source_cursor("pubmed"), "2026-06-12T00:00:00+00:00"
        )

    def test_many_seen_records_do_not_require_in_memory_set(self):
        for index in range(3000):
            self.db.upsert_record(
                record(
                    source_id=str(index),
                    identifiers=[("pmid", str(index))],
                    title=f"Paper {index}",
                    abstract="x" * 50,
                )
            )
        reopened = Database(Path(self.temp.name) / "tracker.sqlite3", cache_kib=512)
        self.assertEqual(sum(reopened.status()["records"].values()), 3000)
        self.assertLess((Path(self.temp.name) / "tracker.sqlite3").stat().st_size, 8_000_000)

    def test_old_unselected_records_do_not_roll_into_future_runs(self):
        self.db.start_run("run-1")
        self.db.upsert_record(record(source_id="1", identifiers=[("pmid", "1")], title="High"))
        self.db.upsert_record(
            record(source_id="2", identifiers=[("pmid", "2")], title="Low", score=0)
        )
        self.db.finish_run("run-1", "ok", {}, [])
        first = self.db.create_pending_batch(limit=1)
        self.assertIsNotNone(first)
        self.db.mark_delivered(first["batch_id"])
        self.assertIsNone(self.db.create_pending_batch(limit=1))

        self.db.start_run("run-2")
        self.db.upsert_record(record(source_id="1", identifiers=[("pmid", "1")], title="High"))
        self.db.upsert_record(
            record(source_id="2", identifiers=[("pmid", "2")], title="Low", score=0)
        )
        self.db.finish_run("run-2", "ok", {}, [])

        self.assertIsNone(self.db.create_pending_batch(limit=1))


class MaintenanceTests(unittest.TestCase):
    def test_prunes_only_expired_date_directories(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old = root / (date.today() - timedelta(days=20)).isoformat()
            recent = root / date.today().isoformat()
            old.mkdir()
            recent.mkdir()
            (old / "old.bin").write_bytes(b"x" * 10)
            (recent / "new.bin").write_bytes(b"x")
            result = prune_dated_directories(root, retention_days=7)
            self.assertEqual(result["directories"], 1)
            self.assertFalse(old.exists())
            self.assertTrue(recent.exists())


class HermesPortableTests(unittest.TestCase):
    def test_job_lock_is_hermes_only_and_deepseek_pro(self):
        portable = load_portable_module()
        job = {"enabled_toolsets": ["web", "delegate"], "skills": ["other-agent"]}

        portable.lock_job(job, deliver="feishu:test")

        self.assertEqual(job["provider"], "deepseek")
        self.assertEqual(job["model"], "deepseek-v4-pro")
        self.assertEqual(job["enabled_toolsets"], ["terminal", "file"])
        self.assertEqual(job["skills"], [])
        self.assertIsNone(job["skill"])
        self.assertEqual(job["medical_literature_tracker"]["runtime"], "hermes-only")


if __name__ == "__main__":
    unittest.main()
