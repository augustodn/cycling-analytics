"""Synthetic one-second checks for the pure Power↔HR analytics core."""

from copy import deepcopy
from dataclasses import replace
from json import dumps
from math import inf, nan

import pytest

from cycling.analytics.power_hr import (
    PowerHRConfig,
    aggregate_power_hr,
    aggregate_power_hr_bins,
    align_power_hr_samples,
    analyze_power_hr,
    hr_at_target_power,
)

FAST = PowerHRConfig(lag_s=15, power_window_s=1, hr_window_s=1)


def rows(seconds, power=200, hr=140, cadence=90):
    return [
        {
            "elapsed_s": second,
            "segment": 0,
            "active": True,
            "power_w": power,
            "hr_bpm": hr,
            "cadence_rpm": cadence,
        }
        for second in range(seconds)
    ]


def group(result, group_id="all"):
    return next(item for item in result["groups"] if item["group_id"] == group_id)


def test_positive_lag_pairs_power_with_later_hr_and_does_not_mutate_input():
    samples = rows(90)
    for sample in samples:
        t = sample["elapsed_s"]
        sample["power_w"] = 100 + t
        sample["hr_bpm"] = 100 + t - FAST.lag_s
    original = deepcopy(samples)
    pairs = align_power_hr_samples(list(reversed(samples)), config=FAST)
    assert samples == original
    assert len(pairs) == 75
    assert pairs[0]["elapsed_s"] == 0
    assert pairs[-1]["hr_elapsed_s"] == 89
    assert all(pair["power_w"] == pair["hr_bpm"] for pair in pairs)


def test_complete_trailing_means_and_unequal_window_warmup():
    samples = rows(60)
    for sample in samples:
        sample["power_w"] = 100 + sample["elapsed_s"]
        sample["hr_bpm"] = 80 + sample["elapsed_s"]
    config = replace(FAST, power_window_s=5, hr_window_s=20)
    pairs = align_power_hr_samples(samples, config=config)
    assert len(pairs) == 41
    assert pairs[0]["elapsed_s"] == 4
    assert pairs[0]["hr_elapsed_s"] == 19
    assert pairs[0]["power_w"] == 102
    assert pairs[0]["hr_bpm"] == 89.5
    assert align_power_hr_samples(rows(19), config=config) == []


@pytest.mark.parametrize("boundary", ["inactive", "gap", "segment", "power", "hr"])
def test_neither_smoothing_nor_lag_crosses_boundaries(boundary):
    samples = rows(100)
    after = 41
    if boundary == "inactive":
        samples[40]["active"] = False
    elif boundary == "gap":
        del samples[40]
    elif boundary == "segment":
        after = 40
        for sample in samples[40:]:
            sample["segment"] = 1
    else:
        samples[40]["power_w" if boundary == "power" else "hr_bpm"] = None
    pairs = align_power_hr_samples(
        samples, config=replace(FAST, power_window_s=3, hr_window_s=3)
    )
    assert len(pairs) == 23 + (100 - after - 2 - 15)
    assert all(
        pair["hr_elapsed_s"] < 40 or pair["elapsed_s"] >= after + 2 for pair in pairs
    )


@pytest.mark.parametrize("field", ["power_w", "hr_bpm"])
@pytest.mark.parametrize("invalid", [None, nan, inf, -1, True, "200"])
def test_invalid_sensors_break_paired_runs(field, invalid):
    samples = rows(60)
    samples[25][field] = invalid
    pairs = align_power_hr_samples(samples, config=FAST)
    assert len(pairs) == 10 + 19
    assert all(pair["hr_elapsed_s"] < 25 or pair["elapsed_s"] > 25 for pair in pairs)


def test_all_mode_allows_zero_power_and_missing_cadence_but_rejects_zero_hr():
    pairs = align_power_hr_samples(rows(30, power=0, cadence=None), config=FAST)
    assert len(pairs) == 15
    assert all(pair["power_w"] == 0 for pair in pairs)
    assert align_power_hr_samples(rows(30, hr=0), config=FAST) == []


