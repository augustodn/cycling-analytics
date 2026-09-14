"""Semantic tools shared by CLI, HTTP and dashboard adapters."""

from datetime import UTC, date, datetime, timedelta
from math import ceil
from typing import Any

from cycling import ALGORITHM_VERSION, analytics
from cycling.analytics.durability import calculate_fresh_reference_single
from cycling.analytics.zones import HR_ZONE_DEFINITIONS
from cycling.models import (
    ActivityContext,
    ActivityRequest,
    AthleteParameters,
    ComparisonRequest,
    CurveRequest,
    DurabilityRequest,
    LoadRequest,
    PeriodHRDistributionRequest,
    PeriodPowerCurveRequest,
    StreamRequest,
    ToolResult,
)
from cycling.storage import Store, encode, now


def _filter_period_activities(
    activities: list[dict[str, Any]],
    modality: str,
    period: str,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[dict[str, Any]]:
    selected = [
        activity
        for activity in activities
        if modality == "all" or activity.get("modality", "unknown") == modality
    ]
    if not selected:
        return []

    activity_dates = {
        activity["id"]: date.fromisoformat(activity["start_time"][:10])
        for activity in selected
    }
    if period == "custom":
        if start_date is None or end_date is None:
            raise ValueError("custom period requires start_date and end_date")
        return [
            activity
            for activity in selected
            if start_date <= activity_dates[activity["id"]] <= end_date
        ]

    if period == "all":
        return selected

    ref_date = end_date or max(activity_dates.values())
    cutoff = ref_date - timedelta(days={"30d": 30, "90d": 90, "365d": 365}[period])
    return [
        activity
        for activity in selected
        if cutoff <= activity_dates[activity["id"]] <= ref_date
    ]


class CyclingService:
    def __init__(self, store: Store):
        self.store = store

    def result(self, operation, data, parameter_id=None, parameter_mode=None):
        return ToolResult(
            operation=operation,
            algorithm_version=ALGORITHM_VERSION,
            parameter_id=parameter_id,
            parameter_mode=parameter_mode,
            computed_at=now(),
            data=data,
        )

    def settings(self, request: ActivityRequest):
        activity = self.store.activity(request.activity_id)
        when = (
            datetime.now(UTC).date()
            if request.parameter_mode == "current"
            else date.fromisoformat(activity["start_time"][:10])
        )
        ident, parameters = self.store.parameters(when, mode=request.parameter_mode)
        return activity, ident, parameters

    def list_activities(self):
        return self.result("activities", {"activities": self.store.activities()})

    def get_activity(self, request: ActivityRequest):
        activity, ident, parameters = self.settings(request)
        return self.result(
            "activity",
            {
                "activity": activity,
                "declared_parameters": parameters.model_dump(mode="json")
                if parameters
                else None,
            },
            ident,
            request.parameter_mode,
        )

    def analyze_activity(self, request: ActivityRequest, force=False):
        activity, ident, parameters = self.settings(request)
        key = encode(
            [
                ALGORITHM_VERSION,
                activity["normalizer_version"],
                activity["sample_path"],
                ident,
                request.parameter_mode,
                activity.get("context_id"),
                request.rpe,
            ]
        )
        cached = None if force else self.store.cached(request.activity_id, key)
        if cached:
            return ToolResult.model_validate(cached)
        rpe = request.rpe if request.rpe is not None else activity.get("rpe")
        metrics = analytics.analyze(
            self.store.samples(request.activity_id), parameters, rpe
        )
        result = self.result(
            "analyze_activity",
            {
                "activity": activity,
                "declared_parameters": parameters.model_dump(mode="json")
                if parameters
                else None,
                "metrics": metrics,
            },
            ident,
            request.parameter_mode,
        )
        self.store.save_metrics(
            request.activity_id, key, result.model_dump(mode="json")
        )
        return result

    def stream(self, request: StreamRequest):
        rows = [
            r
            for r in self.store.samples(request.activity_id)
            if r["elapsed_s"] >= request.start_s
            and (request.end_s is None or r["elapsed_s"] <= request.end_s)
        ]
        stride = max(1, ceil(len(rows) / request.max_points))
        sampled = rows[::stride]
        # Display decimation only; never feed this back into domain analytics.
        return self.result(
            "stream",
            {
                "samples": sampled,
                "original_points": len(rows),
                "returned_points": len(sampled),
                "downsampling": "stride; peaks may be omitted",
            },
        )

    def power_curve(self, request: CurveRequest):
        activity = self.store.activity(request.activity_id)
        curve = analytics.power_curve(
            self.store.samples(request.activity_id), request.durations
        )
        return self.result(
            "power_curve",
            {
                "activity_id": request.activity_id,
                "normalizer_version": activity["normalizer_version"],
                "watts": {str(duration): value for duration, value in curve.items()},
                "durations_s": request.durations,
                "quality_flags": activity.get("quality_flags", []),
                "reason": "Best observed complete contiguous efforts; null means no eligible window",
            },
        )

    def period_power_curve(self, request: PeriodPowerCurveRequest):
        activities = _filter_period_activities(
            self.store.activities(),
            request.modality,
            request.period,
            request.start_date,
            request.end_date,
        )

        best_watts: dict[int, float | None] = {d: None for d in request.durations}
        best_records: dict[int, dict[str, Any] | None] = {
            d: None for d in request.durations
        }

        for activity in activities:
            curve = analytics.power_curve(
                self.store.samples(activity["id"]), request.durations
            )
            for d in request.durations:
                w = curve.get(d)
                if w is not None:
                    if best_watts[d] is None or w > best_watts[d]:
                        best_watts[d] = w
                        best_records[d] = {
                            "activity_id": activity["id"],
                            "start_time": activity.get("start_time"),
                        }

        return self.result(
            "period_power_curve",
            {
                "period": request.period,
                "modality": request.modality,
                "durations_s": request.durations,
                "watts": {str(d): best_watts[d] for d in request.durations},
                "records": {str(d): best_records[d] for d in request.durations},
                "activities_evaluated": len(activities),
            },
        )

    def durability(self, request: DurabilityRequest):
        activity = self.store.activity(request.activity_id)
        samples = self.store.samples(request.activity_id)

        # Fresh references come from other activities in the preceding 90 days.
        # Passing an explicit empty mapping prevents analytics from silently
        # falling back to the same activity as its own baseline.
        historical_fresh_refs: dict[int, dict[str, Any]] = {}
        act_date_str = activity.get("start_time")
        if act_date_str:
            act_date = date.fromisoformat(act_date_str[:10])
            recent_acts = _filter_period_activities(
                self.store.activities(),
                activity.get("modality", "unknown"),
                "custom",
                act_date - timedelta(days=90),
                act_date,
            )
            for duration_s in request.durations:
                best_hist = None
                for candidate in recent_acts:
                    if candidate["id"] == request.activity_id:
                        continue
                    reference = calculate_fresh_reference_single(
                        self.store.samples(candidate["id"]),
                        duration_s,
                        activity_id=candidate["id"],
                        activity_date=candidate.get("start_time"),
                        activity_duration_s=candidate.get("duration_s"),
                    )
                    if reference and (
                        best_hist is None or reference["power_w"] > best_hist["power_w"]
                    ):
                        best_hist = {**reference, "source": "historical_90d"}
                if best_hist is not None:
                    historical_fresh_refs[duration_s] = best_hist

        data = analytics.durability(
            samples,
            durations=request.durations,
            thresholds_kj=request.thresholds_kj,
            historical_fresh_references=historical_fresh_refs,
            activity_id=request.activity_id,
            activity_date=activity.get("start_time"),
            activity_duration_s=activity.get("duration_s"),
        )
        return self.result(
            "durability",
            {
                **data,
                "activity_id": request.activity_id,
                "durations_s": request.durations,
                "thresholds_kj": request.thresholds_kj,
                "reference_window_days": 90,
                "normalizer_version": activity["normalizer_version"],
                "quality_flags": activity.get("quality_flags", []),
            },
        )

    def aerobic_durability(self, request: ActivityRequest):
        activity = self.store.activity(request.activity_id)
        data = analytics.calculate_aerobic_durability(
            self.store.samples(request.activity_id),
            activity_duration_s=activity.get("duration_s"),
        )
        return self.result(
            "aerobic_durability",
            {
                **data,
                "activity_id": request.activity_id,
                "normalizer_version": activity["normalizer_version"],
                "quality_flags": activity.get("quality_flags", []),
            },
        )

    def drift(self, request: ActivityRequest):
        _, ident, parameters = self.settings(request)
        return self.result(
            "drift",
            analytics.aerobic_drift(
                self.store.samples(request.activity_id), parameters
            ),
            ident,
            request.parameter_mode,
        )

    def hr_distribution(self, request: ActivityRequest):
        _, ident, _ = self.settings(request)
        distribution = analytics.calculate_hr_zone_distribution(
            self.store.samples(request.activity_id)
        )
        return self.result(
            "hr_distribution",
            {
                "activity_id": request.activity_id,
                **distribution,
            },
            parameter_id=ident,
            parameter_mode=request.parameter_mode,
        )

    def period_hr_distribution(self, request: PeriodHRDistributionRequest):
        activities = _filter_period_activities(
            self.store.activities(),
            request.modality,
            request.period,
            request.start_date,
            request.end_date,
        )

        num_zones = len(HR_ZONE_DEFINITIONS)
        accumulated_seconds = [0] * num_zones
        total_unknown = 0
        matching_activities = []

        for activity in activities:
            distribution = analytics.calculate_hr_zone_distribution(
                self.store.samples(activity["id"])
            )
            accumulated_seconds = [
                a + b
                for a, b in zip(
                    accumulated_seconds, distribution["seconds"], strict=True
                )
            ]
            total_unknown += distribution["unknown_seconds"]
            matching_activities.append(activity["id"])

        total_seconds = sum(accumulated_seconds)
        percentages = [
            (100.0 * s / total_seconds) if total_seconds > 0 else 0.0
            for s in accumulated_seconds
        ]
        return self.result(
            "period_hr_distribution",
            {
                "period": request.period,
                "modality": request.modality,
                "activity_count": len(matching_activities),
                "basis": "hr",
                "seconds": accumulated_seconds,
                "percentages": percentages,
                "total_seconds": total_seconds,
                "unknown_seconds": total_unknown,
                "zones": [
                    {
                        "label": label,
                        "percentage_range": percentage_range,
                        "hr_range": hr_range,
                    }
                    for label, percentage_range, hr_range, _, _ in HR_ZONE_DEFINITIONS
                ],
                "activity_ids": matching_activities,
            },
            parameter_mode=request.parameter_mode,
        )

    def thresholds(self, request: ActivityRequest):
        _, ident, parameters = self.settings(request)
        curve = analytics.power_curve(self.store.samples(request.activity_id), [1200])
        return self.result(
            "thresholds",
            {
                "declared_ftp_w": parameters.ftp_w if parameters else None,
                "observed_20_minute_curve": curve,
                "estimate": analytics.threshold_estimate(
                    self.store.samples(request.activity_id)
                ),
                "warning": "Observed-effort heuristic, not measured FTP; declared settings unchanged",
            },
            ident,
            request.parameter_mode,
        )

    def compare(self, request: ComparisonRequest):
        activities = [self.store.activity(i) for i in request.activity_ids]
        modalities = {a.get("modality", "unknown") for a in activities}
        sports = {a.get("sport", "unknown") for a in activities}
        if not request.allow_mixed and (
            len(modalities) != 1
            or "unknown" in modalities
            or len(sports) != 1
            or "unknown" in sports
        ):
            raise ValueError(
                "Comparison requires same known sport/modality; set allow_mixed explicitly to override"
            )
        results = [
            self.analyze_activity(
                ActivityRequest(activity_id=i, parameter_mode=request.parameter_mode)
            ).model_dump(mode="json")
            for i in request.activity_ids
        ]
        return self.result(
            "compare",
            {
                "activities": results,
                "modalities": sorted(modalities),
                "mixed_or_unknown_allowed": request.allow_mixed,
            },
        )

    def load(self, request: LoadRequest):
        settings_date = (
            datetime.now(UTC).date()
            if request.parameter_mode == "current"
            else request.end
        )
        load_parameter_id, settings = self.store.parameters(settings_date)
        ctl_days = request.ctl_days or (settings.ctl_days if settings else 42)
        atl_days = request.atl_days or (settings.atl_days if settings else 7)
        activities = [
            a
            for a in self.store.activities()
            if (
                request.modality == "all"
                or a.get("modality", "unknown") == request.modality
            )
            and date.fromisoformat(a["start_time"][:10]) <= request.end
        ]
        daily, sources, unavailable, entries = {}, {}, [], []
        distribution = [0.0, 0.0, 0.0]
        sensor_bases = set()
        unknown_seconds = 0
        for activity in activities:
            day = activity["start_time"][:10]
            response = self.analyze_activity(
                ActivityRequest(
                    activity_id=activity["id"], parameter_mode=request.parameter_mode
                )
            )
            metric = response.data["metrics"]
            load = metric["load"]
            if load.get("value") is not None:
                daily[day] = daily.get(day, 0) + load["value"]
            if date.fromisoformat(day) < request.start:
                continue
            source = load.get("source", "unavailable")
            sources[source] = sources.get(source, 0) + 1
            entries.append(
                {
                    "activity_id": activity["id"],
                    "day": day,
                    "load": load,
                    "parameter_id": response.parameter_id,
                }
            )
            if load.get("value") is None:
                unavailable.append(activity["id"])
            zones = metric["zones"]["three_zone"]
            values = zones["seconds"]
            sensor_bases.add(zones["basis"])
            unknown_seconds += zones.get("unknown_seconds", 0)
            if sum(values):
                if request.basis == "sessions":
                    distribution[values.index(max(values))] += 1
                elif request.basis == "load":
                    if load.get("value") is not None:
                        distribution = [
                            a + load["value"] * v / sum(values)
                            for a, v in zip(distribution, values, strict=True)
                        ]
                else:
                    distribution = [
                        a + v for a, v in zip(distribution, values, strict=True)
                    ]
        first = min(
            [request.start]
            + [date.fromisoformat(a["start_time"][:10]) for a in activities]
        )
        days = analytics.training_load(daily, first, request.end, ctl_days, atl_days)
        visible = [d for d in days if str(d["date"]) >= str(request.start)]
        return self.result(
            "load",
            {
                "days": visible,
                "activities": entries,
                "sources": sources,
                "unavailable_activity_ids": unavailable,
                "modality": request.modality,
                "ctl_days": ctl_days,
                "atl_days": atl_days,
                "time_constant_settings_date": str(settings_date),
                "initialization_date": str(first),
                "distribution": {
                    "basis": request.basis,
                    "values": distribution,
                    "sensor_bases": sorted(sensor_bases),
                    "unknown_seconds": unknown_seconds,
                },
                "warnings": [
                    f"Zero-initialized load; allow at least {ctl_days:g} days of warm-up",
                    "Unrecorded days and unavailable activity loads count as zero, not proven rest",
                    "Power stress, approximate HR stress and RPE units are not interchangeable; mixed sources reduce comparability",
                    "Unknown modality is an unclassified group, not evidence of modality equivalence",
                    "Session zones use dominant time; load distribution allocates load by zone time",
                ],
            },
            parameter_id=load_parameter_id,
            parameter_mode=request.parameter_mode,
        )

    def status(self):
        return self.result("status", self.store.status())

    def set_context(self, activity_id: str, context: ActivityContext):
        context_id = self.store.set_context(activity_id, context)
        return self.result(
            "set_context",
            {"activity_id": activity_id, "context_id": context_id},
        )

    def add_parameters(self, parameters: AthleteParameters):
        param_id = self.store.add_parameters(parameters)
        return self.result(
            "add_parameters",
            {"parameter_id": param_id},
        )

    def list_parameters(self):
        return self.result(
            "list_parameters",
            {"parameters": self.store.parameter_history()},
        )

    # Semantic integration aliases
    def get_activity_stream(self, request: StreamRequest):
        return self.stream(request)

    def get_hr_drift(self, request: ActivityRequest):
        return self.drift(request)

    def get_aerobic_efficiency(self, request: ActivityRequest):
        return self.drift(request)

    def get_training_load(self, request: LoadRequest):
        return self.load(request)

    def get_training_distribution(self, request: LoadRequest):
        return self.load(request)

    def estimate_thresholds(self, request: ActivityRequest):
        return self.thresholds(request)
