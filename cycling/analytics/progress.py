"""Progress analytics for longitudinal trends across activities and time buckets."""

from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Sequence

from cycling.analytics.durability import (
    _best_window,
    _intervals,
    _paired_runs,
    _window_metrics,
    _window_prefixes,
    calculate_aerobic_durability,
)
from cycling.analytics.rolling import _is_valid_value, filter_active


def calculate_weekly_composition(
    activities_with_metrics: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Group analyzed activities into ISO week buckets with time, load, distribution, and longest ride."""
    weeks: Dict[str, Dict[str, Any]] = {}

    for item in sorted(
        activities_with_metrics, key=lambda x: x["activity"]["start_time"]
    ):
        activity = item["activity"]
        metrics = item.get("metrics") or {}
        start_time_str = activity["start_time"]
        act_date = date.fromisoformat(start_time_str[:10])

        iso_year, iso_week, iso_weekday = act_date.isocalendar()
        week_key = f"{iso_year}-W{iso_week:02d}"

        if week_key not in weeks:
            monday = act_date - timedelta(days=iso_weekday - 1)
            sunday = monday + timedelta(days=6)
            weeks[week_key] = {
                "iso_week": week_key,
                "start_date": monday.isoformat(),
                "end_date": sunday.isoformat(),
                "activity_count": 0,
                "total_seconds": 0,
                "total_hours": 0.0,
                "total_load": 0.0,
                "load_sources": {"power": 0, "hr": 0, "rpe": 0, "unavailable": 0},
                "three_zone_seconds": [0, 0, 0],
                "three_zone_percentages": [0.0, 0.0, 0.0],
                "longest_ride": None,
                "sensor_bases": set(),
                "unknown_seconds": 0,
                "caveats": [],
            }

        wdata = weeks[week_key]
        wdata["activity_count"] += 1

        duration_s = float(activity.get("duration_s") or 0)
        wdata["total_seconds"] += int(duration_s)

        # Load calculation
        load_info = metrics.get("load") or {}
        load_val = load_info.get("value")
        source = load_info.get("source", "unavailable")
        wdata["load_sources"][source] = wdata["load_sources"].get(source, 0) + 1
        if load_val is not None:
            wdata["total_load"] += float(load_val)

        # Zones calculation
        zones_info = metrics.get("zones") or {}
        three_z = zones_info.get("three_zone") or {}
        tz_secs = list(three_z.get("seconds") or [0, 0, 0])[:3]
        tz_secs.extend([0] * (3 - len(tz_secs)))
        wdata["three_zone_seconds"] = [
            a + b for a, b in zip(wdata["three_zone_seconds"], tz_secs, strict=False)
        ]
        basis = three_z.get("basis")
        if basis and basis != "unavailable":
            wdata["sensor_bases"].add(basis)
        wdata["unknown_seconds"] += three_z.get("unknown_seconds", 0)

        # Longest ride tracking
        if (
            wdata["longest_ride"] is None
            or duration_s > wdata["longest_ride"]["duration_s"]
        ):
            wdata["longest_ride"] = {
                "activity_id": activity["id"],
                "start_time": activity["start_time"],
                "duration_s": duration_s,
                "modality": activity.get("modality", "unknown"),
            }

    result = []
    for week_key in sorted(weeks.keys()):
        wdata = weeks[week_key]
        tot_sec = wdata["three_zone_seconds"]
        tot_time = sum(tot_sec)
        wdata["total_hours"] = round(wdata["total_seconds"] / 3600.0, 2)
        wdata["total_load"] = round(wdata["total_load"], 1)
        wdata["three_zone_percentages"] = [
            round(100.0 * s / tot_time, 1) if tot_time > 0 else 0.0 for s in tot_sec
        ]
        wdata["sensor_bases"] = sorted(list(wdata["sensor_bases"]))

        caveats = []
        if len(wdata["sensor_bases"]) > 1:
            caveats.append(
                f"Mixed zone bases in week ({', '.join(wdata['sensor_bases'])})"
            )
        if wdata["load_sources"].get("unavailable", 0) > 0:
            caveats.append(
                f"{wdata['load_sources']['unavailable']} activity load(s) unavailable; treated as zero in weekly load"
            )
        if wdata["unknown_seconds"] > 0:
            caveats.append(
                f"{wdata['unknown_seconds']}s active time with unclassified/unknown sensor data"
            )
        wdata["caveats"] = caveats
        result.append(wdata)

    return result


def calculate_fatigued_pdc_for_activity(
    samples: Sequence[Dict[str, Any]],
    durations: Sequence[int],
    fatigue_thresholds_kj: Sequence[float],
    activity_duration_s: Optional[float] = None,
) -> Dict[str, Any]:
    """Calculate maximal power for requested durations after accumulated work thresholds in a single activity."""
    runs, total_work_j, _, _, _ = _intervals(samples, activity_duration_s)
    total_work_kj = total_work_j / 1000.0

    watts_by_threshold: Dict[str, Dict[str, Optional[float]]] = {}
    for threshold_kj in fatigue_thresholds_kj:
        threshold_watts: Dict[str, Optional[float]] = {}
        if total_work_kj < threshold_kj or not runs:
            for d in durations:
                threshold_watts[str(d)] = None
        else:
            for d in durations:
                best = _best_window(runs, d, minimum_start_work_j=threshold_kj * 1000.0)
                threshold_watts[str(d)] = best["power_w"] if best else None
        watts_by_threshold[str(threshold_kj)] = threshold_watts

    return {
        "total_work_kj": total_work_kj,
        "watts_after_thresholds": watts_by_threshold,
    }


def calculate_fixed_hr_ef_trend_point(
    activity: Dict[str, Any],
    samples: Sequence[Dict[str, Any]],
    hr_targets_bpm: Optional[Sequence[float]] = None,
    window_duration_s: int = 600,
    max_hr_diff_bpm: float = 5.0,
    max_power_cv: float = 0.15,
) -> Dict[str, Any]:
    """Calculate fixed-HR window power/EF targets and z2 stable window for a single activity.

    Uses contiguous active paired power+HR samples to find stable windows matching target HRs.
    """
    modality = activity.get("modality", "unknown")
    active = filter_active(samples)
    active_samples = len(active)
    powers = [float(s["power_w"]) for s in active if _is_valid_value(s.get("power_w"))]
    hrs = [float(s["hr_bpm"]) for s in active if _is_valid_value(s.get("hr_bpm"))]

    valid_power_samples = len(powers)
    valid_hr_samples = len(hrs)

    avg_p = (sum(powers) / valid_power_samples) if valid_power_samples > 0 else None
    avg_h = (sum(hrs) / valid_hr_samples) if valid_hr_samples > 0 else None
    ef = (
        (avg_p / avg_h)
        if (avg_p is not None and avg_h is not None and avg_h > 0)
        else None
    )

    aerobic_res = calculate_aerobic_durability(
        samples, activity_duration_s=activity.get("duration_s")
    )
    baseline = aerobic_res.get("baseline", {}) if aerobic_res.get("available") else {}

    quality_flags = []
    if valid_power_samples == 0:
        quality_flags.append("missing_power")
    if valid_hr_samples == 0:
        quality_flags.append("missing_hr")
    if baseline.get("quality_flags"):
        quality_flags.extend(baseline["quality_flags"])

    # Fixed HR bands aggregation over contiguous paired intervals
    targets = (
        [130.0, 135.0, 140.0]
        if hr_targets_bpm is None
        else [float(t) for t in hr_targets_bpm]
    )
    target_results: Dict[str, Optional[Dict[str, Any]]] = {}

    runs, _, _, _, _ = _intervals(samples, activity.get("duration_s"))
    paired_runs = _paired_runs(runs)

    for target_hr in targets:
        best_window = None
        best_diff = float("inf")
        if valid_power_samples > 0 and valid_hr_samples > 0:
            for run in paired_runs:
                ends, prefixes = _window_prefixes(run)
                for index, _interval in enumerate(run):
                    metrics = _window_metrics(
                        run, index, window_duration_s, ends, prefixes
                    )
                    if metrics is None:
                        continue
                    if (
                        metrics["power_cv"] > max_power_cv
                        or metrics["coasting_pct"] > 10.0
                    ):
                        continue
                    hr_diff = abs(metrics["avg_hr"] - target_hr)
                    if hr_diff <= max_hr_diff_bpm and hr_diff < best_diff:
                        best_diff = hr_diff
                        best_window = metrics

        if best_window is not None:
            target_results[str(int(target_hr))] = {
                "target_hr_bpm": target_hr,
                "power_w": best_window["avg_power"],
                "hr_bpm": best_window["avg_hr"],
                "ef": best_window["efficiency_factor"],
                "window_duration_s": window_duration_s,
                "power_cv": best_window["power_cv"],
                "sample_count": window_duration_s,
            }
        else:
            target_results[str(int(target_hr))] = None

    available = any(value is not None for value in target_results.values())
    reason = None
    if not available:
        if valid_power_samples == 0 and valid_hr_samples == 0:
            reason = "Power and heart-rate sensors both missing or invalid"
        elif valid_power_samples == 0:
            reason = "Power sensor missing or invalid"
        elif valid_hr_samples == 0:
            reason = "Heart-rate sensor missing or invalid"
        else:
            reason = "No stable contiguous window matched a fixed heart-rate target"

    return {
        "activity_id": activity["id"],
        "date": activity["start_time"][:10],
        "modality": modality,
        "is_indoor": modality == "indoor",
        "active_samples": active_samples,
        "valid_power_samples": valid_power_samples,
        "valid_hr_samples": valid_hr_samples,
        "avg_power_w": avg_p,
        "avg_hr_bpm": avg_h,
        "ef": ef,
        "fixed_hr_targets": target_results,
        "z2_stable_window": {
            "ef": baseline.get("efficiency_factor"),
            "power_w": baseline.get("avg_power"),
            "hr_bpm": baseline.get("avg_hr"),
            "power_cv": baseline.get("power_cv"),
            "confidence": baseline.get("confidence"),
        }
        if baseline.get("efficiency_factor") is not None
        else None,
        "quality_flags": sorted(set(quality_flags)),
        "available": available,
        "unavailable_reason": reason,
    }