@pytest.mark.parametrize(
    "changes",
    [
        {"lag_s": 14},
        {"lag_s": 61},
        {"lag_s": 15.0},
        {"lag_s": True},
        {"power_window_s": 0},
        {"hr_window_s": -1},
        {"stable_window_s": 1.5},
        {"bin_size_w": 0},
        {"bin_size_w": inf},
        {"mode": "unknown"},
        {"max_power_cv": nan},
        {"min_power_ftp_fraction": -0.1},
        {"min_cadence_rpm": -1},
        {"aerobic_floor_ftp_fraction": 0.8, "aerobic_ceiling_ftp_fraction": 0.7},
        {"mode": "stable", "aerobic_ceiling_ftp_fraction": 0.39},
    ],
)
def test_invalid_configuration_is_rejected(changes):
    with pytest.raises(ValueError):
        PowerHRConfig(**changes)


@pytest.mark.parametrize("lag", [15, 60])
def test_inclusive_lag_limits(lag):
    assert (
        len(align_power_hr_samples(rows(90), config=replace(FAST, lag_s=lag)))
        == 90 - lag
    )


@pytest.mark.parametrize(
    "bad_rows",
    [
        [*rows(2), rows(1)[0]],
        [{**rows(1)[0], "elapsed_s": 0.5}],
        [{**rows(1)[0], "elapsed_s": -1}],
        [{**rows(1)[0], "segment": None}],
        [{**rows(1)[0], "active": 1}],
    ],
)
def test_bad_sample_structure_is_rejected(bad_rows):
    with pytest.raises(ValueError):
        align_power_hr_samples(bad_rows, config=FAST)


def test_power_bins_are_half_open_and_quartiles_use_inclusive_interpolation():
    samples = rows(21)
    for sample, power in zip(
        samples[:6], [199.999, 200, 200, 209.999, 210, 210], strict=True
    ):
        sample["power_w"] = power
    for sample, hr in zip(samples[15:], [100, 100, 100, 160, 170, 190], strict=True):
        sample["hr_bpm"] = hr
    points = group(analyze_power_hr(samples, config=FAST))["bins"]
    assert [(point["power_low_w"], point["valid_seconds"]) for point in points] == [
        (190, 1),
        (200, 3),
        (210, 2),
    ]
    assert points[0]["hr_p25_bpm"] == points[0]["hr_p75_bpm"] == 100
    assert points[1]["power_w"] == 205
    assert points[1]["hr_median_bpm"] == 100
    assert points[1]["hr_mean_bpm"] == 120
    assert points[1]["hr_p25_bpm"] == 100
    assert points[1]["hr_p75_bpm"] == 130
    wider = analyze_power_hr(samples, config=replace(FAST, bin_size_w=20))
    assert [point["valid_seconds"] for point in group(wider)["bins"]] == [1, 5]


def test_stable_mode_requires_full_180_seconds_and_valid_effective_ftp():
    config = replace(FAST, mode="stable")
    assert align_power_hr_samples(rows(194), config=config, effective_ftp_w=400) == []
    pairs = align_power_hr_samples(
        rows(195, cadence=70), config=config, effective_ftp_w=400
    )
    assert len(pairs) == 1
    assert pairs[0]["elapsed_s"] == 179
    for ftp in (None, 0, -1, inf, nan):
        with pytest.raises(ValueError):
            align_power_hr_samples(rows(195), config=config, effective_ftp_w=ftp)


def test_stable_defaults_use_over_40_percent_ftp_and_over_50_rpm():
    config = replace(FAST, mode="stable")
    eligible = align_power_hr_samples(
        rows(195, power=120, cadence=51), config=config, effective_ftp_w=300
    )
    assert len(eligible) == 1
    assert (
        align_power_hr_samples(
            rows(195, power=119, cadence=51), config=config, effective_ftp_w=300
        )
        == []
    )
    assert (
        align_power_hr_samples(
            rows(195, power=120, cadence=50), config=config, effective_ftp_w=300
        )
        == []
    )


def test_stability_cv_uses_raw_power_not_smoothed_power_and_threshold_is_inclusive():
    samples = rows(360)
    for sample in samples:
        sample["power_w"] = 180 if sample["elapsed_s"] % 2 else 220
    config = replace(FAST, mode="stable", power_window_s=2)
    assert align_power_hr_samples(samples, config=config, effective_ftp_w=300) == []
    pairs = align_power_hr_samples(
        samples, config=replace(config, max_power_cv=0.10), effective_ftp_w=300
    )
    assert len(pairs) == 166
    assert all(pair["power_w"] == 200 for pair in pairs)


