"""Deterministic cycling analytics core package.

Exposes pure mathematical functions and typed structures for analyzing
1Hz normalized cycling time-series samples.
"""

from cycling.analytics.drift import aerobic_drift
from cycling.analytics.durability import calculate_aerobic_durability, durability
from cycling.analytics.load import calculate_session_load, training_load
from cycling.analytics.pipeline import analyze
from cycling.analytics.power import (
    calculate_if,
    calculate_np,
    power_curve,
    power_curve_detailed,
    threshold_estimate,
)
from cycling.analytics.rolling import (
    calculate_coverage,
    extract_runs,
    filter_active,
    rolling_windows,
)
from cycling.analytics.summaries import (
    calculate_distance,
    summarize_cadence,
    summarize_elevation,
    summarize_hr,
    summarize_power,
    summarize_speed,
)
from cycling.analytics.types import (
    AerobicDurabilityPoint,
    AerobicDurabilityResult,
    CadenceSummary,
    Coverage,
    CTLATLPoint,
    DriftResult,
    DriftSegment,
    DurabilityBucket,
    DurabilityPoint,
    DurabilityResult,
    ElevationSummary,
    FreshReference,
    HRSummary,
    LoadResult,
    MMPPoint,
    PowerSummary,
    SpeedSummary,
    ThreeZoneDistribution,
    ZoneDistribution,
    ZonesResult,
)
from cycling.analytics.zones import (
    calculate_hr_zone_distribution,
    calculate_zone_seconds,
)

__all__ = [
    # Main Pipeline & Legacy API
    "analyze",
    "power_curve",
    "power_curve_detailed",
    "durability",
    "calculate_aerobic_durability",
    "aerobic_drift",
    "threshold_estimate",
    "training_load",
    # Specific Functions
    "calculate_session_load",
    "calculate_zone_seconds",
    "calculate_hr_zone_distribution",
    "summarize_power",
    "summarize_hr",
    "summarize_cadence",
    "summarize_speed",
    "summarize_elevation",
    "calculate_distance",
    "rolling_windows",
    "calculate_np",
    "calculate_if",
    "filter_active",
    "extract_runs",
    "calculate_coverage",
    # Dataclasses
    "Coverage",
    "MMPPoint",
    "PowerSummary",
    "HRSummary",
    "CadenceSummary",
    "AerobicDurabilityPoint",
    "AerobicDurabilityResult",
    "SpeedSummary",
    "ElevationSummary",
    "ThreeZoneDistribution",
    "ZoneDistribution",
    "ZonesResult",
    "DriftSegment",
    "DriftResult",
    "LoadResult",
    "CTLATLPoint",
    "DurabilityBucket",
    "DurabilityPoint",
    "DurabilityResult",
    "FreshReference",
]
