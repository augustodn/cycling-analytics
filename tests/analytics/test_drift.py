"""Synthetic vector tests for aerobic drift / decoupling."""

from dataclasses import dataclass

import pytest

from cycling.analytics.drift import aerobic_drift


@dataclass
class DummyParams:
    ftp_w: float = 300.0


def make_drift_samples(powers, hr=140):
    return [
        {
            "elapsed_s": i,
            "active": True,
            "segment": 0,
            "power_w": p,
            "hr_bpm": hr,
        }
        for i, p in enumerate(powers)
    ]


def test_aerobic_drift_positive_decoupling():
    # 600s at 180W + 600s at 162W at constant 140 bpm HR (FTP=300, 50-80% FTP is 150-240W)
    powers = [180] * 600 + [162] * 600
    samples = make_drift_samples(powers, hr=140)
    res = aerobic_drift(samples, DummyParams(ftp_w=300))

    assert res["available"] is True
    assert res["duration_s"] == 1200
    # EF1 = 180/140 = 1.285714, EF2 = 162/140 = 1.157143 -> drift = 10.0%
    assert pytest.approx(res["drift_percent"], rel=1e-5) == 10.0
    assert res["confidence"] == "low"


def test_aerobic_drift_rejects_non_qualifying_short_segment():
    # 1199s valid + 1s invalid (>80% FTP = 250W)
    powers = [180] * 1199 + [250]
    samples = make_drift_samples(powers, hr=140)
    res = aerobic_drift(samples, DummyParams(ftp_w=300))

    assert res["available"] is False
    assert "no qualifying stable 20-minute segment" in res["reason"]


def test_aerobic_drift_requires_parameters():
    samples = make_drift_samples([180] * 1200)
    res = aerobic_drift(samples, None)
    assert res["available"] is False
