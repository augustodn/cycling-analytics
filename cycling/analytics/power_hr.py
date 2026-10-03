"""Pure Power↔HR relationships for normalized one-second samples.

Power's complete trailing mean at t is paired with HR's trailing mean at
t + lag_s. No smoothing or lag pair crosses a pause, missing second, segment
change, or invalid power/HR sample. Stable mode also breaks on low cadence or
power outside the configured FTP fractions, and checks raw-power population
CV at both paired timestamps. Fractions/CV use 0–1 units, not percentages.

Each pair represents one observed second, even when rolling windows overlap.
Groups assign pairs by power's timestamp, not HR's later timestamp. Fatigue
uses work accumulated *before* that second; unknown work is never imputed.
Elapsed splits include pauses/gaps. Power bins are [low, high), anchored at
zero. Quartiles use inclusive linear interpolation; singleton quartiles equal
the observation. Across activities, statistics describe equally weighted
activity-bin HR medians, never pooled samples or exposure-weighted means.

These are descriptive observations, not causal fitness or fatigue estimates.
Heat, hydration, terrain, sensor accuracy and the selected lag remain confounders.
"""

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from itertools import pairwise
from math import floor, isfinite, sqrt
from statistics import fmean, median, quantiles
from typing import Any, Literal

from cycling.analytics.rolling import extract_runs, rolling_windows


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return float(value) if isfinite(value) else None
    except OverflowError:
        return None


def _positive_integer(value: Any, name: str, *, allow_zero: bool = False) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < (0 if allow_zero else 1)
    ):
        raise ValueError(f"{name} must be an integer {'>= 0' if allow_zero else '> 0'}")


@dataclass(frozen=True)
class PowerHRConfig:
    """Smoothing/alignment and optional stable-power eligibility controls.

    Supply the activity's effective FTP explicitly for stable mode or aerobic
    bounds. No historical FTP lookup or automatic lag estimation is performed.
    """

    mode: Literal["all", "stable"] = "all"
    lag_s: int = 30
    power_window_s: int = 30
    hr_window_s: int = 30
    bin_size_w: float = 10.0
    stable_window_s: int = 180
    max_power_cv: float = 0.08
    min_power_ftp_fraction: float = 0.40
    min_cadence_rpm: float = 50.0
    aerobic_floor_ftp_fraction: float | None = None
    aerobic_ceiling_ftp_fraction: float | None = None

    def __post_init__(self) -> None:
        if self.mode not in {"all", "stable"}:
            raise ValueError("mode must be 'all' or 'stable'")
        for name in ("lag_s", "power_window_s", "hr_window_s", "stable_window_s"):
            _positive_integer(getattr(self, name), name)
        if not 15 <= self.lag_s <= 60:
            raise ValueError("lag_s must be between 15 and 60 seconds")
        if _number(self.bin_size_w) is None or self.bin_size_w <= 0:
            raise ValueError("bin_size_w must be finite and positive")
        for name in ("max_power_cv", "min_power_ftp_fraction", "min_cadence_rpm"):
            value = getattr(self, name)
            if _number(value) is None or value < 0:
                raise ValueError(f"{name} must be finite and nonnegative")
        for name in ("aerobic_floor_ftp_fraction", "aerobic_ceiling_ftp_fraction"):
            value = getattr(self, name)
            if value is not None and (_number(value) is None or value < 0):
                raise ValueError(f"{name} must be finite and nonnegative")
        if self.aerobic_ceiling_ftp_fraction is not None:
            minimum = max(
                self.aerobic_floor_ftp_fraction or 0.0,
                self.min_power_ftp_fraction if self.mode == "stable" else 0.0,
            )
            if self.aerobic_ceiling_ftp_fraction < minimum:
                raise ValueError("aerobic ceiling must be >= the effective power floor")


DEFAULT_CONFIG = PowerHRConfig()


