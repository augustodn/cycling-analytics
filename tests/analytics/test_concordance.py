import unittest
from datetime import UTC, datetime, timedelta

from cycling.analytics.concordance import analyze_power_hr_windows
from cycling.models import AthleteParameters


def samples(duration_s, power=200, hr=130, cadence=90):
    return [
        {
            "elapsed_s": second,
            "segment": 0,
            "active": True,
            "power_w": power,
            "hr_bpm": hr,
            "cadence_rpm": cadence,
        }
        for second in range(duration_s)
    ]


def analyze(rows, ftp=240):
    return analyze_power_hr_windows(
        rows,
        AthleteParameters(ftp_w=ftp),
        activity_id="ride",
        start_time="2026-01-01T00:00:00Z",
        modality="indoor",
    )


class PowerHRWindowTests(unittest.TestCase):
    def test_one_hour_erg_produces_rolling_valid_windows_and_counts_tail(self):
        result = analyze(samples(3600))

        self.assertEqual(result["diagnostics"]["candidate_windows"], 60)
        self.assertEqual(result["diagnostics"]["valid_windows"], 53)
        self.assertEqual(result["diagnostics"]["rejected_windows"], 7)
        self.assertEqual(
            result["diagnostics"]["rejected_by_reason"]["insufficient_samples"], 7
        )
        self.assertEqual(result["points"][0]["start_s"], 0)
        self.assertEqual(result["points"][0]["represented_seconds"], 60)
        self.assertEqual(
            result["points"][0]["timestamp"],
            (datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=480)).isoformat(),
        )

    def test_zone_boundary_fluctuation_does_not_reject_valid_data(self):
        rows = samples(480, hr=139)
        for row in rows[::2]:
            row["hr_bpm"] = 141

        result = analyze(rows)
        point = result["points"][0]

        self.assertEqual(result["diagnostics"]["valid_windows"], 1)
        self.assertEqual(point["representative_hr_bpm"], 140)
        self.assertEqual(point["hr_zone"], 2)
        self.assertEqual(point["hr_zone_dominance_pct"], 50)
        self.assertEqual(point["quality"], "MEDIUM")

    def test_variability_coasting_and_missing_cadence_are_quality_only(self):
        rows = samples(480, cadence=None)
        for row in rows:
            row["power_w"] = 170 if row["elapsed_s"] % 2 else 230
        rows[1]["power_w"] = 0

        result = analyze(rows)
        point = result["points"][0]

        self.assertEqual(result["diagnostics"]["valid_windows"], 1)
        self.assertGreater(point["power_cv_pct"], 15)
        self.assertGreater(point["power_cv_nonzero_pct"], 14)
        self.assertGreater(point["zero_power_pct"], 0)
        self.assertGreater(point["isolated_zero_power_pct"], 0)
        self.assertEqual(point["zero_power_max_run_s"], 1)
        self.assertIsNone(point["avg_cadence_rpm"])
        self.assertEqual(point["quality"], "LOW")

    def test_coverage_rejections_are_counted_separately(self):
        rows = samples(600)
        for row in rows[:100]:
            row["hr_bpm"] = None

        diagnostics = analyze(rows)["diagnostics"]

        self.assertEqual(diagnostics["candidate_windows"], 10)
        self.assertEqual(diagnostics["valid_windows"], 2)
        self.assertEqual(diagnostics["rejected_windows"], 8)
        self.assertEqual(diagnostics["rejected_by_reason"]["missing_hr"], 1)
        self.assertEqual(diagnostics["rejected_by_reason"]["insufficient_samples"], 7)
        self.assertEqual(diagnostics["coverage_failures"]["hr"], 1)
        self.assertEqual(diagnostics["coverage_failures"]["power"], 0)

        rows_without_power = samples(600)
        for row in rows_without_power[:100]:
            row["power_w"] = None
        power_diagnostics = analyze(rows_without_power)["diagnostics"]
        self.assertEqual(power_diagnostics["rejected_by_reason"]["missing_power"], 1)
        self.assertEqual(power_diagnostics["coverage_failures"]["power"], 1)

    def test_pauses_split_candidate_runs_instead_of_being_joined(self):
        rows = samples(600)
        for row in rows[300:320]:
            row["active"] = False

        diagnostics = analyze(rows)["diagnostics"]

        self.assertEqual(diagnostics["active_runs"], 2)
        self.assertEqual(diagnostics["inactive_seconds"], 20)
        self.assertEqual(diagnostics["candidate_windows"], 10)
        self.assertEqual(diagnostics["valid_windows"], 0)
        self.assertEqual(diagnostics["rejected_by_reason"]["insufficient_samples"], 10)


if __name__ == "__main__":
    unittest.main()