def test_stable_mode_checks_cv_at_later_hr_timestamp_too():
    samples = rows(210)
    samples[190]["power_w"] = 500
    pairs = align_power_hr_samples(
        samples, config=replace(FAST, mode="stable"), effective_ftp_w=300
    )
    assert 179 not in [pair["elapsed_s"] for pair in pairs]


@pytest.mark.parametrize(
    "changes", [{"power_w": 149}, {"cadence_rpm": 69}, {"cadence_rpm": None}]
)
def test_stable_eligibility_break_restarts_full_window(changes):
    samples = rows(420)
    samples[200].update(changes)
    config = replace(
        FAST, mode="stable", min_power_ftp_fraction=0.5, min_cadence_rpm=70
    )
    pairs = align_power_hr_samples(samples, config=config, effective_ftp_w=300)
    assert len(pairs) == 6 + 25
    assert all(pair["hr_elapsed_s"] < 200 or pair["elapsed_s"] >= 380 for pair in pairs)


def test_optional_aerobic_bounds_and_minimum_ftp_fraction():
    stable = replace(FAST, mode="stable", min_power_ftp_fraction=0.5)
    assert (
        len(
            align_power_hr_samples(
                rows(195, power=150), config=stable, effective_ftp_w=300
            )
        )
        == 1
    )
    assert (
        align_power_hr_samples(rows(195, power=149), config=stable, effective_ftp_w=300)
        == []
    )
    assert (
        align_power_hr_samples(
            rows(195),
            config=replace(stable, aerobic_floor_ftp_fraction=0.7),
            effective_ftp_w=300,
        )
        == []
    )
    assert (
        align_power_hr_samples(
            rows(195),
            config=replace(stable, aerobic_ceiling_ftp_fraction=0.6),
            effective_ftp_w=300,
        )
        == []
    )
    aerobic = replace(
        FAST, aerobic_floor_ftp_fraction=0.5, aerobic_ceiling_ftp_fraction=0.8
    )
    assert (
        len(
            align_power_hr_samples(
                rows(30, power=240), config=aerobic, effective_ftp_w=300
            )
        )
        == 15
    )
    with pytest.raises(ValueError):
        align_power_hr_samples(rows(30), config=aerobic)


def test_work_band_boundaries_and_elapsed_thirds_have_independent_bins():
    result = analyze_power_hr(
        rows(60, power=100),
        config=FAST,
        fatigue_thresholds_kj=(2, 10),
        elapsed_splits=3,
    )
    assert result["valid_seconds"] == 45
    assert result["observed_work_kj"] == 6
    assert result["work_complete"] is True
    assert [
        group(result, key)["valid_seconds"] for key in ("work_1", "work_2", "work_3")
    ] == [20, 25, 0]
    assert [group(result, f"elapsed_{i}_of_3")["valid_seconds"] for i in (1, 2, 3)] == [
        20,
        20,
        5,
    ]
    assert group(result, "work_1")["bins"][0]["valid_seconds"] == 20
    assert group(result, "work_2")["bins"][0]["valid_seconds"] == 25
    assert group(result, "work_3")["bins"] == []


def test_elapsed_halves_use_wall_clock_not_valid_pair_count_and_allow_explicit_duration():
    samples = rows(60)
    for sample in samples[20:30]:
        sample["active"] = False
    result = analyze_power_hr(samples, config=FAST, elapsed_splits=2)
    assert [group(result, f"elapsed_{i}_of_2")["valid_seconds"] for i in (1, 2)] == [
        5,
        15,
    ]
    longer = analyze_power_hr(
        rows(60), config=FAST, elapsed_splits=3, activity_duration_s=90
    )
    assert [group(longer, f"elapsed_{i}_of_3")["valid_seconds"] for i in (1, 2, 3)] == [
        30,
        15,
        0,
    ]
    assert longer["work_complete"] is False


def test_fatigue_work_includes_earlier_power_rejected_by_stable_filters():
    samples = rows(400, power=100)
    for sample in samples[200:]:
        sample["power_w"] = 200
    result = analyze_power_hr(
        samples,
        config=replace(FAST, mode="stable", min_power_ftp_fraction=0.5),
        effective_ftp_w=300,
        fatigue_thresholds_kj=(20,),
    )
    assert result["observed_work_kj"] == 60
    assert result["valid_seconds"] == 6
    assert group(result, "work_1")["valid_seconds"] == 0
    assert group(result, "work_2")["valid_seconds"] == 6


