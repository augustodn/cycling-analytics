import unittest

from cycling.analytics import analyze
from cycling.analytics.summaries import (
    summarize_cadence,
    summarize_elevation,
    summarize_power,
    summarize_speed,
)
from cycling.models import AthleteParameters


class MetricCompletenessTests(unittest.TestCase):
    def test_power_max_zero_and_coasting(self):
        # 10 samples: 5 non-zero power (100W..500W), 5 zero power (0W)
        samples = [
            {"elapsed_s": i, "active": True, "power_w": p}
            for i, p in enumerate([100, 200, 300, 400, 500, 0, 0, 0, 0, 0])
        ]
        summary = summarize_power(samples)
        self.assertEqual(summary.max_w, 500.0)
        self.assertEqual(summary.zero_power_seconds, 5)
        self.assertEqual(summary.coasting_pct, 50.0)
        self.assertEqual(summary.avg_w, 150.0)

    def test_power_null_versus_zero(self):
        # 5 null power samples vs 5 zero power samples
        null_samples = [
            {"elapsed_s": i, "active": True, "power_w": None} for i in range(5)
        ]
        summary_null = summarize_power(null_samples)
        self.assertIsNone(summary_null.max_w)
        self.assertEqual(summary_null.zero_power_seconds, 0)
        self.assertIsNone(summary_null.coasting_pct)
        self.assertIsNone(summary_null.avg_w)

        zero_samples = [
            {"elapsed_s": i, "active": True, "power_w": 0} for i in range(5)
        ]
        summary_zero = summarize_power(zero_samples)
        self.assertEqual(summary_zero.max_w, 0.0)
        self.assertEqual(summary_zero.zero_power_seconds, 5)
        self.assertEqual(summary_zero.coasting_pct, 100.0)
        self.assertEqual(summary_zero.avg_w, 0.0)

    def test_cadence_max_and_target_range(self):
        # 10 samples: cadences [80, 85, 90, 95, 100, 105, 92, 98, 70, 110]
        # 90..100 inclusive: 90, 95, 100, 92, 98 -> 5 samples
        samples = [
            {"elapsed_s": i, "active": True, "cadence_rpm": c}
            for i, c in enumerate([80, 85, 90, 95, 100, 105, 92, 98, 70, 110])
        ]
        summary = summarize_cadence(samples)
        self.assertEqual(summary.max_rpm, 110.0)
        self.assertEqual(summary.target_range_seconds, 5)
        self.assertEqual(summary.target_range_pct, 50.0)

    def test_cadence_null_data(self):
        samples = [
            {"elapsed_s": i, "active": True, "cadence_rpm": None} for i in range(5)
        ]
        summary = summarize_cadence(samples)
        self.assertIsNone(summary.max_rpm)
        self.assertEqual(summary.target_range_seconds, 0)
        self.assertIsNone(summary.target_range_pct)

    def test_speed_direct_and_derived(self):
        # Direct speed_mps
        samples_direct = [
            {"elapsed_s": i, "active": True, "speed_mps": s}
            for i, s in enumerate([5.0, 10.0, 15.0])
        ]
        summary_direct = summarize_speed(samples_direct)
        self.assertEqual(summary_direct.avg_m_s, 10.0)
        self.assertEqual(summary_direct.max_m_s, 15.0)
        self.assertEqual(summary_direct.avg_km_h, 36.0)
        self.assertEqual(summary_direct.max_km_h, 54.0)

        # Derived from distance_m
        samples_derived = [
            {"elapsed_s": 0, "active": True, "distance_m": 0.0},
            {"elapsed_s": 1, "active": True, "distance_m": 10.0},
            {"elapsed_s": 2, "active": True, "distance_m": 30.0},
        ]
        summary_derived = summarize_speed(samples_derived)
        self.assertEqual(summary_derived.avg_m_s, 15.0)  # (10 + 20) / 2 = 15.0
        self.assertEqual(summary_derived.max_m_s, 20.0)

    def test_speed_null_data(self):
        samples = [{"elapsed_s": i, "active": True} for i in range(5)]
        summary = summarize_speed(samples)
        self.assertIsNone(summary.avg_m_s)
        self.assertIsNone(summary.max_m_s)
        self.assertIsNone(summary.avg_km_h)
        self.assertIsNone(summary.max_km_h)

    def test_elevation_gain_loss_and_resets(self):
        # Altitudes: 100, 110 (+10), 105 (-5), 120 (+15) -> gain=25, loss=5
        samples = [
            {"elapsed_s": 0, "active": True, "altitude_m": 100.0, "distance_m": 0.0},
            {"elapsed_s": 1, "active": True, "altitude_m": 110.0, "distance_m": 10.0},
            {"elapsed_s": 2, "active": True, "altitude_m": 105.0, "distance_m": 20.0},
            {"elapsed_s": 3, "active": True, "altitude_m": 120.0, "distance_m": 30.0},
        ]
        summary = summarize_elevation(samples)
        self.assertEqual(summary.gain_m, 25.0)
        self.assertEqual(summary.loss_m, 5.0)

        # Reset / gap handling: distance reset or >10s time gap should not jump elevation
        samples_with_reset = [
            {"elapsed_s": 0, "active": True, "altitude_m": 100.0, "distance_m": 10.0},
            {"elapsed_s": 1, "active": True, "altitude_m": 110.0, "distance_m": 20.0},
            # Reset: distance drops to 0, altitude changes from 110 to 50
            {"elapsed_s": 2, "active": True, "altitude_m": 50.0, "distance_m": 0.0},
            {"elapsed_s": 3, "active": True, "altitude_m": 60.0, "distance_m": 10.0},
        ]
        summary_reset = summarize_elevation(samples_with_reset)
        # 100->110 (+10), reset skipped (110->50 ignored), 50->60 (+10) -> gain=20, loss=0
        self.assertEqual(summary_reset.gain_m, 20.0)
        self.assertEqual(summary_reset.loss_m, 0.0)

    def test_elevation_null_data(self):
        samples = [{"elapsed_s": i, "active": True} for i in range(5)]
        summary = summarize_elevation(samples)
        self.assertIsNone(summary.gain_m)
        self.assertIsNone(summary.loss_m)

    def test_pipeline_analyze_incorporates_metrics(self):
        samples = [
            {
                "elapsed_s": 0,
                "active": True,
                "power_w": 200,
                "hr_bpm": 150,
                "cadence_rpm": 95,
                "speed_mps": 10.0,
                "altitude_m": 100.0,
                "distance_m": 0.0,
            },
            {
                "elapsed_s": 1,
                "active": True,
                "power_w": 0,
                "hr_bpm": 140,
                "cadence_rpm": 90,
                "speed_mps": 12.0,
                "altitude_m": 105.0,
                "distance_m": 12.0,
            },
        ]
        result = analyze(samples, AthleteParameters(ftp_w=250))

        # Check general section
        self.assertEqual(result["general"]["distance_m"], 12.0)
        self.assertEqual(result["general"]["elevation_gain_m"], 5.0)
        self.assertEqual(result["general"]["elevation_loss_m"], 0.0)
        self.assertEqual(result["general"]["speed_avg_m_s"], 11.0)
        self.assertEqual(result["general"]["speed_max_m_s"], 12.0)

        # Check power, cadence, speed, elevation sections
        self.assertEqual(result["power"]["max_w"], 200.0)
        self.assertEqual(result["power"]["zero_power_seconds"], 1)
        self.assertEqual(result["power"]["coasting_pct"], 50.0)

        self.assertEqual(result["cadence"]["max_rpm"], 95.0)
        self.assertEqual(result["cadence"]["target_range_seconds"], 2)
        self.assertEqual(result["cadence"]["target_range_pct"], 100.0)

        self.assertEqual(result["speed"]["avg_m_s"], 11.0)
        self.assertEqual(result["speed"]["max_m_s"], 12.0)

        self.assertEqual(result["elevation"]["gain_m"], 5.0)
        self.assertEqual(result["elevation"]["loss_m"], 0.0)


if __name__ == "__main__":
    unittest.main()
