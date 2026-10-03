"""Backend wiring checks; the pure Power-HR tests remain independent."""

import json
from datetime import date
from unittest.mock import Mock, patch

import pytest
from pydantic import ValidationError

from cycling.models import AthleteParameters, PowerHRRequest
from cycling.postgres_store import PostgresStore
from cycling.service import CyclingService
from cycling.storage import Store
from tests.test_postgres_store import stores as stores
from tests.test_postgres_store import upload

FAST = {"lag_s": 15, "power_window_s": 1, "hr_window_s": 1}


def write_ride(store, ident, day="2026-01-01", modality="road", seconds=600, hr=140):
    store.write_activity(
        ident,
        {
            "start_time": f"{day}T10:00:00Z",
            "elapsed_seconds": seconds,
            "modality": modality,
            "quality_flags": [],
            "rpe": 4,
        },
        [
            {
                "elapsed_s": second,
                "segment": 0,
                "active": True,
                "power_w": 200,
                "hr_bpm": hr,
                "cadence_rpm": 90,
            }
            for second in range(seconds)
        ],
    )


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "data") as store:
        yield store


def bucket(result, environment="outdoor", period="current_period"):
    return result.data[period]["environments"][environment]


def test_activity_first_aggregation_environment_isolation_and_historical_context(store):
    write_ride(store, "long", seconds=1200, hr=100)
    write_ride(store, "short", day="2026-01-02", hr=180)
    write_ride(store, "inside", modality="indoor", hr=150)
    write_ride(store, "unknown", modality="unknown", hr=90)
    parameter_id = store.add_parameters(
        AthleteParameters(
            effective_date=date(2026, 1, 2), ftp_w=400, hr_zone_bounds=[120, 135, 150]
        )
    )
    service = CyclingService(store)
    with (
        patch.object(store, "samples", wraps=store.samples) as samples,
        patch.object(service, "stream", side_effect=AssertionError("decimated")),
        patch.object(store, "save_metrics", side_effect=AssertionError("cache write")),
    ):
        result = service.power_hr(
            PowerHRRequest(**FAST, min_activities=2, min_total_seconds=1000)
        )
    assert samples.call_count == 4
    assert all(not call.kwargs for call in samples.call_args_list)
    outdoor = bucket(result)
    point = outdoor["groups"][0]["bins"][0]
    assert point["hr_median_bpm"] == point["hr_mean_bpm"] == 140
    assert point["activity_count"] == 2
    assert point["valid_seconds"] == 1770
    assert point["status"] == "ok"
    assert bucket(result, "indoor")["groups"][0]["bins"][0]["hr_median_bpm"] == 150
    assert set(bucket(result, "unknown")["activities"]) == {"unknown"}
    long, short = outdoor["activities"]["long"], outdoor["activities"]["short"]
    assert long["effective_ftp_w"] == 285
    assert short["effective_ftp_w"] == 400
    assert short["parameter_id"] == parameter_id
    assert short["hr_zone_bounds"] == [120, 135, 150]
    assert short["date"] == "2026-01-02"
    assert short["modality"] == "road"
    assert short["duration_s"] == 600
    assert short["total_work_kj"] == 120
    assert short["context"]["rpe"] == 4
    assert result.parameter_mode == "historical"
    assert outdoor["statistic_basis"] == "activity_bin_hr_medians"
    json.dumps(result.model_dump(mode="json"), allow_nan=False)


def test_single_current_parameters_and_fixed_power_hr_trend(store):
    write_ride(store, "ride", seconds=900)
    parameter_id = store.add_parameters(
        AthleteParameters(effective_date=date(2035, 1, 1), ftp_w=350)
    )
    result = CyclingService(store).power_hr(
        PowerHRRequest(activity_id="ride", parameter_mode="current", **FAST)
    )
    activity = bucket(result)["activities"]["ride"]
    assert activity["effective_ftp_w"] == 350
    assert result.parameter_id == activity["parameter_id"] == parameter_id
    trend = bucket(result)["hr_at_fixed_power"]
    at_200_w = next(point for point in trend if point["target_power_w"] == 200)
    assert at_200_w["hr_median_bpm"] == 140
    assert at_200_w["matched_power_median_w"] == 200
    assert at_200_w["valid_seconds"] == 885
    assert at_200_w["effective_ftp_w"] == 350
    assert at_200_w["rolling_median_hr_bpm"] == 140
    assert activity["total_work_kj"] == 180
    assert result.data["fixed_power_trend_basis"]["lag_s"] == 15


def test_configurable_lag_and_unequal_rolling_means(store):
    write_ride(store, "ride", seconds=120)
    result = CyclingService(store).power_hr(
        PowerHRRequest(activity_id="ride", lag_s=60, power_window_s=5, hr_window_s=20)
    )
    analysis = bucket(result)["activities"]["ride"]["analysis"]
    assert analysis["valid_seconds"] == 56
    assert analysis["config"]["lag_s"] == 60
    assert analysis["config"]["power_window_s"] == 5
    assert analysis["config"]["hr_window_s"] == 20


