"""Multi-tier Training Load hierarchy and CTL/ATL/TSB recursive model.

Formulas & Principles:
----------------------
1. Multi-Tier Load Priority Hierarchy:
   No summing across streams! Strictly evaluates sources in priority order:

   Tier 1: Power Load (TSS-equivalent)
   - Condition: Athlete FTP declared AND power coverage >= min_coverage (default 90%).
   - Formula: Load_power = (active_seconds / 3600) * (NP / FTP)^2 * 100

   Tier 2: HR Load (Approximate stress points)
   - Condition: Athlete LTHR declared AND HR coverage >= min_coverage (default 90%).
   - Formula: Load_HR = (1 / 3600) * sum_{i in valid HR} (HR_i / LTHR)^2 * 100

   Tier 3: Session RPE (sRPE)
   - Condition: Declared session RPE is provided.
   - Formula: Load_RPE = (active_seconds / 60) * RPE

   Tier 4: Unavailable
   - Returned when no tier conditions are satisfied.

2. CTL / ATL / TSB Recursive Model (EWMA):
   Given daily loads for date range [start, end]:
   - ctl_days (tau_CTL, default 42)
   - atl_days (tau_ATL, default 7)

   For each date t:
   - effective_load = daily_load[t] if date is present else 0.0
   - prior_ctl = CTL_{t-1}
   - prior_atl = ATL_{t-1}
   - CTL_t = prior_ctl + (effective_load - prior_ctl) / ctl_days
   - ATL_t = prior_atl + (effective_load - prior_atl) / atl_days
   - TSB_t = prior_ctl - prior_atl
"""

import math
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Sequence

from cycling.analytics.power import calculate_np
from cycling.analytics.rolling import _is_valid_value, calculate_coverage, filter_active
from cycling.analytics.types import CTLATLPoint, LoadResult


def _is_finite_number(val: Any) -> bool:
    if val is None:
        return False
    if isinstance(val, (int, float)):
        return math.isfinite(val)
    return False


def calculate_session_load(
    samples: Sequence[Dict[str, Any]],
    parameters: Optional[Any] = None,
    rpe: Optional[float] = None,
    min_coverage: float = 0.90,
) -> Dict[str, Any]:
    """Calculate single activity training load using the multi-tier hierarchy."""
    active = filter_active(samples)
    active_seconds = len(active)

    power_cov = calculate_coverage(samples, "power_w", active_seconds)
    hr_cov = calculate_coverage(samples, "hr_bpm", active_seconds)

    ftp = getattr(parameters, "ftp_w", None) if parameters else None
    lthr = getattr(parameters, "lthr_bpm", None) if parameters else None

    if not _is_finite_number(ftp):
        ftp = None
    if not _is_finite_number(lthr):
        lthr = None
    hr_load_factor = getattr(parameters, "hr_load_factor", 1.0) if parameters else 1.0
    if not _is_finite_number(hr_load_factor) or hr_load_factor <= 0:
        hr_load_factor = 1.0
    if not _is_finite_number(rpe):
        rpe = None

    # Tier 1: Power Load
    if ftp is not None and ftp > 0 and power_cov.fraction >= min_coverage:
        np_w, _ = calculate_np(samples)
        if np_w is not None:
            val = (active_seconds / 3600.0) * ((np_w / ftp) ** 2) * 100.0
            return LoadResult(
                value=val,
                source="power",
                unit="arbitrary stress points",
                reason=f"power coverage >= {int(min_coverage * 100)}%",
            ).to_dict()

    # Tier 2: HR Load
    if lthr is not None and lthr > 0 and hr_cov.fraction >= min_coverage:
        valid_hrs = [
            float(s["hr_bpm"])
            for s in active
            if _is_valid_value(s.get("hr_bpm")) and math.isfinite(float(s["hr_bpm"]))
        ]
        if valid_hrs:
            val = (
                (active_seconds / 3600.0)
                * (sum((h / lthr) ** 2 for h in valid_hrs) / len(valid_hrs))
                * 100.0
                * hr_load_factor
            )
            return LoadResult(
                value=val,
                source="hr",
                unit="approximate stress points",
                reason=(
                    f"HR coverage >= {int(min_coverage * 100)}%; "
                    f"calibrated factor={hr_load_factor:.3f}; approximate, not TRIMP"
                ),
            ).to_dict()

    # Tier 3: RPE Load
    if rpe is not None and rpe >= 0:
        val = (active_seconds / 60.0) * float(rpe)
        return LoadResult(
            value=val,
            source="rpe",
            unit="arbitrary units",
            reason="declared session RPE fallback",
        ).to_dict()

    # Tier 4: Unavailable
    return LoadResult(
        value=None,
        source="unavailable",
        unit=None,
        reason="power, HR and RPE load evidence insufficient",
    ).to_dict()


def training_load(
    daily_load: Dict[str, Optional[float]],
    start: date,
    end: date,
    ctl_days: int = 42,
    atl_days: int = 7,
) -> List[Dict[str, Any]]:
    """Calculate CTL/ATL/TSB time series using exponentially weighted moving averages."""
    if ctl_days < 1 or atl_days < 1:
        raise ValueError("ctl_days and atl_days must be at least 1")
    if end < start:
        raise ValueError("end must not precede start")

    ctl = 0.0
    atl = 0.0
    rows: List[Dict[str, Any]] = []
    day = start

    while day <= end:
        date_str = day.isoformat()
        prior_ctl = ctl
        prior_atl = atl

        known = date_str in daily_load and daily_load[date_str] is not None
        load_val = float(daily_load[date_str]) if known else None
        effective_load = load_val if known else 0.0

        ctl += (effective_load - ctl) / ctl_days
        atl += (effective_load - atl) / atl_days

        tsb = prior_ctl - prior_atl
        warning = "warm-up; initial state is zero" if len(rows) < ctl_days else None

        pt = CTLATLPoint(
            date=date_str,
            load=load_val,
            ctl=ctl,
            atl=atl,
            tsb=tsb,
            known=known,
            warning=warning,
        )
        rows.append(pt.to_dict())

        day += timedelta(days=1)

    return rows
