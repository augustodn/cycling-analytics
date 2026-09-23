"""Validated settings and semantic tool contracts; units are explicit."""

from datetime import date
from itertools import pairwise
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class AthleteParameters(Contract):
    effective_date: date = date(2026, 1, 1)
    ftp_w: float = Field(default=285, gt=0, le=1000)
    lthr_bpm: float = Field(default=155, gt=0, le=240)
    hr_load_factor: float = Field(default=1.0, gt=0, le=5)
    max_hr_bpm: float = Field(default=181, gt=0, le=250)
    weight_kg: float = Field(default=75, gt=0, le=300)
    power_zone_fractions: list[float] = [0.55, 0.75, 0.90, 1.05, 1.20]
    hr_zone_bounds: list[float] = [127, 141, 147, 158]
    power_three_zone_fractions: list[float] = [0.80, 1.0]
    hr_three_zone_fractions: list[float] = [0.87, 1.0]
    ctl_days: float = Field(default=42, ge=1, le=365)
    atl_days: float = Field(default=7, ge=1, le=365)
    notes: str = "Profile defaults; declared, not measured historical thresholds"

    @model_validator(mode="after")
    def valid_bounds(self):
        for name in (
            "power_zone_fractions",
            "hr_zone_bounds",
            "power_three_zone_fractions",
            "hr_three_zone_fractions",
        ):
            bounds = getattr(self, name)
            if (
                not bounds
                or any(x <= 0 for x in bounds)
                or any(a >= b for a, b in pairwise(bounds))
            ):
                raise ValueError(f"{name} must be positive and strictly increasing")
        if (
            len(self.power_three_zone_fractions) != 2
            or len(self.hr_three_zone_fractions) != 2
        ):
            raise ValueError("three-zone models require exactly two boundaries")
        if self.lthr_bpm > self.max_hr_bpm:
            raise ValueError("LTHR must not exceed maximum HR")
        return self


class ActivityRequest(Contract):
    activity_id: str = Field(min_length=1, max_length=128)
    parameter_mode: Literal["historical", "current"] = "historical"
    rpe: float | None = Field(default=None, ge=0, le=10)


class StreamRequest(ActivityRequest):
    start_s: int = Field(default=0, ge=0)
    end_s: int | None = Field(default=None, ge=0)
    max_points: int = Field(default=2000, ge=2, le=20000)

    @model_validator(mode="after")
    def ordered(self):
        if self.end_s is not None and self.end_s < self.start_s:
            raise ValueError("end_s must not precede start_s")
        return self


class ActivityContext(Contract):
    rpe: float | None = Field(default=None, ge=0, le=10)
    modality: Literal["road", "mtb", "gravel", "indoor", "unknown"] | None = None

    @model_validator(mode="after")
    def nonempty(self):
        if self.rpe is None and self.modality is None:
            raise ValueError("Provide RPE or modality")
        return self


class SetContextRequest(Contract):
    activity_id: str = Field(min_length=1, max_length=128)
    rpe: float | None = Field(default=None, ge=0, le=10)
    modality: Literal["road", "mtb", "gravel", "indoor", "unknown"] | None = None

    @model_validator(mode="after")
    def nonempty(self):
        if self.rpe is None and self.modality is None:
            raise ValueError("Provide RPE or modality")
        return self


class CurveRequest(ActivityRequest):
    durations: list[int] = Field(
        default=[
            5,
            30,
            60,
            300,
            600,
            900,
            1200,
            1800,
            2700,
            3600,
            4500,
            5400,
            6300,
            7200,
            9000,
            10800,
            12600,
            14400,
            16200,
            18000,
            19800,
            21600,
        ],
        min_length=1,
        max_length=30,
    )

    @model_validator(mode="after")
    def positive_durations(self):
        if any(d < 1 or d > 86400 for d in self.durations):
            raise ValueError("durations must be between 1 and 86400 seconds")
        return self


class PeriodPowerCurveRequest(Contract):
    period: Literal["7d", "21d", "30d", "90d", "365d", "all", "custom"] = "all"
    modality: Literal["indoor", "road", "mtb", "gravel", "unknown", "all"] = "all"
    durations: list[int] = Field(
        default=[
            5,
            30,
            60,
            300,
            600,
            900,
            1200,
            1800,
            2700,
            3600,
            4500,
            5400,
            6300,
            7200,
            9000,
            10800,
            12600,
            14400,
            16200,
            18000,
            19800,
            21600,
        ],
        min_length=1,
        max_length=30,
    )
    start_date: date | None = None
    end_date: date | None = None

    @model_validator(mode="after")
    def positive_durations(self):
        if any(d < 1 or d > 86400 for d in self.durations):
            raise ValueError("durations must be between 1 and 86400 seconds")
        if self.period == "custom" and (
            self.start_date is None or self.end_date is None
        ):
            raise ValueError("custom period requires start_date and end_date")
        if (
            self.start_date is not None
            and self.end_date is not None
            and self.end_date < self.start_date
        ):
            raise ValueError("end_date must not precede start_date")
        return self


