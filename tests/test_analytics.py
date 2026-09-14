import unittest
from datetime import date

from cycling.analytics import (
    aerobic_drift,
    analyze,
    durability,
    power_curve,
    training_load,
)
from cycling.models import AthleteParameters


def rows(power, hr=140, start=0, segment=0, active=True):
    return [
        {
            "elapsed_s": start + i,
            "segment": segment,
            "active": active,
            "power_w": p,
            "hr_bpm": hr,
            "cadence_rpm": 95,
        }
        for i, p in enumerate(power)
    ]


class AnalyticsTests(unittest.TestCase):
    def test_np_constant_30_seconds(self):
        self.assertEqual(
            analyze(rows([200] * 30), AthleteParameters(ftp_w=200))["power"]["np_w"],
            200,
        )

    def test_np_short_array_has_no_window(self):
        self.assertIsNone(
            analyze(rows([200] * 29), AthleteParameters())["power"]["np_w"]
        )

    def test_np_step_array_exact_formula(self):
        result = analyze(rows([100] * 30 + [300] * 30), AthleteParameters())
        averages = [(100 * (30 - k) + 300 * k) / 30 for k in range(31)]
        expected = (sum(value**4 for value in averages) / len(averages)) ** 0.25
        self.assertAlmostEqual(result["power"]["np_w"], expected)

    def test_power_curve_pdc_known_max(self):
        self.assertEqual(
            power_curve(rows([100] * 10 + [300] * 10), [5, 10]), {5: 300, 10: 300}
        )

    def test_power_curve_rejects_invalid_duration(self):
        with self.assertRaises(ValueError):
            power_curve(rows([100] * 10), [0])

    def test_zero_load_is_known(self):
        self.assertEqual(
            analyze(rows([0] * 30), AthleteParameters())["load"]["source"], "power"
        )

    def test_null_load_falls_back_to_rpe(self):
        result = analyze(rows([None] * 30, hr=None), AthleteParameters(), rpe=5)
        self.assertEqual(result["load"]["source"], "rpe")

    def test_missing_parameters_leave_threshold_metrics_unavailable(self):
        result = analyze(rows([200] * 30, hr=140), None, rpe=5)
        self.assertIsNone(result["power"]["intensity_factor"])
        self.assertEqual(result["load"]["source"], "rpe")
        self.assertFalse(result["drift"]["available"])
        self.assertEqual(result["zones"]["three_zone"]["basis"], "unavailable")

    def test_zones_use_all_configured_bounds(self):
        parameters = AthleteParameters(
            ftp_w=100,
            lthr_bpm=100,
            power_zone_fractions=[0.5, 0.75, 1, 1.25, 1.5],
            hr_zone_bounds=[90, 100, 110, 120],
        )
        result = analyze(rows([40, 60, 80, 105, 130, 160], hr=95), parameters)
        self.assertEqual(result["zones"]["power"]["seconds"], [1, 1, 1, 1, 1, 1])
        self.assertEqual(result["zones"]["power"]["bounds"], [50, 75, 100, 125, 150])
        self.assertEqual(result["zones"]["hr"]["seconds"], [0, 6, 0, 0, 0])

    def test_drift_positive_and_paired_coverage(self):
        result = aerobic_drift(
            rows([180] * 600 + [162] * 600, hr=140), AthleteParameters(ftp_w=300)
        )
        self.assertTrue(result["available"])
        self.assertAlmostEqual(result["drift_percent"], 10.0)
        self.assertEqual(result["coverage"], 1.0)
        self.assertEqual(result["eligible_fraction"], 1.0)

    def test_drift_negative(self):
        result = aerobic_drift(
            rows([162] * 600 + [180] * 600, hr=140), AthleteParameters(ftp_w=300)
        )
        self.assertAlmostEqual(result["drift_percent"], -11.1111111111)

    def test_drift_rejects_out_of_range_sample(self):
        result = aerobic_drift(
            rows([180] * 1199 + [250], hr=140), AthleteParameters(ftp_w=300)
        )
        self.assertFalse(result["available"])

    def test_training_load_decay_and_tsb_use_prior_state(self):
        result = training_load(
            {"2026-01-01": 100}, date(2026, 1, 1), date(2026, 1, 3), 2, 2
        )
        self.assertEqual(result[0]["ctl"], 50)
        self.assertFalse(result[1]["known"])
        self.assertIsNone(result[1]["load"])
        self.assertEqual(result[1]["ctl"], 25)
        self.assertEqual(result[1]["tsb"], 0)

    def test_training_load_validates_tau_and_dates(self):
        with self.assertRaises(ValueError):
            training_load({}, date(2026, 1, 2), date(2026, 1, 1))
        with self.assertRaises(ValueError):
            training_load({}, date(2026, 1, 1), date(2026, 1, 1), 0, 7)

    def test_durability_bucket_boundary_and_baseline(self):
        result = durability(
            rows([100] * 10),
            durations=[5],
            thresholds_kj=[0.5],
            historical_fresh_references={5: {"power_w": 100.0}},
        )
        self.assertTrue(result["available"])
        self.assertEqual(result["points"][0]["power_w"], 100)
        self.assertEqual(result["points"][0]["retention_pct"], 100.0)

    def test_durability_rejects_unexplained_gap(self):
        result = durability(
            rows([100] * 5) + rows([100] * 5, start=7), durations=[5], bucket_kj=[0]
        )
        self.assertTrue(result["available"])
        self.assertIn("gaps", result["reason"])

    def test_durability_allows_explicit_inactive_pause(self):
        result = durability(
            rows([100] * 5)
            + rows([None] * 2, start=5, active=False)
            + rows([100] * 5, start=7),
            durations=[5],
            bucket_kj=[0],
        )
        self.assertTrue(result["available"])
        self.assertEqual(result["exposure_seconds"], 10)

    def test_cumulative_distance_uses_increments_and_resets(self):
        data = rows([100] * 4)
        for sample, distance in zip(data, [0, 10, 25, 5], strict=True):
            sample["distance_m"] = distance
        self.assertEqual(analyze(data, AthleteParameters())["distance_m"], 25)

    def test_enriched_interval_detection(self):
        # ftp = 200, 105% = 210. 40 samples at 220W with HR=160, Cadence=90
        sample_rows = rows([220] * 40, hr=160)
        res = analyze(sample_rows, AthleteParameters(ftp_w=200))
        intervals = res["intervals"]
        self.assertEqual(len(intervals), 1)
        inv = intervals[0]
        self.assertEqual(inv["start_s"], 0)
        self.assertEqual(inv["end_s"], 40)
        self.assertEqual(inv["duration_s"], 40)
        self.assertEqual(inv["seconds"], 40)
        self.assertEqual(inv["avg_power_w"], 220.0)
        self.assertEqual(inv["max_power_w"], 220.0)
        self.assertEqual(inv["avg_hr_bpm"], 160.0)
        self.assertEqual(inv["max_hr_bpm"], 160.0)
        self.assertEqual(inv["avg_cadence_rpm"], 95.0)
        self.assertAlmostEqual(inv["relative_power"], 1.1, places=4)
        self.assertEqual(inv["confidence"], 1.0)


if __name__ == "__main__":
    unittest.main()