def _prepare_samples(samples: Sequence[Mapping[str, Any]]) -> list[dict]:
    """Validate structure, sanitize sensors, and annotate known accumulated work."""
    for row in samples:
        _positive_integer(row.get("elapsed_s"), "elapsed_s", allow_zero=True)
        segment = row.get("segment")
        if isinstance(segment, bool) or not isinstance(segment, int) or segment < 0:
            raise ValueError("segment must be a nonnegative integer")
        if not isinstance(row.get("active"), bool):
            raise ValueError("active must be a boolean")
    ordered = sorted(samples, key=lambda row: row["elapsed_s"])
    result = []
    work_j = 0.0
    complete = True
    previous_s = -1
    for row in ordered:
        elapsed = row["elapsed_s"]
        if elapsed == previous_s:
            raise ValueError("samples must have unique elapsed_s values")
        if elapsed != previous_s + 1:
            complete = False
        power, hr, cadence = (
            _number(row.get(key)) for key in ("power_w", "hr_bpm", "cadence_rpm")
        )
        power = power if power is not None and power >= 0 else None
        hr = hr if hr is not None and hr > 0 else None
        cadence = cadence if cadence is not None and cadence >= 0 else None
        if row["active"] and power is None:
            complete = False
        result.append(
            {
                "elapsed_s": elapsed,
                "segment": row["segment"],
                "active": row["active"],
                "power_w": power,
                "hr_bpm": hr,
                "cadence_rpm": cadence,
                "work_kj": work_j / 1000.0,
                "work_complete": complete,
            }
        )
        if row["active"] and power is not None:
            work_j += power
        previous_s = elapsed
    return result


def _ftp_bounds(
    config: PowerHRConfig, effective_ftp_w: float | None
) -> tuple[float, float]:
    needs_ftp = (
        config.mode == "stable"
        or config.aerobic_floor_ftp_fraction is not None
        or config.aerobic_ceiling_ftp_fraction is not None
    )
    if effective_ftp_w is not None and (
        _number(effective_ftp_w) is None or effective_ftp_w <= 0
    ):
        raise ValueError("effective_ftp_w must be finite and positive")
    if needs_ftp and effective_ftp_w is None:
        raise ValueError(
            "effective_ftp_w is required for stable mode or aerobic bounds"
        )
    floor_fraction = max(
        config.min_power_ftp_fraction if config.mode == "stable" else 0.0,
        config.aerobic_floor_ftp_fraction or 0.0,
    )
    return (
        floor_fraction * (effective_ftp_w or 0.0),
        config.aerobic_ceiling_ftp_fraction * effective_ftp_w
        if config.aerobic_ceiling_ftp_fraction is not None
        else float("inf"),
    )


def _power_cvs(run: list[dict], window_s: int) -> dict[int, float]:
    squares = [{**row, "power_w": row["power_w"] ** 2} for row in run]
    return {
        row["elapsed_s"]: sqrt(max(0.0, mean_square - average**2)) / average
        for (row, average), (_, mean_square) in zip(
            rolling_windows(run, "power_w", window_s),
            rolling_windows(squares, "power_w", window_s),
            strict=True,
        )
        if average > 0
    }


def _aligned_pairs(
    ordered: list[dict], config: PowerHRConfig, ftp: float | None
) -> list[dict]:
    minimum, maximum = _ftp_bounds(config, ftp)
    eligible = []
    for row in ordered:
        power = row["power_w"]
        valid = (
            power is not None
            and row["hr_bpm"] is not None
            and minimum <= power <= maximum
        )
        if config.mode == "stable":
            valid = (
                valid
                and row["cadence_rpm"] is not None
                and row["cadence_rpm"] > config.min_cadence_rpm
            )
        eligible.append({**row, "eligible_power_w": power if valid else None})
    pairs = []
    for run in extract_runs(eligible, "eligible_power_w"):
        powers = rolling_windows(run, "power_w", config.power_window_s)
        hrs = {
            row["elapsed_s"]: value
            for row, value in rolling_windows(run, "hr_bpm", config.hr_window_s)
        }
        cvs = _power_cvs(run, config.stable_window_s) if config.mode == "stable" else {}
        for row, power in powers:
            elapsed = row["elapsed_s"]
            hr_elapsed = elapsed + config.lag_s
            if hr_elapsed not in hrs:
                continue
            if config.mode == "stable" and any(
                cvs.get(second, float("inf")) > config.max_power_cv + 1e-12
                for second in (elapsed, hr_elapsed)
            ):
                continue
            pairs.append(
                {
                    "elapsed_s": elapsed,
                    "hr_elapsed_s": hr_elapsed,
                    "segment": row["segment"],
                    "power_w": power,
                    "hr_bpm": hrs[hr_elapsed],
                    "work_kj": row["work_kj"],
                    "work_complete": row["work_complete"],
                }
            )
    return pairs