@pytest.mark.parametrize("unknown", ["gap", "missing_power"])
def test_unknown_work_never_fabricates_later_fatigue_labels(unknown):
    samples = rows(60, power=100)
    if unknown == "gap":
        del samples[20]
    else:
        samples[20]["power_w"] = None
    result = analyze_power_hr(samples, config=FAST, fatigue_thresholds_kj=(2,))
    assert result["observed_work_kj"] == pytest.approx(5.9)
    assert result["work_complete"] is False
    assert result["valid_seconds"] == 29
    assert result["fatigue_unclassified_seconds"] == 24
    assert group(result, "work_1")["valid_seconds"] == 5
    assert group(result, "work_2")["valid_seconds"] == 0


def test_explicit_pause_and_segment_change_preserve_known_work_but_hr_loss_does_not_erase_it():
    samples = rows(60, power=100)
    samples[20].update(active=False, power_w=None)
    for sample in samples[21:]:
        sample["segment"] = 1
    result = analyze_power_hr(samples, config=FAST, fatigue_thresholds_kj=(2,))
    assert result["work_complete"] is True
    assert result["observed_work_kj"] == pytest.approx(5.9)
    assert group(result, "work_2")["valid_seconds"] == 24
    samples[20].update(active=True, power_w=100, hr_bpm=None)
    assert analyze_power_hr(samples, config=FAST)["observed_work_kj"] == 6
    assert analyze_power_hr(samples, config=FAST)["work_complete"] is True
    assert analyze_power_hr(samples[1:], config=FAST)["work_complete"] is False


@pytest.mark.parametrize(
    "kwargs",
    [
        {"fatigue_thresholds_kj": (2, 1)},
        {"fatigue_thresholds_kj": (1, 1)},
        {"fatigue_thresholds_kj": (0,)},
        {"fatigue_thresholds_kj": (inf,)},
        {"elapsed_splits": 4},
        {"elapsed_splits": 2.0},
        {"activity_duration_s": 10},
    ],
)
def test_invalid_group_settings_are_rejected(kwargs):
    with pytest.raises(ValueError):
        analyze_power_hr(rows(30), config=FAST, **kwargs)


def test_aggregation_weights_activity_bin_medians_equally_and_reports_per_bin_confidence():
    activities = {
        "long": rows(100, hr=100),
        "short": rows(20, hr=180),
        "other_bin": rows(30, power=300),
    }
    result = aggregate_power_hr(
        activities, config=FAST, min_activities=2, min_total_seconds=90
    )
    point, other = group(result)["bins"]
    assert result["activity_count"] == group(result)["activity_count"] == 3
    assert result["valid_seconds"] == group(result)["valid_seconds"] == 105
    dumps(result, allow_nan=False)
    assert point["hr_median_bpm"] == point["hr_mean_bpm"] == 140
    assert point["hr_p25_bpm"] == 120
    assert point["hr_p75_bpm"] == 160
    assert point["activity_count"] == 2
    assert point["valid_seconds"] == 90
    assert point["status"] == "ok"
    assert other["activity_count"] == 1
    assert other["status"] == "low_confidence"
    assert other["confidence_reasons"] == [
        "insufficient_activities",
        "insufficient_exposure",
    ]
    assert result["activities"]["long"]["valid_seconds"] == 85
    sparse = aggregate_power_hr_bins(
        result["activities"], min_activities=3, min_total_seconds=91
    )
    assert group(sparse)["bins"][0]["confidence_reasons"] == [
        "insufficient_activities",
        "insufficient_exposure",
    ]