def test_period_comparison_inclusive_selection_and_overlap_reuse(store):
    for day in (1, 2, 3):
        write_ride(store, str(day), day=f"2026-01-0{day}")
    with patch.object(store, "samples", wraps=store.samples) as samples:
        result = CyclingService(store).power_hr(
            PowerHRRequest(
                period="custom",
                start_date="2026-01-02",
                end_date="2026-01-03",
                compare_period={
                    "period": "custom",
                    "start_date": "2026-01-01",
                    "end_date": "2026-01-02",
                },
                environment="outdoor",
                **FAST,
            )
        )
    assert samples.call_count == 3
    assert set(bucket(result)["activities"]) == {"2", "3"}
    assert set(bucket(result, period="comparison_period")["activities"]) == {"1", "2"}
    assert result.data["comparison_period"]["selection"]["end_date"] == "2026-01-02"
    anchored = CyclingService(store).power_hr(
        PowerHRRequest(period="7d", end_date="2026-01-01", **FAST)
    )
    assert set(bucket(anchored)["activities"]) == {"1"}
    assert PowerHRRequest(period="28d").period == "28d"
    assert PowerHRRequest(period="42d").period == "42d"


def test_period_presets_use_exact_day_count_and_shared_environment_anchor(store):
    write_ride(store, "too-old", day="2026-09-03", modality="road")
    write_ride(store, "first-day", day="2026-09-04", modality="road")
    write_ride(store, "last-day", day="2026-10-01", modality="road")
    write_ride(store, "old-indoor", day="2026-09-01", modality="indoor")
    service = CyclingService(store)

    current = service.power_hr(
        PowerHRRequest(period="28d", end_date="2026-10-01", environment="both", **FAST)
    )
    assert set(bucket(current, "outdoor")["activities"]) == {"first-day", "last-day"}
    assert bucket(current, "indoor")["activities"] == {}

    comparison = service.power_hr(
        PowerHRRequest(
            period="28d",
            end_date="2026-10-01",
            compare_period={"period": "28d"},
            environment="outdoor",
            **FAST,
        )
    )
    assert comparison.data["comparison_period"]["selection"]["end_date"] == "2026-09-03"
    assert set(bucket(comparison, "outdoor", "comparison_period")["activities"]) == {
        "too-old"
    }

    indoor = service.power_hr(
        PowerHRRequest(period="7d", end_date="2026-10-01", environment="indoor", **FAST)
    )
    assert bucket(indoor, "indoor")["activities"] == {}


def test_stable_filters_aerobic_bounds_fatigue_elapsed_groups_and_confidence(store):
    write_ride(store, "ride", seconds=600)
    result = CyclingService(store).power_hr(
        PowerHRRequest(
            activity_id="ride",
            mode="stable",
            bin_size_w=20,
            max_power_cv=0.01,
            min_power_ftp_fraction=0.6,
            min_cadence_rpm=85,
            aerobic_floor_ftp_fraction=0.6,
            aerobic_ceiling_ftp_fraction=0.75,
            fatigue_thresholds_kj=[50, 100],
            elapsed_splits=3,
            min_activities=2,
            min_total_seconds=500,
            **FAST,
        )
    )
    outdoor = bucket(result)
    analysis = outdoor["activities"]["ride"]["analysis"]
    assert analysis["valid_seconds"] == 406
    assert analysis["config"]["stable_window_s"] == 180
    assert analysis["config"]["max_power_cv"] == 0.01
    assert len(analysis["groups"]) == 7
    assert analysis["grouping"]["elapsed_splits"] == 3
    assert analysis["grouping"]["fatigue_thresholds_kj"] == [50, 100]
    point = outdoor["groups"][0]["bins"][0]
    assert point["power_low_w"] == 200
    assert point["power_high_w"] == 220
    assert point["confidence_reasons"] == [
        "insufficient_activities",
        "insufficient_exposure",
    ]
    excluded = CyclingService(store).power_hr(
        PowerHRRequest(activity_id="ride", aerobic_ceiling_ftp_fraction=0.6, **FAST)
    )
    assert bucket(excluded)["activity_count"] == 0


def test_missing_parameters_do_not_abort_other_activities_or_observed_mode(store):
    write_ride(store, "missing")
    write_ride(store, "known")
    scoped = Mock(spec=PostgresStore)
    scoped.activities.return_value = store.activities()
    scoped.samples.side_effect = store.samples
    scoped.parameters.side_effect = [
        (None, None),
        ("known-parameters", AthleteParameters()),
    ]
    stable = CyclingService(scoped).power_hr(PowerHRRequest(mode="stable", **FAST))
    members = bucket(stable)["activities"]
    missing = next(member for member in members.values() if member["reason"])
    assert missing["reason"] == "missing_ftp"
    assert "analysis" not in missing
    assert missing["effective_ftp_w"] is missing["hr_zone_bounds"] is None
    assert missing["total_work_kj"] == 120
    assert bucket(stable)["activity_count"] == 1
    scoped.cached.assert_not_called()
    scoped.save_metrics.assert_not_called()
    with patch.object(store, "parameters", return_value=(None, None)):
        observed = CyclingService(store).power_hr(PowerHRRequest(**FAST))
        aerobic = CyclingService(store).power_hr(
            PowerHRRequest(aerobic_floor_ftp_fraction=0.5, **FAST)
        )
    assert bucket(observed)["activity_count"] == 2
    assert all(
        m["parameter_warning"] == "missing_parameters"
        for m in bucket(observed)["activities"].values()
    )
    assert bucket(aerobic)["activity_count"] == 0
    assert all(
        m["reason"] == "missing_ftp" for m in bucket(aerobic)["activities"].values()
    )