def align_power_hr_samples(
    samples: Sequence[Mapping[str, Any]],
    *,
    config: PowerHRConfig = DEFAULT_CONFIG,
    effective_ftp_w: float | None = None,
) -> list[dict]:
    """Return gap-safe smoothed pairs, sorted by power timestamp (no input mutation)."""
    return _aligned_pairs(_prepare_samples(samples), config, effective_ftp_w)


def _hr_statistics(values: Sequence[float]) -> dict:
    quarters = (
        quantiles(values, n=4, method="inclusive")
        if len(values) > 1
        else list(values) * 3
    )
    return {
        "hr_median_bpm": quarters[1] if quarters else None,
        "hr_mean_bpm": fmean(values) if values else None,
        "hr_p25_bpm": quarters[0] if quarters else None,
        "hr_p75_bpm": quarters[2] if quarters else None,
    }


def _bin_pairs(pairs: Sequence[dict], bin_size_w: float) -> list[dict]:
    bins = defaultdict(list)
    for pair in pairs:
        bins[floor(pair["power_w"] / bin_size_w)].append(pair["hr_bpm"])
    return [
        {
            "power_low_w": index * bin_size_w,
            "power_high_w": (index + 1) * bin_size_w,
            "power_w": (index + 0.5) * bin_size_w,
            "valid_seconds": len(values),
            **_hr_statistics(values),
        }
        for index, values in sorted(bins.items())
    ]


