"""Power/HR zone comparison with data validity separate from window quality."""

from bisect import bisect_right
from collections import Counter
from datetime import datetime, timedelta
from math import ceil, isfinite
from statistics import mean, median, pstdev
from typing import Any, Sequence


def _valid(value: Any) -> bool:
    try:
        return value is not None and isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _cv(values: Sequence[float]) -> float | None:
    if not values:
        return None
    average = mean(values)
    return pstdev(values) / average if average else None


def _quality(
    power_cv_pct: float | None,
    zero_power_pct: float,
    cadence_rpm: float | None,
    hr_dominance_pct: float,
    power_dominance_pct: float,
    cadence_coverage_pct: float,
) -> tuple[str, list[str]]:
    flags = []
    if power_cv_pct is not None and power_cv_pct > 8:
        flags.append("power_cv_over_8_pct")
    if zero_power_pct > 5:
        flags.append("zero_power_over_5_pct")
    if cadence_rpm is None or cadence_rpm <= 70:
        flags.append("cadence_low_or_missing")
    if hr_dominance_pct < 60:
        flags.append("hr_zone_boundary_sensitive")
    if power_dominance_pct < 60:
        flags.append("power_zone_boundary_sensitive")

    low_quality = (
        (power_cv_pct is not None and power_cv_pct > 15)
        or zero_power_pct > 10
        or (cadence_rpm is not None and cadence_rpm <= 60)
        or hr_dominance_pct < 50
        or power_dominance_pct < 50
    )
    high_quality = (
        power_cv_pct is not None
        and power_cv_pct <= 8
        and zero_power_pct <= 5
        and cadence_rpm is not None
        and cadence_rpm > 70
        and cadence_coverage_pct >= 90
        and hr_dominance_pct >= 60
        and power_dominance_pct >= 60
    )
    return ("LOW" if low_quality else "HIGH" if high_quality else "MEDIUM", flags)


