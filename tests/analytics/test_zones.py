"""Synthetic vector tests for Zone time distribution and Polarized 3-Zone model."""

from dataclasses import dataclass

from cycling.analytics.zones import (
    calculate_hr_zone_distribution,
    calculate_zone_seconds,
)


@dataclass
class DummyParams:
    ftp_w: float = 200.0
    lthr_bpm: float = 160.0
    power_three_zone_fractions: tuple = (0.75, 1.0)
    hr_three_zone_fractions: tuple = (0.82, 1.0)
    power_zone_fractions: tuple = (0.55, 0.75, 0.90, 1.05, 1.20)
    hr_zone_bounds: tuple = (130, 145, 158, 170)


def test_polarized_3zone_power_basis():
    # FTP = 200W -> Z1: <150W, Z2: 150-200W, Z3: >=200W
    samples = [
        {"elapsed_s": i, "active": True, "power_w": w, "hr_bpm": 130}
        for i, w in enumerate([100, 140, 160, 180, 210, 250])
    ]
    res = calculate_zone_seconds(samples, DummyParams())
    tz = res.three_zone
    assert tz["basis"] == "power"
    # Z1: 100, 140 -> 2s; Z2: 160, 180 -> 2s; Z3: 210, 250 -> 2s
    assert tz["seconds"] == [2, 2, 2]
    assert tz["percentages"] == [
        33.333333333333336,
        33.333333333333336,
        33.333333333333336,
    ]
    assert tz["unknown_seconds"] == 0


def test_polarized_3zone_hr_fallback():
    # LTHR = 160 -> Z1: <131.2, Z2: 131.2-160, Z3: >=160
    samples = [
        {"elapsed_s": i, "active": True, "power_w": None, "hr_bpm": h}
        for i, h in enumerate([120, 140, 170])
    ]
    res = calculate_zone_seconds(samples, DummyParams())
    tz = res.three_zone
    assert tz["basis"] == "hr"
    assert tz["seconds"] == [1, 1, 1]


def test_zones_without_parameters():
    samples = [{"elapsed_s": 0, "active": True, "power_w": 200}]
    res = calculate_zone_seconds(samples, None)
    assert res.three_zone["basis"] == "unavailable"
    assert res.three_zone["unknown_seconds"] == 1


def test_hr_distribution_assigns_zone_boundaries():
    samples = [
        {"elapsed_s": index, "active": True, "hr_bpm": hr}
        for index, hr in enumerate(
            [
                126,
                127,
                138,
                139,
                140,
                144,
                145,
                146,
                153,
                154,
                155,
                158,
                159,
                160,
                164,
                165,
                166,
            ]
        )
    ]

    result = calculate_hr_zone_distribution(samples)

    assert result["seconds"] == [1, 3, 3, 3, 3, 3, 1]
    assert result["unknown_seconds"] == 0
    assert [zone["label"] for zone in result["zones"]] == [
        "1 Recovery",
        "2 Aerobic",
        "3 Tempo",
        "4 SubThreshold",
        "5a Threshold",
        "5b Aerobic Capacity",
        "5c Anaerobic",
    ]
