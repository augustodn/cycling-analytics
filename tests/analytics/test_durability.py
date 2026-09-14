"""Synthetic vector tests for Durability analysis across cumulative work buckets."""

from cycling.analytics.durability import calculate_aerobic_durability, durability


def make_power_rows(power, start_s=0, active=True):
    return [
        {
            "elapsed_s": start_s + i,
            "active": active,
            "segment": 0,
            "power_w": p,
        }
        for i, p in enumerate(power)
    ]


def test_durability_baseline_and_decay():
    # 20 samples of 100W -> total work = 2000 J = 2.0 kJ
    samples = make_power_rows([100] * 20)
    res = durability(
        samples,
        durations=[5],
        thresholds_kj=[0.5],
        historical_fresh_references={5: {"power_w": 100.0}},
    )

    assert res["available"] is True
    assert res["total_work_kj"] == 2.0
    pt = res["points"][0]
    assert pt["power_w"] == 100.0
    assert pt["retention_pct"] == 100.0


def test_durability_new_requirements():
    # 1. Fresh 20m (1200s) @ 300W, then 1500 kJ prior work + 20m @ 270W -> 90%
    # First 1200s @ 300W -> 360 kJ work
    s1 = make_power_rows([300] * 1200, start_s=0)
    # Middle work filler: 1140s @ 1000W -> 1140 kJ (total work before second block = 1500 kJ)
    s2 = make_power_rows([1000] * 1140, start_s=1200)
    # Second block: 1200s @ 270W starting at exactly 1500 kJ
    s3 = make_power_rows([270] * 1200, start_s=2340)
    samples = s1 + s2 + s3

    # Provide historical fresh reference of 300W for 1200s
    hist_ref = {
        1200: {
            "power_w": 300.0,
            "activity_id": "hist1",
            "date": "2026-01-01",
            "start_work_kj": 0.0,
            "source": "historical_90d",
        }
    }
    res = durability(
        samples,
        durations=[1200],
        thresholds_kj=[1500.0],
        historical_fresh_references=hist_ref,
    )
    assert res["available"] is True
    pt = next(
        p
        for p in res["points"]
        if p["threshold_kj"] == 1500.0 and p["duration_s"] == 1200
    )
    assert pt["power_w"] == 270.0
    assert abs(pt["retention_pct"] - 90.0) < 1e-5

    # 2. No 20m remaining after 1800 kJ -> null
    res2 = durability(
        samples,
        durations=[1200],
        thresholds_kj=[1800.0],
        historical_fresh_references=hist_ref,
    )
    pt1800 = next(
        p
        for p in res2["points"]
        if p["threshold_kj"] == 1800.0 and p["duration_s"] == 1200
    )
    assert pt1800["power_w"] is None
    assert pt1800["retention_pct"] is None

    # 3. Window starts before threshold and ends after -> ineligible
    # Effort starts at 1400 kJ and ends at 1600 kJ (span 200 kJ across threshold 1500 kJ)
    # Build 1400s @ 1000W (1400 kJ), then 200s @ 500W (100 kJ). Total activity = 1600s.
    s_early = make_power_rows([1000] * 1400, start_s=0)
    s_effort = make_power_rows([500] * 200, start_s=1400)
    res3 = durability(
        s_early + s_effort,
        durations=[200],
        thresholds_kj=[1500.0],
        historical_fresh_references={200: {"power_w": 500.0}},
    )
    pt_cross = next(
        p
        for p in res3["points"]
        if p["threshold_kj"] == 1500.0 and p["duration_s"] == 200
    )
    assert pt_cross["power_w"] is None

    # 4. Irregular timestamps use elapsed time instead of assuming 1 Hz.
    irregular = [
        {"timestamp": t, "active": True, "segment": 0, "power_w": p}
        for t, p in ((0, 100), (2, 200), (5, 300), (7, 400))
    ]
    res4 = durability(
        irregular,
        durations=[2],
        thresholds_kj=[0.8],
        historical_fresh_references={
            2: {"power_w": 300.0, "activity_id": "hist", "source": "historical_90d"}
        },
    )
    assert res4["total_work_kj"] == 1.4
    point4 = res4["points"][0]
    assert point4["power_w"] == 300.0
    assert point4["start_offset_s"] == 5.0
    assert point4["available_exposure_seconds"] == 2.0