def test_aggregation_keeps_relative_elapsed_groups_independent_per_activity():
    activities = {"long": rows(120), "short": rows(60)}
    for activity_id, samples in activities.items():
        first, second = (100, 180) if activity_id == "long" else (120, 160)
        for sample in samples:
            sample["hr_bpm"] = (
                first if sample["elapsed_s"] < len(samples) // 2 + 15 else second
            )
    result = aggregate_power_hr(
        activities, config=FAST, elapsed_splits=2, min_activities=2, min_total_seconds=0
    )
    first = group(result, "elapsed_1_of_2")["bins"][0]
    second = group(result, "elapsed_2_of_2")["bins"][0]
    assert first["hr_median_bpm"] == 110
    assert second["hr_median_bpm"] == 170
    assert first["valid_seconds"] == 90
    assert second["valid_seconds"] == 60
    assert first["activity_count"] == second["activity_count"] == 2


def test_aggregation_resolves_each_activity_ftp_without_leaking_into_other_activities():
    result = aggregate_power_hr(
        {"eligible": rows(195), "below_floor": rows(195)},
        config=replace(FAST, mode="stable", min_power_ftp_fraction=0.5),
        effective_ftps_w={"eligible": 300, "below_floor": 500},
    )
    point = group(result)["bins"][0]
    assert point["activity_count"] == 1
    assert point["valid_seconds"] == 1
    assert result["activities"]["below_floor"]["valid_seconds"] == 0


def test_aggregation_rejects_incompatible_analysis_settings():
    first = analyze_power_hr(rows(60), config=FAST)
    for second in (
        analyze_power_hr(rows(60), config=replace(FAST, lag_s=30)),
        analyze_power_hr(rows(60), config=FAST, fatigue_thresholds_kj=(2,)),
    ):
        with pytest.raises(ValueError):
            aggregate_power_hr_bins({"first": first, "second": second})
    with pytest.raises(ValueError):
        aggregate_power_hr_bins({}, min_activities=0)
    with pytest.raises(ValueError):
        aggregate_power_hr_bins({}, min_total_seconds=-1)


def test_target_power_uses_exact_tolerance_and_never_interpolates_or_extrapolates():
    pairs = [
        {"power_w": power, "hr_bpm": hr}
        for power, hr in (
            (194.9, 90),
            (195, 100),
            (200, 130),
            (205, 160),
            (205.1, 180),
            (nan, 100),
            (200, 0),
        )
    ]
    result = hr_at_target_power(pairs, 200, min_seconds=3)
    assert result["valid_seconds"] == 3
    assert result["hr_median_bpm"] == 130
    assert result["matched_power_median_w"] == 200
    assert result["efficiency_w_per_bpm"] == pytest.approx(200 / 130)
    assert result["status"] == "ok"
    assert hr_at_target_power(pairs, 200, min_seconds=4)["status"] == "low_confidence"
    assert hr_at_target_power(pairs, 200, tolerance_w=0)["valid_seconds"] == 1
    absent = hr_at_target_power(pairs, 300)
    assert absent["status"] == "unavailable"
    assert absent["valid_seconds"] == 0
    assert absent["hr_median_bpm"] is None
    between_bins = [{"power_w": 190, "hr_bpm": 100}, {"power_w": 210, "hr_bpm": 160}]
    assert hr_at_target_power(between_bins, 200)["valid_seconds"] == 0


def test_target_helper_accepts_core_pairs_and_validates_arguments():
    pairs = align_power_hr_samples(rows(100), config=FAST)
    assert hr_at_target_power(pairs, 200)["valid_seconds"] == 85
    for kwargs in (
        {"target_power_w": nan},
        {"target_power_w": -1},
        {"target_power_w": 200, "tolerance_w": -1},
        {"target_power_w": 200, "min_seconds": 1.5},
    ):
        with pytest.raises(ValueError):
            hr_at_target_power(pairs, **kwargs)


def test_empty_short_and_sensorless_inputs_have_no_fabricated_bins():
    for samples in ([], rows(15), rows(60, power=None), rows(60, hr=None)):
        result = analyze_power_hr(
            samples, config=FAST, fatigue_thresholds_kj=(2,), elapsed_splits=3
        )
        assert result["valid_seconds"] == 0
        assert all(item["bins"] == [] for item in result["groups"])
    assert aggregate_power_hr({})["groups"] == []
    empty = aggregate_power_hr({"empty": []}, config=FAST, min_total_seconds=0)
    assert empty["activity_count"] == empty["valid_seconds"] == 0
    assert group(empty)["status"] == "unavailable"
    assert group(empty)["activity_count"] == 0
    assert hr_at_target_power([], 200)["hr_p25_bpm"] is None
