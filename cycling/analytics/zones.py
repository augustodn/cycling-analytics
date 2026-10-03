"""Zone time distribution: Multi-zone power/HR and Polarized 3-Zone model.

Formulas:
---------
1. Polarized 3-Zone Distribution:
   - Zone 1 (Low / Aerobic): Value < bound_1
   - Zone 2 (Threshold / Heavy): bound_1 <= Value < bound_2
   - Zone 3 (High / Severe): Value >= bound_2

   Basis precedence:
   - Primary basis is "power" if any active power sample exists.
   - Secondary basis is "hr" if no power exists but active HR sample exists.
   - Basis is "unavailable" if neither exists or parameters are missing.

   Bounds by basis:
   - Power: bound_1 = pf[0] * FTP, bound_2 = pf[1] * FTP (default fractions: [0.75, 1.0])
   - HR: bound_1 = hf[0] * LTHR, bound_2 = hf[1] * LTHR (default fractions: [0.82, 1.0])

2. Multi-Zone Power Distribution:
   Configurable fractions of FTP (e.g. [0.55, 0.75, 0.90, 1.05, 1.20]).
   Bins: [0, f0*FTP), [f0*FTP, f1*FTP), ..., [f_{n-1}*FTP, inf)

3. Multi-Zone HR Distribution:
   Configurable bpm bounds (e.g. [130, 145, 158, 170]).
   Bins: [0, b0), [b0, b1), ..., [b_{n-1}, inf)
"""

from typing import Any, Dict, Optional, Sequence

from cycling.analytics.rolling import _is_valid_value, filter_active
from cycling.analytics.types import (
    ZonesResult,
)

HR_ZONE_DEFINITIONS = (
    ("1 Recovery", "0–81%", "0–126 bpm", 0, 126),
    ("2 Aerobic", "82–89%", "127–139 bpm", 127, 139),
    ("3 Tempo", "90–93%", "140–145 bpm", 140, 145),
    ("4 SubThreshold", "94–99%", "146–154 bpm", 146, 154),
    ("5a Threshold", "100–102%", "155–159 bpm", 155, 159),
    ("5b Aerobic Capacity", "103–106%", "160–165 bpm", 160, 165),
    ("5c Anaerobic", "107%+", "166 bpm+", 166, None),
)


def calculate_hr_zone_distribution(
    samples: Sequence[Dict[str, Any]],
    parameters: Optional[Any] = None,
) -> Dict[str, Any]:
    """Calculate HR-zone time; optional athlete bounds override legacy display zones."""
    personalized = parameters is not None
    if not personalized:
        definitions = HR_ZONE_DEFINITIONS
    else:
        bounds = list(parameters.hr_zone_bounds)
        definitions = tuple(
            (
                f"HR Z{index + 1}",
                "Athlete configured",
                (f"< {upper:g} bpm" if index == 0 else f"{lower:g}–< {upper:g} bpm")
                if upper is not None
                else f"≥ {lower:g} bpm",
                lower,
                upper,
            )
            for index, (lower, upper) in enumerate(
                zip([0, *bounds], [*bounds, None], strict=True)
            )
        )
    seconds = [0] * len(definitions)
    unknown_seconds = 0

    for sample in filter_active(samples):
        value = sample.get("hr_bpm")
        if not _is_valid_value(value):
            unknown_seconds += 1
            continue

        hr_bpm = float(value)
        zone_index = next(
            (
                index
                for index, (_, _, _, lower, upper) in enumerate(definitions)
                if hr_bpm >= lower
                and (
                    upper is None
                    or (hr_bpm < upper if personalized else hr_bpm <= upper)
                )
            ),
            None,
        )
        if zone_index is None:
            unknown_seconds += 1
        else:
            seconds[zone_index] += 1

    total_seconds = sum(seconds)
    percentages = [
        (100.0 * value / total_seconds) if total_seconds else 0.0 for value in seconds
    ]
    return {
        "basis": "hr",
        "seconds": seconds,
        "percentages": percentages,
        "total_seconds": total_seconds,
        "unknown_seconds": unknown_seconds,
        "zones": [
            {
                "label": label,
                "percentage_range": percentage_range,
                "hr_range": hr_range,
            }
            for label, percentage_range, hr_range, _, _ in definitions
        ],
    }