def analyze_power_hr_windows(
    samples: Sequence[dict],
    parameters: Any,
    *,
    activity_id: str,
    start_time: str,
    modality: str,
    window_s: int = 480,
    step_s: int = 60,
    min_coverage: float = 0.90,
) -> dict:
    """Analyze rolling windows; only paired power/HR coverage rejects a full window.

    Power uses a 5%-trimmed mean, HR uses its median. Stability, cadence and
    zone dominance are reported as quality, never used to discard valid data.
    """
    if window_s < 1 or step_s < 1 or not 0 < min_coverage <= 1:
        raise ValueError("window/step must be positive and coverage must be in (0, 1]")

    diagnostics = {
        "candidate_windows": 0,
        "valid_windows": 0,
        "rejected_windows": 0,
        "rejected_by_reason": {
            "insufficient_samples": 0,
            "missing_power": 0,
            "missing_hr": 0,
            "missing_parameters": 0,
        },
        "coverage_failures": {"power": 0, "hr": 0},
        "quality_counts": {"HIGH": 0, "MEDIUM": 0, "LOW": 0},
        "active_runs": 0,
        "inactive_seconds": sum(not sample.get("active", False) for sample in samples),
    }
    points = []
    power_bounds = (
        [
            float(bound) * float(parameters.ftp_w)
            for bound in parameters.power_zone_fractions
        ]
        if parameters
        else []
    )
    hr_bounds = (
        [float(bound) for bound in parameters.hr_zone_bounds] if parameters else []
    )
    activity_start = datetime.fromisoformat(start_time.replace("Z", "+00:00"))

    runs: list[list[dict]] = []
    run: list[dict] = []
    previous = None
    for sample in sorted(samples, key=lambda row: row["elapsed_s"]):
        contiguous = (
            previous is not None
            and sample.get("active", False)
            and previous.get("active", False)
            and sample["elapsed_s"] == previous["elapsed_s"] + 1
            and sample.get("segment") == previous.get("segment")
        )
        if not sample.get("active", False) or (previous is not None and not contiguous):
            if run:
                runs.append(run)
            run = []
        if sample.get("active", False):
            run.append(sample)
        previous = sample
    if run:
        runs.append(run)
    diagnostics["active_runs"] = len(runs)

    min_coverage_count = ceil(window_s * min_coverage)

    for current in runs:
        for first in range(0, len(current), step_s):
            diagnostics["candidate_windows"] += 1
            last = first + window_s
            if last > len(current):
                diagnostics["rejected_windows"] += 1
                diagnostics["rejected_by_reason"]["insufficient_samples"] += 1
                continue

            window = current[first:last]
            powers = [
                float(row["power_w"]) for row in window if _valid(row.get("power_w"))
            ]
            heart_rates = [
                float(row["hr_bpm"]) for row in window if _valid(row.get("hr_bpm"))
            ]
            cadences = [
                float(row["cadence_rpm"])
                for row in window
                if _valid(row.get("cadence_rpm"))
            ]
            p_count = len(powers)
            h_count = len(heart_rates)
            p_coverage = 100 * p_count / window_s
            h_coverage = 100 * h_count / window_s
            power_missing = p_count < min_coverage_count
            hr_missing = h_count < min_coverage_count
            diagnostics["coverage_failures"]["power"] += int(power_missing)
            diagnostics["coverage_failures"]["hr"] += int(hr_missing)
            if power_missing or hr_missing:
                diagnostics["rejected_windows"] += 1
                reason = "missing_power" if power_missing else "missing_hr"
                diagnostics["rejected_by_reason"][reason] += 1
                continue
            if parameters is None:
                diagnostics["rejected_windows"] += 1
                diagnostics["rejected_by_reason"]["missing_parameters"] += 1
                continue
            ordered_power = sorted(powers)
            trim_count = int(len(ordered_power) * 0.05)
            trimmed_power = (
                ordered_power[trim_count : len(ordered_power) - trim_count]
                if trim_count
                else ordered_power
            )
            representative_power = mean(trimmed_power)
            representative_hr = median(heart_rates)
            representative_cadence = median(cadences) if cadences else None

            power_zone_counts = Counter(
                bisect_right(power_bounds, value) + 1 for value in powers
            )
            hr_zone_counts = Counter(
                bisect_right(hr_bounds, value) + 1 for value in heart_rates
            )
            power_dominance = 100 * max(power_zone_counts.values()) / len(powers)
            hr_dominance = 100 * max(hr_zone_counts.values()) / len(heart_rates)
            power_cv = _cv(powers)
            nonzero_power_cv = _cv([value for value in powers if value > 0])
            zero_power_pct = 100 * sum(value <= 0 for value in powers) / len(powers)
            cadence_coverage = 100 * len(cadences) / window_s
            isolated_zeros = sum(
                1
                for index in range(1, len(window) - 1)
                if _valid(window[index - 1].get("power_w"))
                and _valid(window[index].get("power_w"))
                and _valid(window[index + 1].get("power_w"))
                and float(window[index]["power_w"]) <= 0
                and float(window[index - 1]["power_w"]) > 0
                and float(window[index + 1]["power_w"]) > 0
            )
            zero_run_s = max_zero_run_s = 0
            for row in window:
                if _valid(row.get("power_w")) and float(row["power_w"]) <= 0:
                    zero_run_s += 1
                    max_zero_run_s = max(max_zero_run_s, zero_run_s)
                else:
                    zero_run_s = 0
            hr_start = [
                float(row["hr_bpm"]) for row in window[:60] if _valid(row.get("hr_bpm"))
            ]
            hr_end = [
                float(row["hr_bpm"])
                for row in window[-60:]
                if _valid(row.get("hr_bpm"))
            ]
            hr_slope = (
                (median(hr_end) - median(hr_start)) / max(1, (window_s - 60) / 60)
                if hr_start and hr_end
                else None
            )
            quality, quality_flags = _quality(
                100 * power_cv if power_cv is not None else None,
                zero_power_pct,
                representative_cadence,
                hr_dominance,
                power_dominance,
                cadence_coverage,
            )
            if isolated_zeros:
                quality_flags.append("isolated_zero_power_samples")
            diagnostics["valid_windows"] += 1
            diagnostics["quality_counts"][quality] += 1

            power_zone = bisect_right(power_bounds, representative_power) + 1
            hr_zone = bisect_right(hr_bounds, representative_hr) + 1
            timestamp = activity_start + timedelta(
                seconds=current[last - 1]["elapsed_s"] + 1
            )
            points.append(
                {
                    "activity_id": activity_id,
                    "timestamp": timestamp.isoformat(),
                    "modality": modality,
                    "start_s": current[first]["elapsed_s"],
                    "representative_power_w": round(representative_power, 1),
                    "representative_hr_bpm": round(representative_hr, 1),
                    "power_zone": power_zone,
                    "hr_zone": hr_zone,
                    "mismatch": power_zone - hr_zone,
                    "represented_seconds": step_s,
                    "quality": quality,
                    "quality_flags": quality_flags,
                    "power_coverage_pct": round(p_coverage, 1),
                    "hr_coverage_pct": round(h_coverage, 1),
                    "cadence_coverage_pct": round(cadence_coverage, 1),
                    "power_cv_pct": round(100 * power_cv, 2)
                    if power_cv is not None
                    else None,
                    "power_cv_nonzero_pct": round(100 * nonzero_power_cv, 2)
                    if nonzero_power_cv is not None
                    else None,
                    "zero_power_pct": round(zero_power_pct, 2),
                    "isolated_zero_power_pct": round(
                        100 * isolated_zeros / len(powers), 2
                    ),
                    "zero_power_max_run_s": max_zero_run_s,
                    "avg_cadence_rpm": round(representative_cadence, 1)
                    if representative_cadence is not None
                    else None,
                    "hr_slope_bpm_per_min": round(hr_slope, 2)
                    if hr_slope is not None
                    else None,
                    "power_zone_dominance_pct": round(power_dominance, 1),
                    "hr_zone_dominance_pct": round(hr_dominance, 1),
                }
            )

    return {"points": points, "diagnostics": diagnostics}
