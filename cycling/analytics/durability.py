"""Power retention after accumulated work.

Durability is an observed-effort metric.  It never claims that a rider's
physiological ceiling fell: it reports the best complete power window found
after each cumulative-work threshold.
"""

from bisect import bisect_left
from dataclasses import dataclass
from datetime import datetime
from itertools import pairwise
from typing import Any, Dict, Iterable, List, Optional, Sequence

from cycling.analytics.power import _validate_durations
from cycling.analytics.rolling import _is_valid_value
from cycling.analytics.types import (
    AerobicDurabilityPoint,
    AerobicDurabilityResult,
    DurabilityPoint,
    DurabilityResult,
)

DEFAULT_DURATIONS_S = (300, 1200, 1800, 3600)
DEFAULT_THRESHOLDS_KJ = (1000.0, 1500.0, 1800.0, 2100.0, 2200.0)
DEFAULT_AEROBIC_WINDOW_S = 1200
DEFAULT_AEROBIC_THRESHOLDS_KJ = (1000.0, 1500.0, 1800.0, 2100.0, 2200.0)
DEFAULT_AEROBIC_BASELINE_MIN_OFFSET_S = 1800
FRESH_MAX_START_WORK_KJ = 500.0
MAX_UNEXPLAINED_GAP_S = 5.0


@dataclass(frozen=True)
class _PowerInterval:
    start_s: float
    end_s: float
    start_offset_s: float
    end_offset_s: float
    power_w: float
    start_work_j: float
    hr_bpm: Optional[float]

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s


def _time_s(sample: Dict[str, Any]) -> float:
    """Return timestamp seconds, falling back to elapsed_s for old fixtures."""
    timestamp = sample.get("timestamp")
    if timestamp is not None:
        if isinstance(timestamp, (int, float)) and not isinstance(timestamp, bool):
            return float(timestamp)
        if isinstance(timestamp, datetime):
            return timestamp.timestamp()
        try:
            return datetime.fromisoformat(
                str(timestamp).replace("Z", "+00:00")
            ).timestamp()
        except (TypeError, ValueError, OverflowError):
            pass
    elapsed = sample.get("elapsed_s")
    if not _is_valid_value(elapsed):
        raise ValueError("samples require timestamp or elapsed_s")
    return float(elapsed)


