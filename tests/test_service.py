"""End-to-end contracts on synthetic sources and disposable storage."""

import json
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import ValidationError

from cycling.api import create_app
from cycling.ingestion import ingest, sha256
from cycling.models import (
    ActivityContext,
    ActivityRequest,
    AthleteParameters,
    ComparisonRequest,
    CurveRequest,
    DurabilityRequest,
    LoadRequest,
    PeriodHRDistributionRequest,
    PeriodPowerCurveRequest,
    StreamRequest,
)
from cycling.service import CyclingService
from cycling.storage import Store
from tests.test_parsers import tcx_text


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.sources = self.root / "originals"
        self.sources.mkdir()
        self.path = self.sources / "synthetic.tcx"
        self.path.write_text(tcx_text())
        self.data = self.root / "data"
        self.store = Store(self.data)
        self.service = CyclingService(self.store)

    def tearDown(self):
        self.store.close()
        self.temporary.cleanup()

    def ingest(self):
        result = ingest(self.store, self.sources)
        self.assertEqual(result["errors"], [])
        return sha256(self.path)

    def test_idempotent_hash_aliases_and_source_unchanged(self):
        original = self.path.read_bytes()
        ident = self.ingest()
        self.service.analyze_activity(ActivityRequest(activity_id=ident))
        count = self.store.status()["metric_snapshots"]
        self.assertEqual(ingest(self.store, self.sources)["skipped"], 1)
        self.service.analyze_activity(ActivityRequest(activity_id=ident))
        self.assertEqual(self.store.status()["metric_snapshots"], count)
        (self.sources / "alias.TCX").write_bytes(original)
        self.assertEqual(ingest(self.store, self.sources)["skipped"], 2)
        self.assertEqual(self.store.status()["activities"], 1)
        self.assertEqual(len(self.store.status()["duplicate_hashes"]), 1)
        self.assertEqual(self.path.read_bytes(), original)

    def test_failed_source_retry_and_missing_source_catalog(self):
        self.path.write_text("not XML")
        self.assertEqual(len(ingest(self.store, self.sources)["errors"]), 1)
        self.assertEqual(self.store.status()["source_counts"], {"error": 1})
        self.path.write_text(tcx_text())
        self.ingest()
        self.path.unlink()
        ingest(self.store, self.sources)
        self.assertEqual(self.store.status()["source_counts"], {"missing": 1})
        self.assertEqual(self.store.status()["activities"], 1)

    def test_missing_parquet_repairs_and_force_creates_snapshot(self):
        ident = self.ingest()
        self.service.analyze_activity(ActivityRequest(activity_id=ident))
        original = self.store.activity(ident)["sample_path"]
        (self.data / original).unlink()
        self.assertEqual(ingest(self.store, self.sources)["ingested"], 1)
        self.assertEqual(self.store.activity(ident)["sample_path"], original)
        self.assertTrue((self.data / original).is_file())
        self.service.analyze_activity(ActivityRequest(activity_id=ident), force=True)
        self.assertEqual(self.store.status()["metric_snapshots"], 2)

    def test_failed_parquet_write_leaves_previous_snapshot(self):
        ident = self.ingest()
        original = self.store.activity(ident)["sample_path"]
        with patch("cycling.storage.pq.write_table", side_effect=OSError("disk full")):
            self.assertEqual(
                len(ingest(self.store, self.sources, force=True)["errors"]), 1
            )
        self.assertEqual(self.store.activity(ident)["sample_path"], original)
        self.assertTrue(self.store.samples(ident))

    def test_historical_current_parameters_append_only(self):
        ident = self.ingest()
        old = self.service.analyze_activity(ActivityRequest(activity_id=ident))
        new_id = self.store.add_parameters(
            AthleteParameters(effective_date=date(2026, 9, 1), ftp_w=300)
        )
        historical = self.service.analyze_activity(ActivityRequest(activity_id=ident))
        current = self.service.analyze_activity(
            ActivityRequest(activity_id=ident, parameter_mode="current")
        )
        self.assertEqual(old.parameter_id, historical.parameter_id)
        self.assertEqual(current.parameter_id, new_id)
        self.assertNotEqual(
            current.data["metrics"]["load"]["value"],
            old.data["metrics"]["load"]["value"],
        )
        self.assertEqual(len(self.store.parameter_history()), 2)
        self.assertEqual(self.store.parameters(date(2025, 12, 31))[0], old.parameter_id)

    def test_settings_future_dated_parameter_mode_current(self):
        ident = self.ingest()
        future_id = self.store.add_parameters(
            AthleteParameters(effective_date=date(2035, 1, 1), ftp_w=350)
        )
        _, historical_ident, historical_params = self.service.settings(
            ActivityRequest(activity_id=ident, parameter_mode="historical")
        )
        _, current_ident, current_params = self.service.settings(
            ActivityRequest(activity_id=ident, parameter_mode="current")
        )
        self.assertEqual(current_ident, future_id)
        self.assertEqual(current_params.ftp_w, 350)
        self.assertNotEqual(current_ident, historical_ident)

    def test_all_semantic_tools(self):
        ident = self.ingest()
        self.assertEqual(
            self.service.power_curve(CurveRequest(activity_id=ident)).data["watts"][
                "5"
            ],
            200,
        )
        self.assertTrue(
            self.service.durability(DurabilityRequest(activity_id=ident)).data[
                "available"
            ]
        )
        self.assertFalse(
            self.service.drift(ActivityRequest(activity_id=ident)).data["available"]
        )
        self.assertIsNone(
            self.service.thresholds(ActivityRequest(activity_id=ident)).data[
                "estimate"
            ]["watts"]
        )
        rows = self.service.stream(
            StreamRequest(activity_id=ident, start_s=10, end_s=30, max_points=5)
        ).data["samples"]
        self.assertLessEqual(len(rows), 5)
        self.assertTrue(all(10 <= row["elapsed_s"] <= 30 for row in rows))
        for basis in ("time", "sessions", "load"):
            result = self.service.load(
                LoadRequest(
                    start=date(2026, 1, 1),
                    end=date(2026, 1, 3),
                    modality="unknown",
                    basis=basis,
                )
            )
            self.assertEqual(len(result.data["days"]), 3)
            self.assertEqual(result.data["distribution"]["basis"], basis)
            self.assertGreater(
                result.data["days"][0]["ctl"], result.data["days"][1]["ctl"]
            )
        global_result = self.service.load(
            LoadRequest(
                start=date(2026, 1, 1),
                end=date(2026, 1, 3),
                modality="all",
            )
        )
        self.assertEqual(global_result.data["modality"], "all")
        self.assertEqual(len(global_result.data["activities"]), 1)
        with self.assertRaises(ValueError):
            self.service.compare(ComparisonRequest(activity_ids=[ident, ident]))
        self.assertEqual(
            len(
                self.service.compare(
                    ComparisonRequest(activity_ids=[ident, ident], allow_mixed=True)
                ).data["activities"]
            ),
            2,
        )

    def test_no_power_durability_is_unavailable_hr_load_survives(self):
        self.path.write_text(tcx_text(power=False))
        ident = self.ingest()
        result = self.service.analyze_activity(ActivityRequest(activity_id=ident))
        self.assertIsNone(result.data["metrics"]["power"]["work_kj"])
        self.assertEqual(result.data["metrics"]["load"]["source"], "hr")
        durability = self.service.durability(DurabilityRequest(activity_id=ident)).data
        self.assertFalse(durability["available"])
        self.assertEqual(durability["buckets"], [])

    def test_persisted_rpe_and_explicit_modality(self):
        self.path.write_text(tcx_text(power=False).replace("<Value>140</Value>", ""))
        ident = self.ingest()
        before = self.service.analyze_activity(ActivityRequest(activity_id=ident))
        self.assertEqual(before.data["metrics"]["load"]["source"], "unavailable")
        self.store.set_context(ident, ActivityContext(rpe=6, modality="mtb"))
        after = self.service.analyze_activity(ActivityRequest(activity_id=ident))
        self.assertEqual(after.data["metrics"]["load"]["source"], "rpe")
        self.assertEqual(after.data["activity"]["modality"], "mtb")
        result = self.service.load(
            LoadRequest(start=date(2026, 1, 1), end=date(2026, 1, 1), modality="mtb")
        )
        self.assertEqual(result.data["sources"], {"rpe": 1})
        self.assertFalse(
            self.service.durability(DurabilityRequest(activity_id=ident)).data[
                "available"
            ]
        )

    def test_durability_uses_other_activity_as_90_day_fresh_reference(self):
        def rows(power):
            return [
                {
                    "elapsed_s": index,
                    "segment": 0,
                    "active": True,
                    "power_w": value,
                }
                for index, value in enumerate(power)
            ]

        self.store.write_activity(
            "historical",
            {
                "start_time": "2026-01-01T00:00:00Z",
                "elapsed_seconds": 10,
                "modality": "road",
                "quality_flags": [],
            },
            rows([300] * 10),
        )
        self.store.write_activity(
            "target",
            {
                "start_time": "2026-01-02T00:00:00Z",
                "elapsed_seconds": 20,
                "modality": "road",
                "quality_flags": [],
            },
            rows([100] * 10 + [300] * 10),
        )

        result = self.service.durability(
            DurabilityRequest(activity_id="target", durations=[5], thresholds_kj=[0.5])
        )
        assert result.data["fresh_reference"]["5"]["activity_id"] == "historical"
        assert result.data["fresh_reference"]["5"]["source"] == "historical_90d"

    def test_read_only_download_state_reconciliation(self):
        state = self.sources / ".state.json"
        original = json.dumps(
            {
                "activities": {
                    "1": {"status": "downloaded", "result": "missing.tcx"},
                    "2": {"status": "failed", "result": "export failed"},
                }
            }
        )
        state.write_text(original)
        self.ingest()
        self.assertEqual(self.store.status()["source_counts"]["missing"], 1)
        audit = self.store.status()["acquisition_audits"][0]
        self.assertEqual(len(audit["failed_records"]), 1)
        self.assertEqual(state.read_text(), original)

    def test_cli_module_entrypoint(self):
        # A subprocess cannot share a DuckDB writer; use its own disposable catalog.
        directory = str(self.root / "cli-data")

        def cli(*args):
            run = subprocess.run(
                [sys.executable, "-m", "cycling", "--data-dir", directory, *args],
                capture_output=True,
                text=True,
                check=True,
            )
            return json.loads(run.stdout)

        self.assertEqual(cli("ingest", str(self.sources))["ingested"], 1)
        ident = cli("activities", "list")["data"]["activities"][0]["id"]
        self.assertEqual(
            cli("activity", "analyze", ident)["operation"], "analyze_activity"
        )
        self.assertEqual(cli("power-curve", ident)["data"]["watts"]["5"], 200)
        self.assertTrue(cli("durability", ident)["data"]["available"])
        self.assertEqual(cli("reprocess")["processed"], 1)
        self.assertEqual(
            cli(
                "load",
                "--start",
                "2026-01-01",
                "--end",
                "2026-01-02",
                "--modality",
                "unknown",
            )["operation"],
            "load",
        )

    def test_declared_load_time_constants_and_override(self):
        self.ingest()
        self.store.add_parameters(
            AthleteParameters(effective_date=date(2026, 1, 1), ctl_days=30, atl_days=5)
        )
        request = LoadRequest(
            start=date(2026, 1, 1), end=date(2026, 1, 2), modality="unknown"
        )
        self.assertEqual(self.service.load(request).data["ctl_days"], 30)
        self.assertEqual(
            self.service.load(request.model_copy(update={"ctl_days": 42})).data[
                "ctl_days"
            ],
            42,
        )

    def test_validation(self):
        for model, data in (
            (StreamRequest, {"activity_id": "x", "start_s": 5, "end_s": 2}),
            (CurveRequest, {"activity_id": "x", "durations": [0]}),
            (DurabilityRequest, {"activity_id": "x", "bucket_kj": [0, 1, 1]}),
            (AthleteParameters, {"ftp_w": 0}),
            (AthleteParameters, {"power_zone_fractions": [float("nan")]}),
            (
                LoadRequest,
                {"start": "2026-02-01", "end": "2026-01-01", "modality": "road"},
            ),
            (
                PeriodPowerCurveRequest,
                {"period": "custom", "start_date": "2026-01-01"},
            ),
            (
                PeriodHRDistributionRequest,
                {
                    "period": "custom",
                    "start_date": "2026-01-02",
                    "end_date": "2026-01-01",
                },
            ),
        ):
            with self.assertRaises(ValidationError):
                model.model_validate(data)

    def test_http_contracts_validation_and_missing_data(self):
        ident = self.ingest()
        with TestClient(create_app(self.data)) as client:
            self.assertEqual(client.get("/activities").status_code, 200)
            for endpoint in (
                "activity",
                "activity/analyze",
                "power-curve",
                "durability",
                "drift",
                "thresholds",
                "stream",
            ):
                response = client.post(f"/{endpoint}", json={"activity_id": ident})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["algorithm_version"], "mvp-2")
            self.assertEqual(
                client.post("/activity", json={"activity_id": "missing"}).status_code,
                404,
            )
            self.assertEqual(
                client.post(
                    "/stream", json={"activity_id": ident, "max_points": 0}
                ).status_code,
                422,
            )
            self.assertEqual(
                client.post(
                    "/load",
                    json={
                        "start": "2026-01-01",
                        "end": "2026-01-03",
                        "modality": "unknown",
                    },
                ).status_code,
                200,
            )
            self.assertEqual(
                client.post(
                    "/compare", json={"activity_ids": [ident, ident]}
                ).status_code,
                422,
            )
            self.assertEqual(
                client.post(
                    "/activity/context", json={"activity_id": ident, "rpe": 7.0}
                ).status_code,
                200,
            )
            self.assertEqual(client.get("/health").status_code, 200)
            self.assertEqual(
                client.post(
                    "/power-curves", json={"period": "90d", "modality": "road"}
                ).status_code,
                200,
            )
            self.assertEqual(client.get("/parameters").status_code, 200)
            self.assertIn("paths", client.get("/openapi.json").json())

    def test_period_power_curve_filtering_and_modality_isolation(self):
        ident = self.ingest()
        self.store.set_context(ident, ActivityContext(rpe=5.0, modality="road"))
        res_all = self.service.period_power_curve(
            PeriodPowerCurveRequest(period="30d", modality="road")
        )
        self.assertEqual(res_all.operation, "period_power_curve")
        self.assertIn("5", res_all.data["watts"])
        self.assertEqual(res_all.data["records"]["5"]["activity_id"], ident)

        # Modality isolation check: requesting "mtb" should return null for activity with "road" modality
        res_mtb = self.service.period_power_curve(
            PeriodPowerCurveRequest(period="30d", modality="mtb")
        )
        self.assertIsNone(res_mtb.data["watts"]["5"])
        self.assertIsNone(res_mtb.data["records"]["5"])

    def test_custom_period_filter_is_inclusive(self):
        ident = self.ingest()
        start = date(2026, 1, 1)

        power_res = self.service.period_power_curve(
            PeriodPowerCurveRequest(
                period="custom",
                start_date=start,
                end_date=start,
            )
        )
        self.assertEqual(power_res.data["activities_evaluated"], 1)
        self.assertEqual(power_res.data["records"]["5"]["activity_id"], ident)

        hr_res = self.service.period_hr_distribution(
            PeriodHRDistributionRequest(
                period="custom",
                start_date=start,
                end_date=start,
            )
        )
        self.assertEqual(hr_res.data["activity_count"], 1)

        outside_res = self.service.period_power_curve(
            PeriodPowerCurveRequest(
                period="custom",
                start_date=date(2026, 1, 2),
                end_date=date(2026, 1, 3),
            )
        )
        self.assertEqual(outside_res.data["activities_evaluated"], 0)

    def test_semantic_service_aliases(self):
        ident = self.ingest()
        req_act = ActivityRequest(activity_id=ident)
        req_stream = StreamRequest(activity_id=ident)
        req_load = LoadRequest(
            start=date(2026, 1, 1), end=date(2026, 1, 2), modality="unknown"
        )
        self.assertEqual(
            self.service.get_activity_stream(req_stream).operation, "stream"
        )
        self.assertEqual(self.service.get_hr_drift(req_act).operation, "drift")
        self.assertEqual(
            self.service.get_aerobic_efficiency(req_act).operation, "drift"
        )
        self.assertEqual(self.service.get_training_load(req_load).operation, "load")
        self.assertEqual(
            self.service.get_training_distribution(req_load).operation, "load"
        )
        self.assertEqual(
            self.service.estimate_thresholds(req_act).operation, "thresholds"
        )

    def test_reprocess_selection_cli(self):
        directory = str(self.root / "cli-reprocess-data")

        def cli(*args):
            run = subprocess.run(
                [sys.executable, "-m", "cycling", "--data-dir", directory, *args],
                capture_output=True,
                text=True,
                check=True,
            )
            return json.loads(run.stdout)

        cli("ingest", str(self.sources))
        reprocess_res = cli("reprocess", "--metric", "power", "--from", "2026-01-01")
        self.assertEqual(reprocess_res["processed"], 1)
        self.assertEqual(reprocess_res["metric"], "power")
        self.assertEqual(reprocess_res["from_date"], "2026-01-01")

        reprocess_future = cli("reprocess", "--from", "2099-01-01")
        self.assertEqual(reprocess_future["processed"], 0)

        pc_res = cli("power-curves", "--period", "30d", "--modality", "all")
        self.assertEqual(pc_res["operation"], "period_power_curve")

    def test_power_curve_extended_durations_and_null_behavior(self):
        ident = self.ingest()
        res = self.service.power_curve(CurveRequest(activity_id=ident))
        watts = res.data["watts"]
        # Standard durations up to 21600 (6h) are present
        self.assertIn("1800", watts)
        self.assertIn("3600", watts)
        self.assertIn("21600", watts)
        # Synthetic activity is short (under 1h), so 6h window max power is None
        self.assertIsNone(watts["21600"])

        period_res = self.service.period_power_curve(
            PeriodPowerCurveRequest(period="all", modality="all")
        )
        period_watts = period_res.data["watts"]
        self.assertIn("21600", period_watts)
        self.assertIsNone(period_watts["21600"])

    def test_hr_distribution_single_and_period(self):
        ident = self.ingest()
        res = self.service.hr_distribution(ActivityRequest(activity_id=ident))
        self.assertEqual(res.operation, "hr_distribution")
        self.assertEqual(res.data["basis"], "hr")
        self.assertEqual(len(res.data["seconds"]), 7)
        self.assertEqual(len(res.data["percentages"]), 7)
        self.assertEqual(len(res.data["zones"]), 7)
        self.assertEqual(res.data["zones"][0]["label"], "1 Recovery")

        period_res = self.service.period_hr_distribution(
            PeriodHRDistributionRequest(period="all", modality="all")
        )
        self.assertEqual(period_res.operation, "period_hr_distribution")
        self.assertEqual(period_res.data["basis"], "hr")
        self.assertEqual(len(period_res.data["seconds"]), 7)

    def test_cli_compatibility_extensions(self):
        directory = str(self.root / "cli-ext-data")

        def cli(*args, check=True):
            run = subprocess.run(
                [sys.executable, "-m", "cycling", "--data-dir", directory, *args],
                capture_output=True,
                text=True,
                check=check,
            )
            return run.returncode, json.loads(run.stdout) if run.stdout else json.loads(
                run.stderr
            )

        code, res = cli("ingest", str(self.sources), "--new-only")
        self.assertEqual(code, 0)
        self.assertEqual(res["ingested"], 1)

        code, res = cli("reprocess", "--all")
        self.assertEqual(code, 0)
        self.assertEqual(res["processed"], 1)

        # power-curve without activity_id (period mode)
        code, res = cli("power-curve", "--period", "30d", "--modality", "road")
        self.assertEqual(code, 0)
        self.assertEqual(res["operation"], "period_power_curve")

        # power-curve with end-date
        code, res = cli("power-curve", "--period", "90d", "--end-date", "2026-06-01")
        self.assertEqual(code, 0)
        self.assertEqual(res["operation"], "period_power_curve")

        code, res = cli(
            "power-curves",
            "--period",
            "custom",
            "--start-date",
            "2026-01-01",
            "--end-date",
            "2026-01-01",
        )
        self.assertEqual(code, 0)
        self.assertEqual(res["data"]["activities_evaluated"], 1)

        # activities list filtering
        code, res = cli("activities", "list", "--limit", "1", "--modality", "all")
        self.assertEqual(code, 0)
        self.assertEqual(len(res["data"]["activities"]), 1)

        # Ambiguous invocation rejection: activity_id with period flag
        ident = res["data"]["activities"][0]["id"]
        code, res = cli("power-curve", ident, "--period", "30d", check=False)
        self.assertEqual(code, 2)
        self.assertIn("error", res)

        # Date validation failure
        code, res = cli("reprocess", "--from", "invalid-date", check=False)
        self.assertEqual(code, 2)
        self.assertIn("error", res)


if __name__ == "__main__":
    unittest.main()
