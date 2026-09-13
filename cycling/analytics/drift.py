"""Aerobic drift and efficiency factor analytics over qualifying stable endurance segments.

Formulas & Principles:
----------------------
1. Qualifying Segment Identification:
   A sub-sequence of contiguous active samples is considered eligible if:
   - HR is valid and > 0 for all samples in the segment.
   - Power is valid and strictly within [min_ftp_frac * FTP, max_ftp_frac * FTP] (default 50%-80% FTP).
   - Duration is at least min_duration_s (default 1200 seconds / 20 minutes).
   - Power Coefficient of Variation (CV = standard_deviation / mean) <= max_cv (default 0.15).

2. Aerobic Efficiency Factor (EF):
   For a given sub-segment (first half H1 or second half H2):
   EF = Mean_Power / Mean_HR

3. Aerobic Drift Percentage:
   Drift % = 100 * (EF_H1 - EF_H2) / EF_H1
   - Positive drift indicates cardiac drift (HR rising relative to power, or power falling relative to HR).
   - Negative drift indicates increasing efficiency.
"""

from statistics import mean, pstdev
from typing import Any, Dict, List, Optional, Sequence

from cycling.analytics.rolling import _is_valid_value, extract_runs, filter_active
from cycling.analytics.types import DriftResult


def aerobic_drift(
    samples: Sequence[Dict[str, Any]],
    parameters: Optional[Any],
    min_duration_s: int = 1200,
    min_ftp_frac: float = 0.50,
    max_ftp_frac: float = 0.80,
    max_cv: float = 0.15,
) -> Dict[str, Any]:
    """Calculate aerobic drift / decoupling over qualifying stable sub-segments."""
    if parameters is None or getattr(parameters, "ftp_w", None) is None:
        return DriftResult(
            available=False,
            reason="declared athlete parameters with valid FTP required",
        ).to_dict()

    ftp = parameters.ftp_w
    if ftp <= 0:
        return DriftResult(
            available=False, reason="FTP must be a positive number"
        ).to_dict()

    candidates: List[List[Dict[str, Any]]] = []

    for run in extract_runs(samples, "power_w"):
        paired: List[Dict[str, Any]] = []
        previous: Optional[Dict[str, Any]] = None

        for sample in run:
            hr = sample.get("hr_bpm")
            p = sample.get("power_w")

            valid = (
                _is_valid_value(hr)
                and float(hr) > 0
                and _is_valid_value(p)
                and (min_ftp_frac * ftp <= float(p) <= max_ftp_frac * ftp)
            )

            contiguous = (
                previous is not None
                and sample["elapsed_s"] == previous["elapsed_s"] + 1
            )

            if valid and contiguous:
                paired.append(sample)
            else:
                if len(paired) >= min_duration_s:
                    candidates.append(paired)
                paired = [sample] if valid else []

            previous = sample

        if len(paired) >= min_duration_s:
            candidates.append(paired)

    stable_candidates = []
    for run in candidates:
        powers = [float(s["power_w"]) for s in run]
        avg_p = mean(powers)
        if avg_p > 0:
            cv = pstdev(powers) / avg_p
            if cv <= max_cv:
                stable_candidates.append(run)

    if not stable_candidates:
        return DriftResult(
            available=False,
            reason=f"no qualifying stable {min_duration_s // 60}-minute segment in {int(min_ftp_frac * 100)}-{int(max_ftp_frac * 100)}% FTP range",
        ).to_dict()

    chosen_run = max(stable_candidates, key=len)
    half = len(chosen_run) // 2
    first, second = chosen_run[:half], chosen_run[-half:]

    p_first = [float(s["power_w"]) for s in first]
    hr_first = [float(s["hr_bpm"]) for s in first]
    p_second = [float(s["power_w"]) for s in second]
    hr_second = [float(s["hr_bpm"]) for s in second]

    ef1 = mean(p_first) / mean(hr_first)
    ef2 = mean(p_second) / mean(hr_second)
    drift_pct = 100.0 * (ef1 - ef2) / ef1
    run_powers = [float(s["power_w"]) for s in chosen_run]
    overall_cv = pstdev(run_powers) / mean(run_powers)

    active = filter_active(samples)
    eligible_frac = len(chosen_run) / max(1, len(active))

    return DriftResult(
        available=True,
        reason="qualifying stable segment identified; terrain, heat, hydration and HR lag confound the observation",
        segment={
            "start_s": chosen_run[0]["elapsed_s"],
            "end_s": chosen_run[-1]["elapsed_s"] + 1,
        },
        duration_s=len(chosen_run),
        coverage=1.0,
        eligible_fraction=eligible_frac,
        efficiency_first=ef1,
        efficiency_second=ef2,
        drift_percent=drift_pct,
        power_cv=overall_cv,
        confidence="low",
    ).to_dict()