def _ordered_samples(samples: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    ordered = sorted(samples, key=_time_s)
    if any(_time_s(a) >= _time_s(b) for a, b in pairwise(ordered)):
        raise ValueError("samples must have strictly increasing timestamps")
    return ordered


def _intervals(
    samples: Sequence[Dict[str, Any]],
    activity_duration_s: Optional[float] = None,
) -> tuple[List[List[_PowerInterval]], float, float, float, int]:
    """Build valid power intervals and cumulative work from actual timestamps.

    A sample's power applies until the next stream timestamp.  A final
    timestamped sample contributes only when the caller supplies an explicit
    activity duration (or sample duration); otherwise no end is invented.
    Legacy fixtures without timestamps retain their one-second sample semantics.
    """
    ordered = _ordered_samples(samples)
    has_timestamps = any(sample.get("timestamp") is not None for sample in ordered)
    if not ordered:
        return [], 0.0, 0.0, 0.0, 0

    runs: List[List[_PowerInterval]] = []
    run: List[_PowerInterval] = []
    work_j = 0.0
    active_duration = 0.0
    valid_duration = 0.0
    gaps = 0
    origin_s = _time_s(ordered[0]) if ordered else 0.0

    def close_run() -> None:
        nonlocal run
        if run:
            runs.append(run)
            run = []

    for index, sample in enumerate(ordered):
        if not sample.get("active", False):
            close_run()
            continue

        start_s = _time_s(sample)
        next_sample = ordered[index + 1] if index + 1 < len(ordered) else None
        if next_sample is None:
            if has_timestamps:
                duration = sample.get("sample_duration_s")
                if not _is_valid_value(duration) and _is_valid_value(
                    activity_duration_s
                ):
                    duration = float(activity_duration_s) - (
                        start_s - _time_s(ordered[0])
                    )
                if not _is_valid_value(duration):
                    close_run()
                    continue
                end_s = start_s + float(duration)
            else:
                end_s = start_s + 1.0
        else:
            end_s = _time_s(next_sample)

        delta_s = end_s - start_s
        if delta_s <= 0:
            close_run()
            continue
        if (not has_timestamps and delta_s != 1.0) or delta_s > MAX_UNEXPLAINED_GAP_S:
            active_duration += delta_s
            gaps += 1
            close_run()
            continue

        active_duration += delta_s
        power = sample.get("power_w")
        valid_power = _is_valid_value(power)
        if valid_power:
            power_w = float(power)
            elapsed = sample.get("elapsed_s")
            offset_s = (
                float(elapsed) if _is_valid_value(elapsed) else start_s - origin_s
            )
            next_elapsed = (
                next_sample.get("elapsed_s") if next_sample is not None else None
            )
            end_offset_s = (
                float(next_elapsed)
                if _is_valid_value(next_elapsed)
                else offset_s + delta_s
            )
            hr = sample.get("hr_bpm")
            interval = _PowerInterval(
                start_s,
                end_s,
                offset_s,
                end_offset_s,
                power_w,
                work_j,
                float(hr) if _is_valid_value(hr) else None,
            )
            run.append(interval)
            work_j += power_w * delta_s
            valid_duration += delta_s
        else:
            close_run()

        if next_sample is not None and (
            not next_sample.get("active", False)
            or not _is_valid_value(next_sample.get("power_w"))
            or next_sample.get("segment") != sample.get("segment")
        ):
            close_run()

    close_run()
    coverage = valid_duration / active_duration if active_duration > 0 else 0.0
    return runs, work_j, coverage, active_duration, gaps


def _window_power(
    run: Sequence[_PowerInterval],
    start_index: int,
    duration_s: int,
    ends: Sequence[float],
    prefix_work_j: Sequence[float],
) -> Optional[float]:
    """Return exact-duration average power, allowing an irregular final slice."""
    target_end = run[start_index].start_s + duration_s
    if target_end > run[-1].end_s + 1e-9:
        return None

    end_index = bisect_left(ends, target_end)
    if end_index >= len(run):
        end_index = len(run) - 1
    if target_end < run[end_index].start_s - 1e-9:
        return None

    work = prefix_work_j[end_index] - prefix_work_j[start_index]
    final_slice = target_end - run[end_index].start_s
    if final_slice < -1e-9 or final_slice > run[end_index].duration_s + 1e-9:
        return None
    work += run[end_index].power_w * max(0.0, final_slice)
    return work / duration_s


def _best_window(
    runs: Sequence[Sequence[_PowerInterval]],
    duration_s: int,
    minimum_start_work_j: Optional[float] = None,
    maximum_start_work_j: Optional[float] = None,
) -> Optional[Dict[str, float]]:
    best: Optional[Dict[str, float]] = None
    for run in runs:
        ends = [interval.end_s for interval in run]
        prefix_work_j = [0.0]
        for interval in run:
            prefix_work_j.append(
                prefix_work_j[-1] + interval.power_w * interval.duration_s
            )
        for index, interval in enumerate(run):
            if (
                minimum_start_work_j is not None
                and interval.start_work_j < minimum_start_work_j
            ):
                continue
            if (
                maximum_start_work_j is not None
                and interval.start_work_j > maximum_start_work_j
            ):
                continue
            power_w = _window_power(run, index, duration_s, ends, prefix_work_j)
            if power_w is None or (best is not None and power_w <= best["power_w"]):
                continue
            best = {
                "power_w": power_w,
                "start_s": interval.start_offset_s,
                "start_work_kj": interval.start_work_j / 1000.0,
            }
    return best


def _available_exposure_seconds(
    runs: Iterable[Sequence[_PowerInterval]], threshold_kj: float
) -> float:
    threshold_j = threshold_kj * 1000.0
    return sum(
        interval.duration_s
        for run in runs
        for interval in run
        if interval.start_work_j >= threshold_j
    )


def get_durability_state(threshold_kj: float) -> str:
    if threshold_kj < 1000:
        return "Fresh"
    if threshold_kj < 1500:
        return "Early endurance"
    if threshold_kj < 1800:
        return "Meaningful fatigue"
    if threshold_kj < 2100:
        return "Late-race"
    return "Deep fatigue"


def calculate_fresh_reference_single(
    samples: Sequence[Dict[str, Any]],
    duration_s: int,
    activity_id: Optional[str] = None,
    activity_date: Optional[str] = None,
    activity_duration_s: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """Find the best observed window beginning within the fresh-work range."""
    runs, _, _, _, _ = _intervals(samples, activity_duration_s)
    best = _best_window(
        runs,
        duration_s,
        maximum_start_work_j=FRESH_MAX_START_WORK_KJ * 1000.0,
    )
    if best is None:
        return None
    return {
        "power_w": best["power_w"],
        "activity_id": activity_id,
        "date": activity_date,
        "start_work_kj": best["start_work_kj"],
        "start_offset_s": best["start_s"],
        "source": "activity_fresh" if activity_id else "single_activity",
    }


def _empty_result(
    durations: Sequence[int], reason: str, coverage: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    result = DurabilityResult(
        available=False,
        reason=reason,
        durations_s=list(durations),
        fresh_reference={},
        points=[],
        coverage=coverage,
    ).to_dict()
    result["buckets"] = []
    return result


def durability(
    samples: Sequence[Dict[str, Any]],
    durations: Optional[Sequence[int]] = None,
    thresholds_kj: Optional[Sequence[float]] = None,
    historical_fresh_references: Optional[Dict[int, Dict[str, Any]]] = None,
    activity_id: Optional[str] = None,
    activity_date: Optional[str] = None,
    activity_duration_s: Optional[float] = None,
    bucket_kj: Optional[Sequence[float]] = None,
) -> Dict[str, Any]:
    """Calculate observed power retention after accumulated work.

    Fresh references are supplied by the service after searching historical
    activities.  Without a reference, retention stays null rather than using
    the current activity as its own baseline.
    """
    dur_seq = tuple(DEFAULT_DURATIONS_S if durations is None else durations)
    if bucket_kj is not None and thresholds_kj is None:
        thresholds_kj = tuple(float(value) for value in bucket_kj if value > 0)
    thresh_seq = tuple(
        DEFAULT_THRESHOLDS_KJ if thresholds_kj is None else thresholds_kj
    )
    _validate_durations(dur_seq)
    if any(not isinstance(value, (int, float)) for value in thresh_seq):
        raise ValueError("thresholds_kj must be numeric")
    thresh_seq = tuple(float(value) for value in thresh_seq)
    if any(value < 0 for value in thresh_seq) or any(
        left >= right for left, right in pairwise(thresh_seq)
    ):
        raise ValueError("thresholds_kj must be non-negative and strictly increasing")

    active = sorted(
        (sample for sample in samples if sample.get("active", False)),
        key=_time_s,
    )
    active_count = len(active)
    valid_count = sum(1 for sample in active if _is_valid_value(sample.get("power_w")))
    if not active or not valid_count:
        return _empty_result(
            dur_seq,
            "power coverage is unavailable; no valid active power samples found",
        )

    runs, total_work_j, coverage_fraction, active_duration_s, gap_count = _intervals(
        samples, activity_duration_s
    )
    if not runs:
        return _empty_result(
            dur_seq,
            "power durability has no complete timestamped power intervals",
            {
                "seconds": 0,
                "fraction": coverage_fraction,
                "active_duration_s": active_duration_s,
                "gaps": gap_count,
            },
        )

    coverage = {
        "seconds": round(active_duration_s * coverage_fraction),
        "fraction": coverage_fraction,
        "active_duration_s": active_duration_s,
        "valid_power_duration_s": active_duration_s * coverage_fraction,
        "gaps": gap_count,
        "active_samples": active_count,
        "valid_power_samples": valid_count,
    }

    fresh_reference_map: Dict[int, Dict[str, Any]] = {}
    for duration_s in dur_seq:
        reference = (
            historical_fresh_references.get(duration_s)
            if historical_fresh_references is not None
            else None
        )
        if reference and _is_valid_value(reference.get("power_w")):
            fresh_reference_map[duration_s] = reference

    points: List[Dict[str, Any]] = []
    for duration_s in dur_seq:
        reference = fresh_reference_map.get(duration_s, {})
        reference_power = reference.get("power_w")
        for threshold_kj in thresh_seq:
            exposure_s = _available_exposure_seconds(runs, threshold_kj)
            best = _best_window(
                runs,
                duration_s,
                minimum_start_work_j=threshold_kj * 1000.0,
            )
            power_w = best["power_w"] if best else None
            retention_pct = (
                power_w / float(reference_power) * 100.0
                if power_w is not None
                and _is_valid_value(reference_power)
                and float(reference_power) > 0
                else None
            )
            effort_ratio = (
                power_w / float(reference_power)
                if power_w is not None
                and _is_valid_value(reference_power)
                and float(reference_power) > 0
                else None
            )
            if (
                power_w is not None
                and effort_ratio is not None
                and coverage_fraction >= 0.95
                and exposure_s >= duration_s
                and effort_ratio >= 0.90
            ):
                confidence = "HIGH"
            elif (
                power_w is not None
                and effort_ratio is not None
                and coverage_fraction >= 0.75
                and exposure_s >= duration_s
                and effort_ratio >= 0.75
            ):
                confidence = "MEDIUM"
            else:
                confidence = "LOW"

            points.append(
                DurabilityPoint(
                    power_w=power_w,
                    retention_pct=retention_pct,
                    threshold_kj=threshold_kj,
                    duration_s=duration_s,
                    activity_id=activity_id,
                    start_offset_s=best["start_s"] if best else None,
                    start_work_kj=best["start_work_kj"] if best else None,
                    available_exposure_seconds=exposure_s,
                    confidence=confidence,
                    state=get_durability_state(threshold_kj),
                    evidence={
                        "reference_power_w": reference_power,
                        "reference_source": reference.get("source"),
                        "coverage_fraction": coverage_fraction,
                        "effort_ratio": effort_ratio,
                        "gap_count": gap_count,
                    },
                ).to_dict()
            )

    formatted_reference = {
        str(duration): value for duration, value in fresh_reference_map.items()
    }
    legacy_buckets = []
    for threshold_kj in thresh_seq:
        threshold_points = [
            point for point in points if point["threshold_kj"] == threshold_kj
        ]
        legacy_buckets.append(
            {
                "start_kj": threshold_kj,
                "end_kj": None,
                "exposure_kj": max(0.0, total_work_j / 1000.0 - threshold_kj),
                "exposure_seconds": round(
                    _available_exposure_seconds(runs, threshold_kj)
                ),
                "best_w": {
                    point["duration_s"]: point["power_w"] for point in threshold_points
                },
                "change_percent": {
                    point["duration_s"]: (
                        point["retention_pct"] - 100.0
                        if point["retention_pct"] is not None
                        else None
                    )
                    for point in threshold_points
                },
            }
        )

    reason = "best observed complete efforts after work thresholds; no physiological extrapolation"
    if not fresh_reference_map:
        reason = (
            "no fresh reference available in the supplied history; points remain null "
            "until a fresh effort is observed"
        )
    if coverage_fraction < 0.95 or gap_count:
        reason += "; gaps or incomplete power coverage reduce confidence"

    return {
        **DurabilityResult(
            available=True,
            reason=reason,
            durations_s=list(dur_seq),
            fresh_reference=formatted_reference,
            points=points,
            total_work_kj=total_work_j / 1000.0,
            exposure_seconds=round(active_duration_s),
            coverage=coverage,
        ).to_dict(),
        "thresholds_kj": list(thresh_seq),
        "buckets": legacy_buckets,
    }


def _paired_runs(
    runs: Sequence[Sequence[_PowerInterval]],
) -> List[List[_PowerInterval]]:
    paired: List[List[_PowerInterval]] = []
    for run in runs:
        current: List[_PowerInterval] = []
        for interval in run:
            if interval.hr_bpm is None:
                if current:
                    paired.append(current)
                    current = []
                continue
            current.append(interval)
        if current:
            paired.append(current)
    return paired


def _window_prefixes(
    run: Sequence[_PowerInterval],
) -> tuple[List[float], Dict[str, List[float]]]:
    ends = [interval.end_s for interval in run]
    prefixes = {"power": [0.0], "hr": [0.0], "power_sq": [0.0], "coast": [0.0]}
    for interval in run:
        prefixes["power"].append(
            prefixes["power"][-1] + interval.power_w * interval.duration_s
        )
        prefixes["hr"].append(
            prefixes["hr"][-1] + float(interval.hr_bpm) * interval.duration_s
        )
        prefixes["power_sq"].append(
            prefixes["power_sq"][-1] + interval.power_w**2 * interval.duration_s
        )
        prefixes["coast"].append(
            prefixes["coast"][-1]
            + (interval.duration_s if interval.power_w <= 1.0 else 0.0)
        )
    return ends, prefixes


def _window_metrics(
    run: Sequence[_PowerInterval],
    start_index: int,
    duration_s: int,
    ends: Sequence[float],
    prefixes: Dict[str, Sequence[float]],
) -> Optional[Dict[str, float]]:
    target_end = run[start_index].start_s + duration_s
    if target_end > run[-1].end_s + 1e-9:
        return None

    end_index = bisect_left(ends, target_end)
    if end_index >= len(run):
        end_index = len(run) - 1
    if target_end < run[end_index].start_s - 1e-9:
        return None

    final_slice = target_end - run[end_index].start_s
    if final_slice < -1e-9 or final_slice > run[end_index].duration_s + 1e-9:
        return None

    def integral(name: str, value: float) -> float:
        return (
            prefixes[name][end_index]
            - prefixes[name][start_index]
            + value * max(0.0, final_slice)
        )

    avg_power = integral("power", run[end_index].power_w) / duration_s
    avg_hr = integral("hr", float(run[end_index].hr_bpm)) / duration_s
    avg_power_sq = integral("power_sq", run[end_index].power_w ** 2) / duration_s
    variance = max(0.0, avg_power_sq - avg_power**2)
    power_cv = variance**0.5 / avg_power if avg_power > 0 else float("inf")
    coasting_pct = integral("coast", 0.0) / duration_s * 100.0
    return {
        "avg_power": avg_power,
        "avg_hr": avg_hr,
        "efficiency_factor": avg_power / avg_hr if avg_hr > 0 else 0.0,
        "power_cv": power_cv,
        "coasting_pct": coasting_pct,
        "window_start": run[start_index].start_offset_s,
        "window_end": run[end_index].start_offset_s + max(0.0, final_slice),
        "start_work_kj": run[start_index].start_work_j / 1000.0,
    }


def _window_quality(metrics: Dict[str, float]) -> List[str]:
    flags = []
    if metrics["power_cv"] > 0.15:
        flags.append("large_power_variation")
    if metrics["coasting_pct"] > 10.0:
        flags.append("excessive_coasting")
    if metrics["avg_power"] <= 0 or metrics["avg_hr"] <= 0:
        flags.append("invalid_efficiency_inputs")
    return flags


def _find_aerobic_window(
    runs: Sequence[Sequence[_PowerInterval]],
    duration_s: int,
    minimum_start_work_kj: float = 0.0,
    minimum_start_offset_s: float = 0.0,
    maximum_start_work_kj: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    first_candidate = None
    threshold_j = minimum_start_work_kj * 1000.0
    for run in runs:
        ends, prefixes = _window_prefixes(run)
        for index, interval in enumerate(run):
            if interval.start_work_j < threshold_j:
                continue
            if interval.start_offset_s < minimum_start_offset_s:
                continue
            if (
                maximum_start_work_kj is not None
                and interval.start_work_j > maximum_start_work_kj * 1000.0
            ):
                continue
            metrics = _window_metrics(run, index, duration_s, ends, prefixes)
            if metrics is None:
                continue
            candidate = {**metrics, "quality_flags": _window_quality(metrics)}
            if first_candidate is None:
                first_candidate = candidate
            if not candidate["quality_flags"]:
                return candidate
    return first_candidate


def _aerobic_confidence(
    metrics: Optional[Dict[str, Any]], coverage_fraction: float, gap_count: int
) -> str:
    if metrics is None:
        return "LOW"
    if coverage_fraction >= 0.95 and gap_count == 0 and not metrics["quality_flags"]:
        return "HIGH"
    if coverage_fraction >= 0.75 and not metrics["quality_flags"]:
        return "MEDIUM"
    return "LOW"


def calculate_aerobic_durability(
    samples: Sequence[Dict[str, Any]],
    window_duration_s: int = DEFAULT_AEROBIC_WINDOW_S,
    thresholds_kj: Optional[Sequence[float]] = None,
    baseline_min_offset_s: int = DEFAULT_AEROBIC_BASELINE_MIN_OFFSET_S,
    activity_duration_s: Optional[float] = None,
) -> Dict[str, Any]:
    """Calculate EF retention in stable power/HR windows after prior work."""
    _validate_durations([window_duration_s])
    threshold_seq = tuple(
        DEFAULT_AEROBIC_THRESHOLDS_KJ
        if thresholds_kj is None
        else (float(value) for value in thresholds_kj)
    )
    if not threshold_seq or any(value < 0 for value in threshold_seq):
        raise ValueError("thresholds_kj must contain non-negative values")
    if any(left >= right for left, right in pairwise(threshold_seq)):
        raise ValueError("thresholds_kj must be strictly increasing")

    runs, total_work_j, power_coverage, active_duration_s, gap_count = _intervals(
        samples, activity_duration_s
    )
    power_duration = sum(interval.duration_s for run in runs for interval in run)
    hr_duration = sum(
        interval.duration_s
        for run in runs
        for interval in run
        if interval.hr_bpm is not None
    )
    hr_coverage = hr_duration / active_duration_s if active_duration_s else 0.0
    coverage = {
        "power_fraction": power_coverage,
        "hr_fraction": hr_coverage,
        "active_duration_s": active_duration_s,
        "power_duration_s": power_duration,
        "hr_duration_s": hr_duration,
        "gaps": gap_count,
    }
    if not runs or power_coverage < 0.75 or hr_coverage < 0.75:
        return AerobicDurabilityResult(
            available=False,
            reason="Aerobic durability requires sufficient power and heart-rate data.",
            window_duration_s=window_duration_s,
            thresholds_kj=list(threshold_seq),
            coverage=coverage,
            total_work_kj=total_work_j / 1000.0,
        ).to_dict()

    paired_runs = _paired_runs(runs)
    if not paired_runs:
        return AerobicDurabilityResult(
            available=False,
            reason="Aerobic durability requires sufficient power and heart-rate data.",
            window_duration_s=window_duration_s,
            thresholds_kj=list(threshold_seq),
            coverage=coverage,
            total_work_kj=total_work_j / 1000.0,
        ).to_dict()

    latest_start = max(
        (run[-1].end_offset_s - window_duration_s for run in paired_runs),
        default=0.0,
    )
    effective_baseline_min_offset = min(
        float(baseline_min_offset_s), max(0.0, latest_start)
    )
    baseline_window = _find_aerobic_window(
        paired_runs,
        window_duration_s,
        minimum_start_offset_s=effective_baseline_min_offset,
        maximum_start_work_kj=FRESH_MAX_START_WORK_KJ,
    )
    if baseline_window is None or baseline_window["efficiency_factor"] <= 0:
        return AerobicDurabilityResult(
            available=False,
            reason="No stable fresh power/heart-rate baseline was available.",
            window_duration_s=window_duration_s,
            thresholds_kj=list(threshold_seq),
            coverage=coverage,
            total_work_kj=total_work_j / 1000.0,
        ).to_dict()

    baseline_confidence = _aerobic_confidence(
        baseline_window, min(power_coverage, hr_coverage), gap_count
    )
    baseline = {
        **baseline_window,
        "confidence": baseline_confidence,
        "start_work_kj": baseline_window["start_work_kj"],
    }
    points = []
    for threshold_kj in threshold_seq:
        window = _find_aerobic_window(
            paired_runs,
            window_duration_s,
            minimum_start_work_kj=threshold_kj,
        )
        efficiency_factor = window.get("efficiency_factor") if window else None
        retention_pct = (
            efficiency_factor / baseline["efficiency_factor"] * 100.0
            if efficiency_factor is not None
            else None
        )
        efficiency_loss_pct = (
            100.0 - retention_pct if retention_pct is not None else None
        )
        confidence = _aerobic_confidence(
            window, min(power_coverage, hr_coverage), gap_count
        )
        points.append(
            AerobicDurabilityPoint(
                threshold_kj=threshold_kj,
                state=get_durability_state(threshold_kj),
                avg_power=window.get("avg_power") if window else None,
                avg_hr=window.get("avg_hr") if window else None,
                efficiency_factor=efficiency_factor,
                retention_pct=retention_pct,
                efficiency_loss_pct=efficiency_loss_pct,
                window_start=window.get("window_start") if window else None,
                window_end=window.get("window_end") if window else None,
                confidence=confidence,
                evidence={
                    "power_cv": window.get("power_cv") if window else None,
                    "coasting_pct": window.get("coasting_pct") if window else None,
                    "quality_flags": window.get("quality_flags", [])
                    if window
                    else ["insufficient_exposure"],
                    "available_exposure_seconds": (
                        max(
                            0.0,
                            sum(
                                interval.duration_s
                                for run in paired_runs
                                for interval in run
                                if interval.start_work_j >= threshold_kj * 1000.0
                            ),
                        )
                    ),
                },
            ).to_dict()
        )

    reason = "EF observed in stable power/heart-rate windows; no extrapolation"
    if gap_count or min(power_coverage, hr_coverage) < 0.95:
        reason += "; incomplete coverage or gaps reduce confidence"
    return AerobicDurabilityResult(
        available=True,
        reason=reason,
        window_duration_s=window_duration_s,
        thresholds_kj=list(threshold_seq),
        baseline=baseline,
        points=points,
        coverage=coverage,
        total_work_kj=total_work_j / 1000.0,
    ).to_dict()