def analyze_power_hr(
    samples: Sequence[Mapping[str, Any]],
    *,
    config: PowerHRConfig = DEFAULT_CONFIG,
    effective_ftp_w: float | None = None,
    fatigue_thresholds_kj: Sequence[float] = (),
    elapsed_splits: Literal[2, 3] | None = None,
    activity_duration_s: int | None = None,
) -> dict:
    """Bin a single activity, independently for all/work-band/elapsed-split groups.

    Fatigue thresholds are strictly increasing positive kJ boundaries; e.g.
    (500, 1000) yields [0,500), [500,1000), [1000,infinity). Later pairs with
    unknown preceding work remain in all/elapsed groups but not fatigue bands.
    Supply activity_duration_s when rows omit the activity's trailing seconds.
    """
    thresholds = list(fatigue_thresholds_kj)
    if any(_number(value) is None or value <= 0 for value in thresholds) or any(
        first >= second for first, second in pairwise(thresholds)
    ):
        raise ValueError(
            "fatigue thresholds must be finite, positive and strictly increasing"
        )
    if elapsed_splits is not None and (
        type(elapsed_splits) is not int or elapsed_splits not in (2, 3)
    ):
        raise ValueError("elapsed_splits must be 2, 3 or None")
    ordered = _prepare_samples(samples)
    observed_duration = ordered[-1]["elapsed_s"] + 1 if ordered else 0
    if activity_duration_s is not None:
        _positive_integer(activity_duration_s, "activity_duration_s")
        if activity_duration_s < observed_duration:
            raise ValueError("activity_duration_s must include every sample")
    duration = (
        activity_duration_s if activity_duration_s is not None else observed_duration
    )
    pairs = _aligned_pairs(ordered, config, effective_ftp_w)
    groups = []

    def add_group(metadata: dict, members: list[dict]) -> None:
        groups.append(
            {
                **metadata,
                "valid_seconds": len(members),
                "bins": _bin_pairs(members, config.bin_size_w),
            }
        )

    add_group({"group_id": "all", "kind": "all"}, pairs)
    if thresholds:
        edges = [0.0, *thresholds, None]
        for index, (lower, upper) in enumerate(pairwise(edges)):
            add_group(
                {
                    "group_id": f"work_{index + 1}",
                    "kind": "work_band",
                    "start_kj": lower,
                    "end_kj": upper,
                },
                [
                    pair
                    for pair in pairs
                    if pair["work_complete"]
                    and pair["work_kj"] >= lower
                    and (upper is None or pair["work_kj"] < upper)
                ],
            )
    if elapsed_splits:
        for index in range(elapsed_splits):
            add_group(
                {
                    "group_id": f"elapsed_{index + 1}_of_{elapsed_splits}",
                    "kind": "elapsed_split",
                    "start_fraction": index / elapsed_splits,
                    "end_fraction": (index + 1) / elapsed_splits,
                },
                [
                    pair
                    for pair in pairs
                    if pair["elapsed_s"] * elapsed_splits // duration == index
                ],
            )
    observed_work = (
        sum(
            row["power_w"]
            for row in ordered
            if row["active"] and row["power_w"] is not None
        )
        / 1000.0
    )
    return {
        "config": asdict(config),
        "effective_ftp_w": effective_ftp_w,
        "grouping": {
            "fatigue_thresholds_kj": thresholds,
            "elapsed_splits": elapsed_splits,
        },
        "elapsed_duration_s": duration,
        "valid_seconds": len(pairs),
        "observed_work_kj": observed_work,
        "work_complete": bool(ordered)
        and ordered[-1]["work_complete"]
        and duration == observed_duration,
        "fatigue_unclassified_seconds": sum(
            not pair["work_complete"] for pair in pairs
        ),
        "groups": groups,
    }


def _confidence(
    activity_count: int, seconds: int, min_activities: int, min_seconds: int
) -> dict:
    reasons = []
    if activity_count < min_activities:
        reasons.append("insufficient_activities")
    if seconds < min_seconds:
        reasons.append("insufficient_exposure")
    return {
        "status": "unavailable"
        if not seconds
        else "low_confidence"
        if reasons
        else "ok",
        "confidence_reasons": reasons,
    }


def aggregate_power_hr_bins(
    activity_results: Mapping[str, dict],
    *,
    min_activities: int = 3,
    min_total_seconds: int = 300,
) -> dict:
    """Aggregate compatible analyze_power_hr results with equal activity weight.

    Counts/seconds are per group/bin, not totals from unrelated bins or groups.
    Sparse bins retain their statistics with an explicit low-confidence status.
    Effective FTP and elapsed durations may differ; analysis/group settings may not.
    """
    _positive_integer(min_activities, "min_activities")
    _positive_integer(min_total_seconds, "min_total_seconds", allow_zero=True)
    grouped = {}
    settings = None
    for result in activity_results.values():
        current_settings = (result["config"], result["grouping"])
        if settings is not None and settings != current_settings:
            raise ValueError(
                "cannot aggregate incompatible Power-HR configurations/groupings"
            )
        settings = current_settings
        for group in result["groups"]:
            key = group["group_id"]
            if key not in grouped:
                grouped[key] = {
                    "metadata": {
                        k: v
                        for k, v in group.items()
                        if k not in {"bins", "valid_seconds"}
                    },
                    "activity_count": 0,
                    "bins": defaultdict(list),
                }
            grouped[key]["activity_count"] += int(group["valid_seconds"] > 0)
            for point in group["bins"]:
                grouped[key]["bins"][point["power_low_w"]].append(point)
    groups = []
    for data in grouped.values():
        bins = []
        for _, points in sorted(data["bins"].items()):
            seconds = sum(point["valid_seconds"] for point in points)
            bins.append(
                {
                    **{
                        key: points[0][key]
                        for key in ("power_low_w", "power_high_w", "power_w")
                    },
                    "activity_count": len(points),
                    "valid_seconds": seconds,
                    **_hr_statistics([point["hr_median_bpm"] for point in points]),
                    **_confidence(
                        len(points), seconds, min_activities, min_total_seconds
                    ),
                }
            )
        group_seconds = sum(point["valid_seconds"] for point in bins)
        groups.append(
            {
                **data["metadata"],
                "activity_count": data["activity_count"],
                "valid_seconds": group_seconds,
                "bins": bins,
                **_confidence(
                    data["activity_count"],
                    group_seconds,
                    min_activities,
                    min_total_seconds,
                ),
            }
        )
    return {
        "statistic_basis": "activity_bin_hr_medians",
        "activity_count": sum(
            result["valid_seconds"] > 0 for result in activity_results.values()
        ),
        "valid_seconds": sum(
            result["valid_seconds"] for result in activity_results.values()
        ),
        "min_activities": min_activities,
        "min_total_seconds": min_total_seconds,
        "groups": groups,
        "config": settings[0] if settings else None,
        "grouping": settings[1] if settings else None,
    }


