"""Comprehensive activity analytics pipeline.

Formulas & Principles:
----------------------
Integrates all deterministic analytics modules into a unified snapshot:
1. General coverage & active vs paused seconds.
2. Power summary (NP, IF, work_kj, coverage).
3. HR & Cadence summaries.
4. Multi-zone and Polarized 3-Zone time distribution.
5. High-intensity interval detection (effort >= 105% FTP for >= 30s).
6. Aerobic drift / decoupling assessment.
7. Multi-tier training load determination.
8. Distance accumulation with reset tracking.
9. Heuristic FTP estimate (95% of 20-min MMP).
"""

import math
from typing import Any, Dict, List, Optional, Sequence

from cycling.analytics.drift import aerobic_drift
from cycling.analytics.load import calculate_session_load
from cycling.analytics.power import threshold_estimate
from cycling.analytics.rolling import extract_runs, filter_active
from cycling.analytics.summaries import (
    calculate_distance,
    summarize_cadence,
    summarize_elevation,
    summarize_hr,
    summarize_power,
    summarize_speed,
)
from cycling.analytics.zones import calculate_zone_seconds


def _build_interval(
    interval_samples: List[Dict[str, Any]], ftp_w: float
) -> Dict[str, Any]:
    start_s = interval_samples[0]["elapsed_s"]
    end_s = interval_samples[-1]["elapsed_s"] + 1
    duration = end_s - start_s

    powers = [
        float(s["power_w"])
        for s in interval_samples
        if s.get("power_w") is not None and not math.isnan(float(s["power_w"]))
    ]
    hrs = [
        float(s["hr_bpm"])
        for s in interval_samples
        if s.get("hr_bpm") is not None and not math.isnan(float(s["hr_bpm"]))
    ]
    cads = [
        float(s["cadence_rpm"])
        for s in interval_samples
        if s.get("cadence_rpm") is not None and not math.isnan(float(s["cadence_rpm"]))
    ]

    avg_p = sum(powers) / len(powers) if powers else None
    max_p = max(powers) if powers else None
    avg_hr = sum(hrs) / len(hrs) if hrs else None
    max_hr = max(hrs) if hrs else None
    avg_cad = sum(cads) / len(cads) if cads else None

    rel_p = (avg_p / ftp_w) if avg_p is not None and ftp_w and ftp_w > 0 else None
    confidence = len(powers) / len(interval_samples) if interval_samples else 0.0

    return {
        "start_s": start_s,
        "end_s": end_s,
        "seconds": duration,
        "duration_s": duration,
        "avg_power_w": round(avg_p, 2) if avg_p is not None else None,
        "max_power_w": round(max_p, 2) if max_p is not None else None,
        "avg_hr_bpm": round(avg_hr, 2) if avg_hr is not None else None,
        "max_hr_bpm": round(max_hr, 2) if max_hr is not None else None,
        "avg_cadence_rpm": round(avg_cad, 2) if avg_cad is not None else None,
        "relative_power": round(rel_p, 4) if rel_p is not None else None,
        "confidence": round(confidence, 4),
    }


def _detect_intervals(
    samples: Sequence[Dict[str, Any]], ftp_w: float
) -> List[Dict[str, Any]]:
    """Detect high-intensity intervals (power >= 105% FTP for at least 30s)."""
    intervals: List[Dict[str, Any]] = []
    for run in extract_runs(samples, "power_w"):
        buf: List[Dict[str, Any]] = []
        for s in run:
            if float(s["power_w"]) >= 1.05 * ftp_w:
                buf.append(s)
            else:
                if len(buf) >= 30:
                    intervals.append(_build_interval(buf, ftp_w))
                buf = []
        if len(buf) >= 30:
            intervals.append(_build_interval(buf, ftp_w))
    return intervals


def analyze(
    samples: Sequence[Dict[str, Any]],
    parameters: Optional[Any] = None,
    rpe: Optional[float] = None,
) -> Dict[str, Any]:
    """Execute complete deterministic analytics pipeline on normalized 1Hz samples."""
    active = filter_active(samples)
    active_seconds = len(active)
    elapsed_seconds = max((s["elapsed_s"] for s in samples), default=-1) + 1

    power_summary = summarize_power(samples, parameters).to_dict()
    hr_summary = summarize_hr(samples).to_dict()
    cadence_summary = summarize_cadence(samples, parameters).to_dict()
    speed_summary = summarize_speed(samples).to_dict()
    elevation_summary = summarize_elevation(samples).to_dict()

    zones_result = calculate_zone_seconds(samples, parameters).to_dict()

    ftp = getattr(parameters, "ftp_w", None) if parameters else None
    intervals = _detect_intervals(samples, ftp) if ftp and ftp > 0 else []

    drift_result = aerobic_drift(samples, parameters)
    load_result = calculate_session_load(samples, parameters, rpe)
    distance_m, distance_resets = calculate_distance(samples)
    th_estimate = threshold_estimate(samples)

    return {
        "general": {
            "active_seconds": active_seconds,
            "elapsed_seconds": elapsed_seconds,
            "coverage": {
                "active_fraction": (
                    active_seconds / elapsed_seconds if elapsed_seconds > 0 else 0.0
                ),
                "power": power_summary["coverage"],
                "hr": hr_summary["coverage"],
                "cadence": cadence_summary["coverage"],
                "speed": speed_summary["coverage"],
                "elevation": elevation_summary["coverage"],
            },
            "paused_seconds": len(samples) - active_seconds,
            "uncovered_seconds": elapsed_seconds - len(samples),
            "distance_m": distance_m,
            "elevation_gain_m": elevation_summary["gain_m"],
            "elevation_loss_m": elevation_summary["loss_m"],
            "speed_avg_m_s": speed_summary["avg_m_s"],
            "speed_max_m_s": speed_summary["max_m_s"],
        },
        "power": power_summary,
        "hr": hr_summary,
        "cadence": cadence_summary,
        "speed": speed_summary,
        "elevation": elevation_summary,
        "zones": zones_result,
        "intervals": intervals,
        "drift": drift_result,
        "load": load_result,
        "distance_m": distance_m,
        "distance_resets": distance_resets,
        "threshold_estimate": th_estimate,
    }