@pytest.mark.parametrize("missing", [[], FileNotFoundError("private object")])
def test_missing_samples_are_unavailable_for_both_store_behaviors(store, missing):
    write_ride(store, "ride")
    kwargs = (
        {"side_effect": missing}
        if isinstance(missing, Exception)
        else {"return_value": missing}
    )
    with patch.object(store, "samples", **kwargs):
        result = CyclingService(store).power_hr(PowerHRRequest(activity_id="ride"))
    activity = bucket(result)["activities"]["ride"]
    assert activity["reason"] == "missing_samples"
    assert activity["total_work_kj"] is None
    assert bucket(result)["activity_count"] == 0
    json.dumps(result.model_dump(mode="json"), allow_nan=False)


def test_invalid_sensors_and_trailing_duration_remain_serializable(store):
    write_ride(store, "ride", seconds=120)
    samples = store.samples("ride")
    for row in samples[:60]:
        row.update(power_w=float("inf"), hr_bpm=True)
    samples = samples[:-10]
    with patch.object(store, "samples", return_value=samples):
        result = CyclingService(store).power_hr(
            PowerHRRequest(activity_id="ride", elapsed_splits=2, **FAST)
        )
    activity = bucket(result)["activities"]["ride"]
    assert activity["analysis"]["elapsed_duration_s"] == 120
    assert activity["work_complete"] is False
    assert activity["total_work_kj"] is None
    json.dumps(result.model_dump(mode="json"), allow_nan=False)


def test_empty_period_and_single_missing_activity(store):
    result = CyclingService(store).power_hr(PowerHRRequest())
    assert bucket(result)["groups"] == []
    assert bucket(result)["activities"] == {}
    assert bucket(result)["activity_count"] == 0
    with pytest.raises(KeyError):
        CyclingService(store).power_hr(PowerHRRequest(activity_id="foreign"))


def test_postgres_power_hr_owner_scope_and_missing_parameters(stores):
    alice, bob = stores("alice"), stores("bob")
    ident = upload(alice)["activity_id"]
    observed = CyclingService(alice).power_hr(PowerHRRequest(**FAST))
    members = bucket(observed, "unknown")["activities"]
    assert members[ident]["effective_ftp_w"] is None
    assert members[ident]["available"] is True
    stable = CyclingService(alice).power_hr(PowerHRRequest(mode="stable", **FAST))
    assert bucket(stable, "unknown")["activities"][ident]["reason"] == "missing_ftp"
    assert (
        bucket(CyclingService(bob).power_hr(PowerHRRequest()), "unknown")["activities"]
        == {}
    )
    with pytest.raises(KeyError):
        CyclingService(bob).power_hr(PowerHRRequest(activity_id=ident))
    assert alice.status()["metric_snapshots"] == 0


@pytest.mark.parametrize(
    "data",
    [
        {"lag_s": 14},
        {"lag_s": 61},
        {"lag_s": True},
        {"lag_s": 15.5},
        {"power_window_s": 0},
        {"hr_window_s": -1},
        {"stable_window_s": 0},
        {"bin_size_w": 0},
        {"bin_size_w": float("inf")},
        {"max_power_cv": float("nan")},
        {"environment": "all"},
        {"parameter_mode": "future"},
        {"mode": "bad"},
        {"fatigue_thresholds_kj": [1, 1]},
        {"fatigue_thresholds_kj": [0]},
        {"fatigue_thresholds_kj": [float("nan")]},
        {"elapsed_splits": 4},
        {"elapsed_splits": 2.0},
        {"min_activities": 0},
        {"min_total_seconds": -1},
        {"mode": "stable", "aerobic_ceiling_ftp_fraction": 0.39},
        {"aerobic_floor_ftp_fraction": 0.8, "aerobic_ceiling_ftp_fraction": 0.7},
        {"period": "custom", "start_date": "2026-01-01"},
        {
            "compare_period": {
                "period": "custom",
                "start_date": "2026-01-02",
                "end_date": "2026-01-01",
            }
        },
        {"activity_id": "ride", "period": "30d"},
        {"activity_id": "ride", "compare_period": {"period": "all"}},
        {"start_date": "2026-01-01"},
        {"target_power_w": [200, 200]},
        {"target_power_w": [float("inf")]},
        {"owner_id": "forged"},
    ],
)
def test_request_rejects_invalid_or_ambiguous_settings(data):
    with pytest.raises(ValidationError):
        PowerHRRequest.model_validate(data)
