"""Synthetic vector tests for Normalized Power (NP) and Intensity Factor (IF)."""

import pytest

from cycling.analytics.power import calculate_if, calculate_np


def make_power_samples(values):
    return [
        {"elapsed_s": i, "active": True, "segment": 0, "power_w": v}
        for i, v in enumerate(values)
    ]


def test_np_constant_power():
    samples = make_power_samples([250.0] * 30)
    np_val, count = calculate_np(samples)
    assert np_val == 250.0
    assert count == 1


def test_np_step_vector_exact_formula():
    samples = make_power_samples([100.0] * 30 + [300.0] * 30)
    np_val, count = calculate_np(samples)
    assert count == 31
    averages = [(100.0 * (30 - k) + 300.0 * k) / 30.0 for k in range(31)]
    expected = (sum(v**4 for v in averages) / len(averages)) ** 0.25
    assert pytest.approx(np_val, rel=1e-6) == expected


def test_np_under_30_seconds_returns_none():
    samples = make_power_samples([200.0] * 29)
    np_val, count = calculate_np(samples)
    assert np_val is None
    assert count == 0


def test_intensity_factor_calculation():
    assert calculate_if(250.0, 250.0) == 1.0
    assert calculate_if(275.0, 250.0) == 1.1
    assert calculate_if(None, 250.0) is None
    assert calculate_if(250.0, None) is None
    assert calculate_if(250.0, 0.0) is None
