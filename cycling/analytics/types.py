"""Typed dataclasses and structures for cycling analytics core."""

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class Coverage:
    """Sensor or active data coverage summary."""

    seconds: int
    fraction: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MMPPoint:
    """Mean Maximal Power point with exact start and end timestamps."""

    duration_s: int
    max_w: Optional[float]
    start_s: Optional[int] = None
    end_s: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PowerSummary:
    """Power metrics summary."""

    avg_w: Optional[float]
    np_w: Optional[float]
    work_kj: Optional[float]
    intensity_factor: Optional[float]
    coverage: Dict[str, Any]
    np_eligible_windows: int
    max_w: Optional[float] = None
    zero_power_seconds: int = 0
    coasting_pct: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class HRSummary:
    """Heart rate metrics summary."""

    avg_bpm: Optional[float]
    max_bpm: Optional[float]
    coverage: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CadenceSummary:
    """Cadence metrics summary."""

    avg_rpm: Optional[float]
    coverage: Dict[str, Any]
    max_rpm: Optional[float] = None
    target_range_seconds: int = 0
    target_range_pct: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SpeedSummary:
    """Speed metrics summary."""

    avg_m_s: Optional[float]
    max_m_s: Optional[float]
    avg_km_h: Optional[float]
    max_km_h: Optional[float]
    coverage: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ElevationSummary:
    """Elevation metrics summary."""

    gain_m: Optional[float]
    loss_m: Optional[float]
    coverage: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ThreeZoneDistribution:
    """Polarized 3-zone time distribution (Zone 1, Zone 2, Zone 3)."""

    basis: str  # "power", "hr", or "unavailable"
    seconds: List[int]
    unknown_seconds: int
    percentages: Optional[List[float]] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ZoneDistribution:
    """Multi-zone time distribution with boundary values."""

    seconds: List[int]
    bounds: Optional[List[float]]
    unknown_seconds: int

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ZonesResult:
    """Combined zone analytics for activity."""

    three_zone: Dict[str, Any]
    power: Dict[str, Any]
    hr: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DriftSegment:
    """Interval defining qualifying stable segment for drift calculation."""

    start_s: int
    end_s: int

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DriftResult:
    """Aerobic drift / decoupling analytical result."""

    available: bool
    reason: str
    segment: Optional[Dict[str, int]] = None
    duration_s: int = 0
    coverage: float = 0.0
    eligible_fraction: float = 0.0
    efficiency_first: Optional[float] = None
    efficiency_second: Optional[float] = None
    drift_percent: Optional[float] = None
    power_cv: Optional[float] = None
    confidence: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LoadResult:
    """Single session training load result adhering to priority hierarchy."""

    value: Optional[float]
    source: str  # "power", "hr", "rpe", "unavailable"
    unit: Optional[str]
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CTLATLPoint:
    """Single date point in CTL/ATL/TSB time-series recursive model."""

    date: str
    load: Optional[float]
    ctl: float
    atl: float
    tsb: float
    known: bool
    warning: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DurabilityBucket:
    """MMP decay performance within a specific prior cumulative work interval."""

    start_kj: float
    end_kj: Optional[float]
    exposure_kj: float
    exposure_seconds: int
    best_w: Dict[int, Optional[float]]
    change_percent: Dict[int, Optional[float]]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DurabilityResult:
    """Durability analysis across work exposure buckets."""

    available: bool
    reason: str
    buckets: List[Dict[str, Any]] = field(default_factory=list)
    confidence: Optional[str] = None
    total_work_kj: Optional[float] = None
    exposure_seconds: int = 0
    coverage: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
