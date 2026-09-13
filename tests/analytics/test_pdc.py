"""Synthetic vector tests for Power Duration Curve (PDC/MMP) with timestamps."""

import pytest

from cycling.analytics.power import (
    power_curve,
    power_curve_detailed,
    threshold_estimate,
)


def make_power_samples(values):
    return [
        {"elapsed_s": i, "active": True, "segment": 0, "power_w": v}
        for i, v in enumerate(values)
    ]


def test_pdc_with_exact_timestamps():
    # 10s at 100W, 5s at 400W, 10s at 100W
    samples = make_power_samples([100] * 10 + [400] * 5 + [100] * 10)
    detailed = power_curve_detailed(samples, [5, 10])

    # 5s peak effort: 400W at elapsed_s 10..14
    pt5 = detailed[5]
    assert pt5.max_w == 400.0
    assert pt5.start_s == 10
    assert pt5.end_s == 14

    # 10s peak effort: (400*5 + 100*5)/10 = 250W
    pt10 = detailed[10]
    assert pt10.max_w == 250.0

    # API compatibility dict mapping
    curve_map = power_curve(samples, [5, 10])
    assert curve_map == {5: 400.0, 10: 250.0}


def test_pdc_rejects_invalid_durations():
    samples = make_power_samples([200] * 10)
    with pytest.raises(ValueError):
        power_curve(samples, [0])
    with pytest.raises(ValueError):
        power_curve(samples, [-5])


def test_threshold_estimate_synthetic():
    # 1200s (20 minutes) at 300W
    samples = make_power_samples([300.0] * 1200)
    est = threshold_estimate(samples)
    assert est["available"] is True
    assert est["watts"] == 0.95 * 300.0  # 285.0W
