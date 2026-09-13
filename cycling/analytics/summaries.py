"""Summaries for Power, HR, Cadence, and Distance tracking.

Formulas:
---------
1. Power Summary:
   - avg_w = sum(P_i) / N_power for valid active power samples.
   - np_w = Normalized Power over complete contiguous 30s windows.
   - work_kj = sum(P_i) / 1000 (since 1 watt = 1 joule/second).
     IMPORTANT: If no power samples are present (no-power activity), work_kj is None!
   - intensity_factor = np_w / FTP if parameters are present.

2. HR Summary:
   - avg_bpm = sum(HR_i) / N_hr for valid active HR samples.
   - max_bpm = max(HR_i) for valid active HR samples.

3. Cadence Summary:
   - avg_rpm = sum(Cadence_i) / N_cadence for valid active cadence samples.

4. Distance Tracking:
   - Accumulates forward positive distance deltas (d_i - d_{i-1} > 0).
   - Counts resets when d_i < d_{i-1}.
"""

from statistics import mean
from typing import Any, Dict, List, Optional, Sequence, Tuple

from cycling.analytics.power import calculate_if, calculate_np
from cycling.analytics.rolling import _is_valid_value, calculate_coverage, filter_active
from cycling.analytics.types import (
    CadenceSummary,
    ElevationSummary,
    HRSummary,
    PowerSummary,
    SpeedSummary,
)


def summarize_power(
    samples: Sequence[Dict[str, Any]], parameters: Optional[Any] = None
) -> PowerSummary:
    """Summarize power metrics for activity."""
    active = filter_active(samples)
    active_seconds = len(active)
    powers = [float(s["power_w"]) for s in active if _is_valid_value(s.get("power_w"))]
    cov = calculate_coverage(samples, "power_w", active_seconds)

    if not powers:
        return PowerSummary(
            avg_w=None,
            np_w=None,
            work_kj=None,
            intensity_factor=None,
            coverage=cov.to_dict(),
            np_eligible_windows=0,
            max_w=None,
            zero_power_seconds=0,
            coasting_pct=None,
        )

    avg_w = mean(powers)
    max_w = float(max(powers))
    zero_power_seconds = sum(1 for p in powers if p == 0.0)
    coasting_pct = (zero_power_seconds / len(powers)) * 100.0

    np_w, np_windows = calculate_np(samples)
    work_kj = sum(powers) / 1000.0
    ftp = getattr(parameters, "ftp_w", None) if parameters else None
    if_val = calculate_if(np_w, ftp)

    return PowerSummary(
        avg_w=avg_w,
        np_w=np_w,
        work_kj=work_kj,
        intensity_factor=if_val,
        coverage=cov.to_dict(),
        np_eligible_windows=np_windows,
        max_w=max_w,
        zero_power_seconds=zero_power_seconds,
        coasting_pct=coasting_pct,
    )


def summarize_hr(samples: Sequence[Dict[str, Any]]) -> HRSummary:
    """Summarize heart rate metrics for activity."""
    active = filter_active(samples)
    active_seconds = len(active)
    hr_vals = [float(s["hr_bpm"]) for s in active if _is_valid_value(s.get("hr_bpm"))]
    cov = calculate_coverage(samples, "hr_bpm", active_seconds)

    if not hr_vals:
        return HRSummary(avg_bpm=None, max_bpm=None, coverage=cov.to_dict())

    return HRSummary(
        avg_bpm=mean(hr_vals),
        max_bpm=max(hr_vals),
        coverage=cov.to_dict(),
    )


def summarize_cadence(
    samples: Sequence[Dict[str, Any]],
    parameters: Optional[Any] = None,
    target_range: Tuple[float, float] = (90.0, 100.0),
) -> CadenceSummary:
    """Summarize cadence metrics for activity including target cadence range (default 90-100 rpm)."""
    active = filter_active(samples)
    active_seconds = len(active)
    cadence_vals = [
        float(s["cadence_rpm"]) for s in active if _is_valid_value(s.get("cadence_rpm"))
    ]
    cov = calculate_coverage(samples, "cadence_rpm", active_seconds)

    low, high = (
        getattr(parameters, "cadence_target_range", None)
        or getattr(parameters, "cadence_range", None)
        or target_range
    )

    if not cadence_vals:
        return CadenceSummary(
            avg_rpm=None,
            coverage=cov.to_dict(),
            max_rpm=None,
            target_range_seconds=0,
            target_range_pct=None,
        )

    avg_rpm = mean(cadence_vals)
    max_rpm = float(max(cadence_vals))
    range_seconds = sum(1 for c in cadence_vals if low <= c <= high)
    range_pct = (range_seconds / len(cadence_vals)) * 100.0

    return CadenceSummary(
        avg_rpm=avg_rpm,
        coverage=cov.to_dict(),
        max_rpm=max_rpm,
        target_range_seconds=range_seconds,
        target_range_pct=range_pct,
    )


