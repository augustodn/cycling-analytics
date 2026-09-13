"""Synthetic vector tests for multi-tier Training Load hierarchy."""

from dataclasses import dataclass

import pytest

from cycling.analytics.load import calculate_session_load


@dataclass
class DummyParams:
    ftp_w: float = 200.0
    lthr_bpm: float = 160.0


def make_samples(power=200, hr=140, count=3600):
    return [
        {
            "elapsed_s": i,
            "active": True,
            "segment": 0,
            "power_w": power,
            "hr_bpm": hr,
        }
        for i in range(count)
    ]


def test_training_load_power_priority():
    # 1 hour at 200W (NP=200W, FTP=200W) -> IF=1.0 -> Load = 1 * (1.0)^2 * 100 = 100
    samples = make_samples(power=200, hr=140, count=3600)
    res = calculate_session_load(samples, DummyParams(), rpe=8)
    assert res["source"] == "power"
    assert pytest.approx(res["value"], rel=1e-5) == 100.0


def test_training_load_hr_fallback():
    # Power is None (no power), HR is present
    samples = make_samples(power=None, hr=160, count=3600)
    res = calculate_session_load(samples, DummyParams(), rpe=5)
    assert res["source"] == "hr"
    # HR=160, LTHR=160 -> sum((160/160)^2 * 3600)/3600 * 100 = 100
    assert pytest.approx(res["value"], rel=1e-5) == 100.0


def test_training_load_rpe_fallback():
    # Both power and HR are None
    samples = make_samples(power=None, hr=None, count=1800)  # 30 min
    res = calculate_session_load(samples, DummyParams(), rpe=6)
    assert res["source"] == "rpe"
    # 30 min * 6 RPE = 180
    assert res["value"] == 180.0


def test_training_load_unavailable():
    samples = make_samples(power=None, hr=None, count=1800)
    res = calculate_session_load(samples, None, rpe=None)
    assert res["source"] == "unavailable"
    assert res["value"] is None


def test_training_load_hr_partial_coverage_duration_scaling():
    # 3600 active seconds total, 90% valid HR (3240 samples with HR=160, 360 with HR=None)
    samples = make_samples(power=None, hr=160, count=3240)
    samples.extend(make_samples(power=None, hr=None, count=360))
    for i, s in enumerate(samples):
        s["elapsed_s"] = i
    res = calculate_session_load(samples, DummyParams(lthr_bpm=160.0), rpe=None)
    assert res["source"] == "hr"
    # Duration scaling uses total active_seconds (3600): 3600/3600 * 1.0 * 100 = 100.0
    assert pytest.approx(res["value"], rel=1e-5) == 100.0


def test_training_load_non_finite_parameters():
    samples = make_samples(power=200, hr=160, count=3600)
    params = DummyParams(ftp_w=float("nan"), lthr_bpm=float("inf"))
    res = calculate_session_load(samples, params, rpe=float("nan"))
    assert res["source"] == "unavailable"
    assert res["value"] is None
