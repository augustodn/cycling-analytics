"""Synthetic vector tests for rolling power and sample filtering."""

import math

from cycling.analytics.rolling import (
    calculate_coverage,
    extract_runs,
    filter_active,
    rolling_windows,
)


def make_samples(values, start_s=0, active=True, segment=0, key="power_w"):
    return [
        {
            "elapsed_s": start_s + i,
            "active": active,
            "segment": segment,
            key: val,
        }
        for i, val in enumerate(values)
    ]


def test_filter_active_and_sorting():
    raw = [
        {"elapsed_s": 5, "active": True},
        {"elapsed_s": 2, "active": False},
        {"elapsed_s": 1, "active": True},
    ]
    filtered = filter_active(raw)
    assert [s["elapsed_s"] for s in filtered] == [1, 5]


def test_extract_runs_handles_gaps_and_nans():
    samples = (
        make_samples([100, 200, 300], start_s=0)
        + make_samples([None, math.nan], start_s=3)
        + make_samples([400, 500], start_s=5)
    )
    runs = list(extract_runs(samples, "power_w"))
    assert len(runs) == 2
    assert [s["power_w"] for s in runs[0]] == [100, 200, 300]
    assert [s["power_w"] for s in runs[1]] == [400, 500]


def test_rolling_windows_exact_vector():
    # 5-second window over [10, 20, 30, 40, 50, 60]
    samples = make_samples([10, 20, 30, 40, 50, 60])
    windows = list(rolling_windows(samples, "power_w", 5))
    assert len(windows) == 2
    end_s1, avg1 = windows[0]
    end_s2, avg2 = windows[1]
    assert end_s1["elapsed_s"] == 4
    assert avg1 == (10 + 20 + 30 + 40 + 50) / 5  # 30.0
    assert end_s2["elapsed_s"] == 5
    assert avg2 == (20 + 30 + 40 + 50 + 60) / 5  # 40.0


def test_rolling_windows_incomplete_window_omitted():
    samples = make_samples([100] * 4)
    windows = list(rolling_windows(samples, "power_w", 5))
    assert len(windows) == 0


def test_calculate_coverage():
    samples = make_samples([100, None, 200, math.nan, 300])
    cov = calculate_coverage(samples, "power_w", 5)
    assert cov.seconds == 3
    assert cov.fraction == 0.6