def calculate_zone_seconds(
    samples: Sequence[Dict[str, Any]], parameters: Optional[Any]
) -> ZonesResult:
    """Calculate multi-zone and 3-zone time distributions."""
    active = filter_active(samples)

    if parameters is None:
        return ZonesResult(
            three_zone={
                "basis": "unavailable",
                "seconds": [0, 0, 0],
                "percentages": [0.0, 0.0, 0.0],
                "unknown_seconds": len(active),
            },
            power={
                "seconds": [],
                "bounds": None,
                "unknown_seconds": len(active),
            },
            hr={
                "seconds": [],
                "bounds": None,
                "unknown_seconds": len(active),
            },
        )

    power_3zone = [0, 0, 0]
    hr_3zone = [0, 0, 0]

    pf = getattr(parameters, "power_three_zone_fractions", [0.75, 1.0])
    hf = getattr(parameters, "hr_three_zone_fractions", [0.82, 1.0])

    power_fractions = getattr(
        parameters, "power_zone_fractions", [0.55, 0.75, 0.90, 1.05, 1.20]
    )
    hr_bounds = getattr(parameters, "hr_zone_bounds", [130, 145, 158, 170])

    ftp = parameters.ftp_w
    lthr = parameters.lthr_bpm

    full_power = [0] * (len(power_fractions) + 1)
    full_hr = [0] * (len(hr_bounds) + 1)

    unknown_power = 0
    unknown_hr = 0

    for s in active:
        p = s.get("power_w")
        h = s.get("hr_bpm")

        if not _is_valid_value(p):
            unknown_power += 1
        else:
            p_val = float(p)
            z3_idx = 0 if p_val < pf[0] * ftp else (1 if p_val < pf[1] * ftp else 2)
            power_3zone[z3_idx] += 1

            bin_idx = next(
                (i for i, bound in enumerate(power_fractions) if p_val < bound * ftp),
                len(power_fractions),
            )
            full_power[bin_idx] += 1

        if not _is_valid_value(h):
            unknown_hr += 1
        else:
            h_val = float(h)
            z3_idx = 0 if h_val < hf[0] * lthr else (1 if h_val < hf[1] * lthr else 2)
            hr_3zone[z3_idx] += 1

            bin_idx = next(
                (i for i, bound in enumerate(hr_bounds) if h_val < bound),
                len(hr_bounds),
            )
            full_hr[bin_idx] += 1

    total_power_known = sum(power_3zone)
    total_hr_known = sum(hr_3zone)

    if total_power_known > 0:
        basis = "power"
        tz_seconds = power_3zone
        tz_unknown = unknown_power
    elif total_hr_known > 0:
        basis = "hr"
        tz_seconds = hr_3zone
        tz_unknown = unknown_hr
    else:
        basis = "unavailable"
        tz_seconds = [0, 0, 0]
        tz_unknown = len(active)

    tz_total = sum(tz_seconds)
    tz_percentages = [
        (100.0 * s / tz_total) if tz_total > 0 else 0.0 for s in tz_seconds
    ]

    return ZonesResult(
        three_zone={
            "basis": basis,
            "seconds": tz_seconds,
            "percentages": tz_percentages,
            "unknown_seconds": tz_unknown,
        },
        power={
            "seconds": full_power,
            "bounds": [f * ftp for f in power_fractions],
            "unknown_seconds": unknown_power,
        },
        hr={
            "seconds": full_hr,
            "bounds": list(hr_bounds),
            "unknown_seconds": unknown_hr,
        },
    )