def test_durability_does_not_use_same_activity_easy_start_as_historical_baseline():
    samples = make_power_rows([100] * 10 + [300] * 10)
    without_history = durability(samples, durations=[5], thresholds_kj=[0.5])
    assert without_history["points"][0]["retention_pct"] is None

    result = durability(
        samples,
        durations=[5],
        thresholds_kj=[0.5],
        historical_fresh_references={
            5: {
                "power_w": 300.0,
                "activity_id": "historical",
                "date": "2026-01-01",
                "start_work_kj": 0.0,
                "source": "historical_90d",
            }
        },
    )
    assert result["fresh_reference"]["5"]["activity_id"] == "historical"
    assert result["points"][0]["retention_pct"] == 100.0


def test_durability_no_power_activity_explicitly_unavailable():
    # Activity without power stream (e.g. power_w is None or absent)
    samples = [
        {"elapsed_s": i, "active": True, "segment": 0, "power_w": None}
        for i in range(10)
    ]
    res = durability(samples, durations=[5], bucket_kj=[0, 100])
    assert res["available"] is False
    assert "power coverage" in res["reason"] or "missing power" in res["reason"]
    assert res["buckets"] == []


def test_durability_unexplained_gap():
    # Gap in elapsed_s without inactive pause row
    samples = make_power_rows([100] * 5, start_s=0) + make_power_rows(
        [100] * 5, start_s=10
    )
    res = durability(samples, durations=[5], bucket_kj=[0])
    assert res["available"] is True
    assert "gaps" in res["reason"]


def test_durability_allows_explicit_inactive_pause():
    # Explicit inactive pause rows bridging the gap
    samples = (
        make_power_rows([100] * 5, start_s=0, active=True)
        + make_power_rows([None] * 2, start_s=5, active=False)
        + make_power_rows([100] * 5, start_s=7, active=True)
    )
    res = durability(samples, durations=[5], bucket_kj=[0])
    assert res["available"] is True
    assert res["exposure_seconds"] == 10


def make_power_hr_rows(power, hr):
    return [
        {
            "elapsed_s": index,
            "active": True,
            "segment": 0,
            "power_w": power[index] if isinstance(power, list) else power,
            "hr_bpm": hr[index] if isinstance(hr, list) else hr,
        }
        for index in range(len(power) if isinstance(power, list) else len(hr))
    ]


def test_aerobic_durability_constant_power_and_hr_retains_efficiency():
    rows = make_power_hr_rows([200] * 8000, [125] * 8000)
    result = calculate_aerobic_durability(rows)

    assert result["available"] is True
    assert result["baseline"]["efficiency_factor"] == 1.6
    point = result["points"][0]
    assert point["threshold_kj"] == 1000.0
    assert point["retention_pct"] == 100.0
    assert point["efficiency_loss_pct"] == 0.0


def test_aerobic_durability_marks_efficiency_loss_when_hr_rises():
    hr = [125] * 5000 + [140] * 3000
    result = calculate_aerobic_durability(make_power_hr_rows([200] * 8000, hr))

    point = result["points"][0]
    assert point["avg_power"] == 200.0
    assert point["avg_hr"] == 140.0
    assert point["retention_pct"] < 100.0
    assert point["efficiency_loss_pct"] > 0.0


def test_aerobic_durability_requires_power_and_hr():
    no_hr = calculate_aerobic_durability(
        make_power_hr_rows([200] * 8000, [None] * 8000)
    )
    no_power = calculate_aerobic_durability(
        make_power_hr_rows([None] * 8000, [125] * 8000)
    )

    assert no_hr["available"] is False
    assert no_power["available"] is False
    assert "requires sufficient power" in no_hr["reason"]


def test_aerobic_durability_returns_null_when_threshold_has_no_window():
    result = calculate_aerobic_durability(
        make_power_hr_rows([200] * 8000, [125] * 8000)
    )

    late_point = next(
        point for point in result["points"] if point["threshold_kj"] == 1500.0
    )
    assert late_point["efficiency_factor"] is None
    assert late_point["retention_pct"] is None


def test_aerobic_durability_excludes_initial_warmup_from_baseline():
    rows = make_power_hr_rows(
        [100] * 600 + [200] * 8000,
        [100] * 600 + [125] * 8000,
    )
    result = calculate_aerobic_durability(rows)

    assert result["baseline"]["window_start"] >= 600
    assert result["baseline"]["avg_power"] == 200.0