class PeriodHRDistributionRequest(Contract):
    period: Literal["7d", "21d", "30d", "90d", "365d", "all", "custom"] = "all"
    modality: Literal["indoor", "road", "mtb", "gravel", "unknown", "all"] = "all"
    parameter_mode: Literal["historical", "current"] = "historical"
    start_date: date | None = None
    end_date: date | None = None

    @model_validator(mode="after")
    def ordered_dates(self):
        if self.period == "custom" and (
            self.start_date is None or self.end_date is None
        ):
            raise ValueError("custom period requires start_date and end_date")
        if (
            self.start_date is not None
            and self.end_date is not None
            and self.end_date < self.start_date
        ):
            raise ValueError("end_date must not precede start_date")
        return self


class ProgressRequest(Contract):
    period: Literal["7d", "21d", "30d", "90d", "365d", "all", "custom"] = "all"
    modality: Literal["indoor", "road", "mtb", "gravel", "unknown", "all"] = "all"
    parameter_mode: Literal["historical", "current"] = "historical"
    start_date: date | None = None
    end_date: date | None = None
    compare_previous: bool = False
    durations: list[int] = Field(
        default=[300, 1200, 3600, 7200, 10800],
        min_length=1,
        max_length=30,
    )
    fatigue_thresholds_kj: list[float] = Field(
        default=[1000.0, 1500.0, 1800.0],
        min_length=1,
        max_length=20,
    )

    @model_validator(mode="after")
    def ordered_dates(self):
        if any(d < 1 or d > 86400 for d in self.durations):
            raise ValueError("durations must be between 1 and 86400 seconds")
        if any(t < 0 for t in self.fatigue_thresholds_kj):
            raise ValueError("fatigue_thresholds_kj must be non-negative")
        if any(a >= b for a, b in pairwise(self.fatigue_thresholds_kj)):
            raise ValueError("fatigue_thresholds_kj must be strictly increasing")
        if self.period == "custom" and (
            self.start_date is None or self.end_date is None
        ):
            raise ValueError("custom period requires start_date and end_date")
        if (
            self.start_date is not None
            and self.end_date is not None
            and self.end_date < self.start_date
        ):
            raise ValueError("end_date must not precede start_date")
        return self


class DurabilityRequest(CurveRequest):
    thresholds_kj: list[float] = Field(
        default=[1000.0, 1500.0, 1800.0, 2100.0, 2200.0],
        min_length=1,
        max_length=20,
    )
    durations: list[int] = Field(
        default=[300, 1200, 1800, 3600],
        min_length=1,
        max_length=30,
    )
    bucket_kj: list[float] | None = None

    @model_validator(mode="after")
    def ordered_thresholds(self):
        if self.bucket_kj is not None:
            self.thresholds_kj = [value for value in self.bucket_kj if value > 0]
        if not self.thresholds_kj or any(t < 0 for t in self.thresholds_kj):
            raise ValueError("thresholds_kj must contain non-negative values")
        if any(a >= b for a, b in pairwise(self.thresholds_kj)):
            raise ValueError("thresholds_kj must be strictly increasing")
        return self


class LoadRequest(Contract):
    start: date
    end: date
    modality: str = Field(min_length=1)
    parameter_mode: Literal["historical", "current"] = "historical"
    ctl_days: float | None = Field(default=None, ge=1, le=365)
    atl_days: float | None = Field(default=None, ge=1, le=365)
    basis: Literal["time", "sessions", "load"] = "time"

    @model_validator(mode="after")
    def ordered_dates(self):
        if self.end < self.start or (self.end - self.start).days > 3660:
            raise ValueError("date range must be ordered and at most ten years")
        return self


class ComparisonRequest(Contract):
    activity_ids: list[str] = Field(min_length=2, max_length=20)
    parameter_mode: Literal["historical", "current"] = "historical"
    allow_mixed: bool = False


class ToolResult(Contract):
    operation: str
    algorithm_version: str
    parameter_id: str | None = None
    parameter_mode: str | None = None
    computed_at: str
    data: dict[str, Any]
