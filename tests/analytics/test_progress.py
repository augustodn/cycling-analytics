"""Tests for longitudinal progress analytics functions."""

from datetime import datetime, timedelta

from cycling.analytics.progress import (
    calculate_fatigued_pdc_for_activity,
    calculate_fixed_hr_ef_trend_point,
    calculate_weekly_composition,
)


def make_sample(
    elapsed_s: int,
    power_w: float | None = 200.0,
    hr_bpm: float | None = 140.0,
    active: bool = True,
) -> dict:
    t = datetime(2026, 4, 1, 10, 0, 0) + timedelta(seconds=elapsed_s)
    return {
        "elapsed_s": elapsed_s,
        "timestamp": t.isoformat(),
        "active": active,
        "power_w": power_w,
        "hr_bpm": hr_bpm,
    }


def test_weekly_composition_grouping_and_caveats():
    act1 = {
        "id": "act1",
        "start_time": "2026-04-06T10:00:00Z",  # Mon of 2026-W15
        "duration_s": 3600,
        "modality": "road",
    }
    metrics1 = {
        "load": {"value": 50.0, "source": "power"},
        "zones": {
            "three_zone": {
                "basis": "power",
                "seconds": [1800, 1200, 600],
                "unknown_seconds": 0,
            }
        },
    }

    act2 = {
        "id": "act2",
        "start_time": "2026-04-08T10:00:00Z",  # Wed of 2026-W15
        "duration_s": 7200,
        "modality": "mtb",
    }
    metrics2 = {
        "load": {"value": None, "source": "unavailable"},
        "zones": {
            "three_zone": {
                "basis": "hr",
                "seconds": [3600, 3600, 0],
                "unknown_seconds": 10,
            }
        },
    }

    weekly = calculate_weekly_composition(
        [
            {"activity": act1, "metrics": metrics1},
            {"activity": act2, "metrics": metrics2},
        ]
    )

    assert len(weekly) == 1
    w = weekly[0]
    assert w["iso_week"] == "2026-W15"
    assert w["activity_count"] == 2
    assert w["total_seconds"] == 10800
    assert w["total_hours"] == 3.0
    assert w["total_load"] == 50.0
    assert w["load_sources"] == {"power": 1, "hr": 0, "rpe": 0, "unavailable": 1}
    assert w["three_zone_seconds"] == [5400, 4800, 600]
    assert w["longest_ride"]["activity_id"] == "act2"
    assert set(w["sensor_bases"]) == {"hr", "power"}
    assert len(w["caveats"]) >= 2  # mixed bases and unavailable load warnings


def test_fatigued_pdc_calculation():
    # 1000s activity @ 200W = 200 kJ total work
    samples = [make_sample(i, power_w=200.0) for i in range(1000)]
    res = calculate_fatigued_pdc_for_activity(
        samples, durations=[300, 600], fatigue_thresholds_kj=[100.0, 500.0]
    )

    # Total work = 200 kJ (since 200W * 1000s / 1000 = 200 kJ)
    assert abs(res["total_work_kj"] - 199.8) < 1.0
    watts = res["watts_after_thresholds"]

    # At 100 kJ threshold (starts around s=500), 300s window fits (500 to 800s) -> 200W
    assert watts["100.0"]["300"] is not None
    assert abs(watts["100.0"]["300"] - 200.0) < 1e-3

    # At 500.0 kJ threshold (exceeds total work of 200 kJ), returns None (no fabricated data)
    assert watts["500.0"]["300"] is None
    assert watts["500.0"]["600"] is None


def test_fixed_hr_ef_trend_point_missing_sensors():
    activity = {
        "id": "nopower",
        "start_time": "2026-04-10T10:00:00Z",
        "modality": "indoor",
        "duration_s": 1800,
    }
    # Samples with HR but no power
    samples = [make_sample(i, power_w=None, hr_bpm=135.0) for i in range(1800)]
    point = calculate_fixed_hr_ef_trend_point(activity, samples)

    assert point["activity_id"] == "nopower"
    assert point["modality"] == "indoor"
    assert point["is_indoor"] is True
    assert point["valid_power_samples"] == 0
    assert point["valid_hr_samples"] == 1800
    assert point["avg_power_w"] is None
    assert point["ef"] is None
    assert point["available"] is False
    assert "missing_power" in point["quality_flags"]
    assert point["unavailable_reason"] is not None
    # No target power fabricated when power stream is missing
    assert point["fixed_hr_targets"]["130"] is None
    assert point["fixed_hr_targets"]["140"] is None


def test_fixed_hr_ef_trend_point_deterministic_window():
    activity = {
        "id": "fixedhr_act",
        "start_time": "2026-04-10T10:00:00Z",
        "modality": "road",
        "duration_s": 1200,
    }
    # 600s @ 200W/130bpm, then 600s @ 250W/140bpm
    samples = [make_sample(i, power_w=200.0, hr_bpm=130.0) for i in range(600)] + [
        make_sample(i, power_w=250.0, hr_bpm=140.0) for i in range(600, 1200)
    ]
    point = calculate_fixed_hr_ef_trend_point(activity, samples)

    assert point["available"] is True
    assert point["fixed_hr_targets"]["130"]["power_w"] == 200.0
    assert point["fixed_hr_targets"]["130"]["hr_bpm"] == 130.0
    assert point["fixed_hr_targets"]["140"]["power_w"] == 250.0
    assert point["fixed_hr_targets"]["140"]["hr_bpm"] == 140.0
    # HR target 150 is out of range (+/- 5bpm) -> None
    point_150 = calculate_fixed_hr_ef_trend_point(
        activity, samples, hr_targets_bpm=[150]
    )
    assert point_150["fixed_hr_targets"]["150"] is None