def summarize_speed(samples: Sequence[Dict[str, Any]]) -> SpeedSummary:
    """Summarize speed metrics (m/s and km/h) for activity."""
    active = filter_active(samples)
    active_seconds = len(active)

    speeds = [
        float(s["speed_mps"])
        for s in active
        if _is_valid_value(s.get("speed_mps")) and float(s["speed_mps"]) >= 0
    ]

    if not speeds:
        prev_dist: Optional[float] = None
        prev_time: Optional[int] = None
        derived_speeds: List[float] = []
        for s in active:
            dist = s.get("distance_m")
            time = s.get("elapsed_s")
            if _is_valid_value(dist) and _is_valid_value(time):
                cur_dist = float(dist)
                cur_time = int(time)
                if prev_dist is not None and prev_time is not None:
                    dt = cur_time - prev_time
                    dd = cur_dist - prev_dist
                    if dt > 0 and dd >= 0:
                        derived_speeds.append(dd / dt)
                prev_dist = cur_dist
                prev_time = cur_time
        if derived_speeds:
            speeds = derived_speeds

    cov = calculate_coverage(samples, "speed_mps", active_seconds)
    if not speeds and not cov.seconds:
        cov = calculate_coverage(samples, "distance_m", active_seconds)

    if not speeds:
        return SpeedSummary(
            avg_m_s=None,
            max_m_s=None,
            avg_km_h=None,
            max_km_h=None,
            coverage=cov.to_dict(),
        )

    avg_m_s = mean(speeds)
    max_m_s = float(max(speeds))
    return SpeedSummary(
        avg_m_s=avg_m_s,
        max_m_s=max_m_s,
        avg_km_h=avg_m_s * 3.6,
        max_km_h=max_m_s * 3.6,
        coverage=cov.to_dict(),
    )


def summarize_elevation(samples: Sequence[Dict[str, Any]]) -> ElevationSummary:
    """Calculate elevation gain and loss from altitude using finite forward differences."""
    active = filter_active(samples)
    active_seconds = len(active)
    cov = calculate_coverage(samples, "altitude_m", active_seconds)

    gain_m = 0.0
    loss_m = 0.0
    has_altitude = False

    prev_alt: Optional[float] = None
    prev_time: Optional[int] = None
    prev_dist: Optional[float] = None

    for s in active:
        alt = s.get("altitude_m")
        if not _is_valid_value(alt):
            prev_alt = None
            prev_time = None
            prev_dist = None
            continue

        cur_alt = float(alt)
        cur_time = int(s["elapsed_s"]) if _is_valid_value(s.get("elapsed_s")) else None
        cur_dist = (
            float(s["distance_m"]) if _is_valid_value(s.get("distance_m")) else None
        )
        has_altitude = True

        if prev_alt is not None:
            dist_reset = (
                cur_dist is not None and prev_dist is not None and cur_dist < prev_dist
            )
            time_gap = (
                cur_time is not None
                and prev_time is not None
                and cur_time - prev_time > 10
            )
            diff = cur_alt - prev_alt
            outlier = abs(diff) > 100.0

            if not (dist_reset or time_gap or outlier):
                if diff > 0:
                    gain_m += diff
                elif diff < 0:
                    loss_m += abs(diff)

        prev_alt = cur_alt
        prev_time = cur_time
        prev_dist = cur_dist

    if not has_altitude:
        return ElevationSummary(gain_m=None, loss_m=None, coverage=cov.to_dict())

    return ElevationSummary(
        gain_m=gain_m,
        loss_m=loss_m,
        coverage=cov.to_dict(),
    )


def calculate_distance(
    samples: Sequence[Dict[str, Any]],
) -> Tuple[Optional[float], int]:
    """Calculate cumulative forward distance and count distance resets."""
    active = filter_active(samples)
    total_dist = 0.0
    resets = 0
    previous: Optional[float] = None
    has_distance = False

    for sample in active:
        current = sample.get("distance_m")
        if not _is_valid_value(current):
            previous = None
            continue

        current_val = float(current)
        has_distance = True

        if previous is not None:
            if current_val < previous:
                resets += 1
            else:
                total_dist += current_val - previous
        previous = current_val

    return (total_dist if has_distance else None), resets
