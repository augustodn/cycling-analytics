"""Focused unit tests for ingestion discovery, hashing, idempotency, and storage parameter modes."""

import tempfile
import unittest
from datetime import date
from pathlib import Path

from cycling.ingestion import discover_sources, extract_strava_hint, ingest, sha256
from cycling.models import AthleteParameters
from cycling.storage import Store
from tests.test_parsers import tcx_text


class IngestionStorageTests(unittest.TestCase):
    def test_discover_sources_and_strava_hint(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            sub_dir = tmp_path / "sub"
            sub_dir.mkdir()

            file1 = tmp_path / "12345678_morning_ride.tcx"
            file2 = sub_dir / "987654321.fit"
            file_txt = tmp_path / "readme.txt"

            file1.write_text(tcx_text())
            file2.write_bytes(b"dummy fit content")
            file_txt.write_text("hello")

            discovered = discover_sources(tmp_path)
            self.assertEqual(len(discovered), 2)
            self.assertIn(file1, discovered)
            self.assertIn(file2, discovered)

            self.assertEqual(extract_strava_hint(file1), "12345678")
            self.assertEqual(extract_strava_hint(file2), "987654321")
            self.assertIsNone(extract_strava_hint(Path("ride_without_id.tcx")))

    def test_sha256(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            file_path = Path(tmp_dir) / "sample.tcx"
            file_path.write_text("test content")
            digest = sha256(file_path)
            self.assertEqual(len(digest), 64)

    def test_idempotent_ingest_and_parquet_path(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root_path = Path(tmp_dir)
            store_root = root_path / ".cycling"
            downloads_root = root_path / "downloads" / "strava"
            downloads_root.mkdir(parents=True)

            tcx_file = downloads_root / "88889999_test.tcx"
            tcx_file.write_text(tcx_text())

            store = Store(root=store_root)
            digest = sha256(tcx_file)

            # First ingest run
            res1 = ingest(store, source=downloads_root)
            self.assertEqual(res1["discovered"], 1)
            self.assertEqual(res1["ingested"], 1)
            self.assertEqual(res1["skipped"], 0)
            self.assertEqual(len(res1["errors"]), 0)

            # Check activity and parquet file
            act = store.activity(digest)
            self.assertEqual(act["id"], digest)
            self.assertEqual(act["strava_id_hint"], "88889999")
            self.assertTrue(act["sample_path"].startswith("samples/2026/01/"))
            self.assertTrue((store_root / act["sample_path"]).is_file())

            # Second ingest run (idempotent skip)
            res2 = ingest(store, source=downloads_root)
            self.assertEqual(res2["discovered"], 1)
            self.assertEqual(res2["ingested"], 0)
            self.assertEqual(res2["skipped"], 1)

            store.close()

    def test_parameter_selection_historical_vs_current(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = Store(root=Path(tmp_dir) / ".cycling")

            p1 = AthleteParameters(effective_date=date(2025, 1, 1), ftp_w=250)
            p2 = AthleteParameters(effective_date=date(2026, 1, 1), ftp_w=285)
            p3 = AthleteParameters(effective_date=date(2026, 6, 1), ftp_w=300)

            store.add_parameters(p1)
            store.add_parameters(p2)
            store.add_parameters(p3)

            # Historical mode for date 2025-06-01 -> should select p1 (250W)
            _, param_hist_2025 = store.parameters(
                when=date(2025, 6, 1), mode="historical"
            )
            self.assertEqual(param_hist_2025.ftp_w, 250)

            # Historical mode for date 2026-03-01 -> should select p2 (285W)
            _, param_hist_2026 = store.parameters(
                when=date(2026, 3, 1), mode="historical"
            )
            self.assertEqual(param_hist_2026.ftp_w, 285)

            # Current mode for any date -> should select latest p3 (300W)
            _, param_curr = store.parameters(when=date(2025, 1, 1), mode="current")
            self.assertEqual(param_curr.ftp_w, 300)

            store.close()

    def test_duckdb_catalog_tables_exist(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = Store(root=Path(tmp_dir) / ".cycling")
            tables = [
                r[0]
                for r in store.db.execute(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
                ).fetchall()
            ]
            expected = [
                "sources",
                "activities",
                "parameters",
                "metrics",
                "intervals",
                "power_curve",
                "daily_training_load",
                "processing_runs",
                "quality_flags",
            ]
            for tbl in expected:
                self.assertIn(tbl, tables)
            store.close()


if __name__ == "__main__":
    unittest.main()