def aggregate_power_hr(
    activity_samples: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    config: PowerHRConfig = DEFAULT_CONFIG,
    effective_ftps_w: Mapping[str, float] | None = None,
    fatigue_thresholds_kj: Sequence[float] = (),
    elapsed_splits: Literal[2, 3] | None = None,
    activity_durations_s: Mapping[str, int] | None = None,
    min_activities: int = 3,
    min_total_seconds: int = 300,
) -> dict:
    """First bin each distinct activity, then aggregate its group/bin medians."""
    activities = {
        activity_id: analyze_power_hr(
            samples,
            config=config,
            effective_ftp_w=(effective_ftps_w or {}).get(activity_id),
            fatigue_thresholds_kj=fatigue_thresholds_kj,
            elapsed_splits=elapsed_splits,
            activity_duration_s=(activity_durations_s or {}).get(activity_id),
        )
        for activity_id, samples in activity_samples.items()
    }
    return {
        **aggregate_power_hr_bins(
            activities,
            min_activities=min_activities,
            min_total_seconds=min_total_seconds,
        ),
        "activities": activities,
    }


def hr_at_target_power(
    pairs: Sequence[Mapping[str, Any]],
    target_power_w: float,
    *,
    tolerance_w: float = 5.0,
    min_seconds: int = 60,
) -> dict:
    """Summarize aligned pairs within target ± tolerance, inclusive; never interpolate.

    Call once per activity/group for fixed-power tracking. Input should come from
    align_power_hr_samples, not raw rows or already aggregated power-bin centers.
    """
    if _number(target_power_w) is None or target_power_w < 0:
        raise ValueError("target_power_w must be finite and nonnegative")
    if _number(tolerance_w) is None or tolerance_w < 0:
        raise ValueError("tolerance_w must be finite and nonnegative")
    _positive_integer(min_seconds, "min_seconds", allow_zero=True)
    matched = []
    for pair in pairs:
        power, hr = _number(pair.get("power_w")), _number(pair.get("hr_bpm"))
        if (
            power is not None
            and power >= 0
            and hr is not None
            and hr > 0
            and abs(power - target_power_w) <= tolerance_w
        ):
            matched.append((power, hr))
    power_median = median([power for power, _ in matched]) if matched else None
    hr_summary = _hr_statistics([hr for _, hr in matched])
    hr_median = hr_summary["hr_median_bpm"]
    return {
        "target_power_w": target_power_w,
        "tolerance_w": tolerance_w,
        "matched_power_median_w": power_median,
        "valid_seconds": len(matched),
        **hr_summary,
        "efficiency_w_per_bpm": power_median / hr_median
        if power_median is not None and hr_median
        else None,
        **_confidence(int(bool(matched)), len(matched), 1, min_seconds),
    }
