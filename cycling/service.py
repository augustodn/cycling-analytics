"""Semantic tools shared by CLI, HTTP and dashboard adapters."""

from datetime import UTC, date, datetime, timedelta
from math import ceil
from typing import Any

from cycling import ALGORITHM_VERSION, analytics
from cycling.analytics.durability import calculate_fresh_references
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
    ProgressRequest,
    StreamRequest,
    ToolResult,
)
from cycling.storage import Store, encode, now

PERIOD_DAYS = {"7d": 7, "21d": 21, "30d": 30, "90d": 90, "365d": 365}


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
    cutoff = ref_date - timedelta(days=PERIOD_DAYS[period])
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

    def latest_activity_with_power_and_hr(self, minimum_duration_s: int = 5400):
        """Return newest long activity containing both sensor streams."""
        if minimum_duration_s < 1:
            raise ValueError("minimum_duration_s must be positive")

        for activity in self.store.activities():
            try:
                duration_s = float(activity.get("duration_s") or 0)
            except (TypeError, ValueError):
                continue
            if duration_s < minimum_duration_s:
                continue

            try:
                samples = self.store.samples(activity["id"])
            except FileNotFoundError:
                continue
            active_seconds = len(analytics.filter_active(samples))
            power = analytics.calculate_coverage(samples, "power_w", active_seconds)
            heart_rate = analytics.calculate_coverage(samples, "hr_bpm", active_seconds)
            if power.seconds > 0 and heart_rate.seconds > 0:
                return activity
        return None

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

    def analyze_activity(
        self,
        request: ActivityRequest,
        force=False,
        samples: list[dict[str, Any]] | None = None,
    ):
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
        samples_data = (
            samples if samples is not None else self.store.samples(request.activity_id)
        )
        metrics = analytics.analyze(samples_data, parameters, rpe)
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
            best_historical = {duration_s: None for duration_s in request.durations}
            for candidate in recent_acts:
                if candidate["id"] == request.activity_id:
                    continue
                if "missing_power_w" in candidate.get("quality_flags", []):
                    continue
                candidate_samples = self.store.samples(candidate["id"])
                references = calculate_fresh_references(
                    candidate_samples,
                    request.durations,
                    activity_id=candidate["id"],
                    activity_date=candidate.get("start_time"),
                    activity_duration_s=candidate.get("duration_s"),
                )
                for duration_s, reference in references.items():
                    best_hist = best_historical[duration_s]
                    if best_hist is None or reference["power_w"] > best_hist["power_w"]:
                        best_historical[duration_s] = {
                            **reference,
                            "source": "historical_90d",
                        }
            historical_fresh_refs = {
                duration_s: reference
                for duration_s, reference in best_historical.items()
                if reference is not None
            }

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

    def weekly_cycling_training(self, end_date: date, weeks: int = 12):
        """Aggregate weekly cycling time and fixed HR-zone exposure."""
        if weeks < 1:
            raise ValueError("weeks must be at least one")

        current_week = end_date - timedelta(days=end_date.weekday())
        first_week = current_week - timedelta(days=7 * (weeks - 1))
        zone_definitions = analytics.calculate_hr_zone_distribution([])["zones"]
        weekly = {
            week_start: {
                "week_start": week_start.isoformat(),
                "iso_week": f"{week_start.isocalendar().year}-W{week_start.isocalendar().week:02d}",
                "activity_count": 0,
                "total_seconds": 0.0,
                "hr_zone_seconds": [0] * len(zone_definitions),
                "unclassified_seconds": 0.0,
            }
            for week_start in (
                first_week + timedelta(days=7 * index) for index in range(weeks)
            )
        }

        cycling_modalities = {"indoor", "road", "mtb", "gravel"}
        for activity in self.store.activities():
            if (
                activity.get("sport") != "cycling"
                and activity.get("modality", "unknown") not in cycling_modalities
            ):
                continue

            activity_date = date.fromisoformat(activity["start_time"][:10])
            week_start = activity_date - timedelta(days=activity_date.weekday())
            if week_start not in weekly or activity_date > end_date:
                continue

            distribution = analytics.calculate_hr_zone_distribution(
                self.store.samples(activity["id"])
            )
            zone_seconds = distribution["seconds"]
            known_hr_seconds = sum(zone_seconds)
            sampled_seconds = known_hr_seconds + distribution["unknown_seconds"]
            activity_seconds = max(
                float(activity.get("duration_s") or 0), sampled_seconds
            )

            bucket = weekly[week_start]
            bucket["activity_count"] += 1
            bucket["total_seconds"] += activity_seconds
            bucket["hr_zone_seconds"] = [
                total + seconds
                for total, seconds in zip(
                    bucket["hr_zone_seconds"], zone_seconds, strict=True
                )
            ]
            bucket["unclassified_seconds"] += activity_seconds - known_hr_seconds

        return self.result(
            "weekly_cycling_training",
            {
                "start_date": first_week.isoformat(),
                "end_date": end_date.isoformat(),
                "weeks": list(weekly.values()),
                "zones": zone_definitions,
            },
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

    def _progress_fresh_references(
        self,
        activities: list[dict[str, Any]],
        durations: list[int],
        sample_cache: dict[str, list[dict[str, Any]]] | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Calculate each candidate's fresh reference once for a progress request."""
        if not activities:
            return {}

        target_dates = [date.fromisoformat(a["start_time"][:10]) for a in activities]
        lower = min(target_dates) - timedelta(days=90)
        upper = max(target_dates)
        candidates = [
            activity
            for activity in self.store.activities()
            if lower <= date.fromisoformat(activity["start_time"][:10]) <= upper
        ]

        references: dict[str, dict[str, Any]] = {}
        min_dur = min(durations) if durations else None
        for candidate in candidates:
            if "missing_power_w" in candidate.get("quality_flags", []):
                continue
            cand_dur = candidate.get("duration_s")
            if cand_dur is not None and min_dur is not None and cand_dur < min_dur:
                continue
            cand_id = candidate["id"]
            if sample_cache is not None and cand_id in sample_cache:
                candidate_samples = sample_cache[cand_id]
            else:
                candidate_samples = self.store.samples(
                    cand_id,
                    columns=("timestamp", "elapsed_s", "segment", "active", "power_w"),
                )
                if sample_cache is not None:
                    sample_cache[cand_id] = candidate_samples

            candidate_references = calculate_fresh_references(
                candidate_samples,
                durations,
                activity_id=cand_id,
                activity_date=candidate.get("start_time"),
                activity_duration_s=candidate.get("duration_s"),
            )
            if candidate_references:
                references[cand_id] = {
                    "activity": candidate,
                    "references": candidate_references,
                }
        return references

    def progress(self, request: ProgressRequest):
        activities = _filter_period_activities(
            self.store.activities(),
            request.modality,
            request.period,
            request.start_date,
            request.end_date,
        )

        sample_cache: dict[str, list[dict[str, Any]]] = {}

        durations = request.durations
        best_watts: dict[int, float | None] = {d: None for d in durations}
        best_records: dict[int, dict[str, Any] | None] = {d: None for d in durations}
        per_activity_pdc = []
        activities_with_metrics = []

        for activity in sorted(activities, key=lambda a: a["start_time"]):
            act_id = activity["id"]
            if act_id in sample_cache:
                samples = sample_cache[act_id]
            else:
                samples = self.store.samples(act_id)
                sample_cache[act_id] = samples

            curve = analytics.power_curve(samples, durations)

            act_watts = {str(d): curve.get(d) for d in durations}
            per_activity_pdc.append(
                {
                    "activity_id": act_id,
                    "date": activity["start_time"][:10],
                    "modality": activity.get("modality", "unknown"),
                    "watts": act_watts,
                }
            )

            for d in durations:
                w = curve.get(d)
                if w is not None:
                    if best_watts[d] is None or w > best_watts[d]:
                        best_watts[d] = w
                        best_records[d] = {
                            "activity_id": act_id,
                            "start_time": activity.get("start_time"),
                        }

            res = self.analyze_activity(
                ActivityRequest(
                    activity_id=act_id, parameter_mode=request.parameter_mode
                ),
                samples=samples,
            )
            metrics = res.data.get("metrics") or {}
            activities_with_metrics.append(
                {
                    "activity": activity,
                    "samples": samples,
                    "metrics": metrics,
                    "declared_parameters": res.data.get("declared_parameters") or {},
                    "result": res,
                }
            )

        previous_period_data = None
        if request.compare_previous:
            prev_start = None
            prev_end = None
            if request.period in PERIOD_DAYS:
                days = PERIOD_DAYS[request.period]
                ref_date = request.end_date or (
                    max(date.fromisoformat(a["start_time"][:10]) for a in activities)
                    if activities
                    else date.today()
                )
                cur_start = ref_date - timedelta(days=days)
                prev_end = cur_start - timedelta(days=1)
                prev_start = prev_end - timedelta(days=days - 1)
            elif request.period == "custom" and request.start_date and request.end_date:
                span = (request.end_date - request.start_date).days
                prev_end = request.start_date - timedelta(days=1)
                prev_start = prev_end - timedelta(days=span)

            if prev_start and prev_end:
                prev_activities = _filter_period_activities(
                    self.store.activities(),
                    request.modality,
                    "custom",
                    prev_start,
                    prev_end,
                )
                prev_best_watts: dict[int, float | None] = {d: None for d in durations}
                prev_best_records: dict[int, dict[str, Any] | None] = {
                    d: None for d in durations
                }
                for prev_act in prev_activities:
                    prev_id = prev_act["id"]
                    if prev_id in sample_cache:
                        prev_samples = sample_cache[prev_id]
                    else:
                        prev_samples = self.store.samples(prev_id)
                        sample_cache[prev_id] = prev_samples
                    prev_curve = analytics.power_curve(prev_samples, durations)
                    for d in durations:
                        pw = prev_curve.get(d)
                        if pw is not None:
                            if prev_best_watts[d] is None or pw > prev_best_watts[d]:
                                prev_best_watts[d] = pw
                                prev_best_records[d] = {
                                    "activity_id": prev_act["id"],
                                    "start_time": prev_act.get("start_time"),
                                }
                previous_period_data = {
                    "start_date": prev_start.isoformat(),
                    "end_date": prev_end.isoformat(),
                    "activity_count": len(prev_activities),
                    "watts": {str(d): prev_best_watts[d] for d in durations},
                    "records": {str(d): prev_best_records[d] for d in durations},
                }

        power_duration_evolution = {
            "durations_s": durations,
            "current_period": {
                "activity_count": len(activities),
                "watts": {str(d): best_watts[d] for d in durations},
                "records": {str(d): best_records[d] for d in durations},
            },
            "previous_period": previous_period_data,
            "per_activity": per_activity_pdc,
        }

        fixed_hr_ef_trend = [
            analytics.calculate_fixed_hr_ef_trend_point(
                item["activity"], item["samples"]
            )
            for item in activities_with_metrics
        ]

        fresh_references = self._progress_fresh_references(
            activities, [1200], sample_cache=sample_cache
        )
        durability_trend = []
        for item in activities_with_metrics:
            act = item["activity"]
            act_id = act["id"]
            act_date = date.fromisoformat(act["start_time"][:10])
            historical_fresh_refs: dict[int, dict[str, Any]] = {}
            for candidate_data in fresh_references.values():
                candidate = candidate_data["activity"]
                candidate_date = date.fromisoformat(candidate["start_time"][:10])
                if (
                    candidate["id"] != act_id
                    and candidate.get("modality", "unknown")
                    == act.get("modality", "unknown")
                    and act_date - timedelta(days=90) <= candidate_date <= act_date
                ):
                    reference = candidate_data["references"].get(1200)
                    if reference is not None:
                        current = historical_fresh_refs.get(1200)
                        if current is None or reference["power_w"] > current["power_w"]:
                            historical_fresh_refs[1200] = {
                                **reference,
                                "source": "historical_90d",
                            }

            dur_data = analytics.durability(
                item["samples"],
                durations=[1200],
                thresholds_kj=[1500.0, 1800.0],
                historical_fresh_references=historical_fresh_refs,
                activity_id=act_id,
                activity_date=act.get("start_time"),
                activity_duration_s=act.get("duration_s"),
            )
            aerobic_dur_res = analytics.calculate_aerobic_durability(
                item["samples"], activity_duration_s=act.get("duration_s")
            )

            points = dur_data.get("points", [])
            p_1500 = next(
                (
                    p
                    for p in points
                    if p["threshold_kj"] == 1500.0 and p["duration_s"] == 1200
                ),
                None,
            )
            p_1800 = next(
                (
                    p
                    for p in points
                    if p["threshold_kj"] == 1800.0 and p["duration_s"] == 1200
                ),
                None,
            )

            aerobic_points = aerobic_dur_res.get("points", [])
            ef_1500 = next(
                (p for p in aerobic_points if p["threshold_kj"] == 1500.0), None
            )
            ef_1800 = next(
                (p for p in aerobic_points if p["threshold_kj"] == 1800.0), None
            )

            durability_trend.append(
                {
                    "activity_id": act_id,
                    "date": act["start_time"][:10],
                    "modality": act.get("modality", "unknown"),
                    "total_work_kj": dur_data.get("total_work_kj"),
                    "retention_20m_1500kj": p_1500.get("retention_pct")
                    if p_1500
                    else None,
                    "retention_20m_1800kj": p_1800.get("retention_pct")
                    if p_1800
                    else None,
                    "ef_retention_1500kj": ef_1500.get("retention_pct")
                    if ef_1500
                    else None,
                    "ef_retention_1800kj": ef_1800.get("retention_pct")
                    if ef_1800
                    else None,
                    "confidence": p_1500.get("confidence") if p_1500 else "LOW",
                    "available": dur_data.get("available", False),
                    "unavailable_reason": None
                    if dur_data.get("available")
                    else dur_data.get("reason"),
                }
            )

        decoupling_trend = []
        for item in activities_with_metrics:
            act = item["activity"]
            act_id = act["id"]
            drift_res = item["metrics"].get("drift") or {}
            decoupling_trend.append(
                {
                    "activity_id": act_id,
                    "date": act["start_time"][:10],
                    "modality": act.get("modality", "unknown"),
                    "duration_s": drift_res.get("duration_s"),
                    "drift_percent": drift_res.get("drift_percent"),
                    "power_cv": drift_res.get("power_cv"),
                    "confidence": drift_res.get("confidence", "low"),
                    "available": drift_res.get("available", False),
                    "unavailable_reason": None
                    if drift_res.get("available")
                    else drift_res.get("reason"),
                }
            )

        weekly_composition = analytics.calculate_weekly_composition(
            activities_with_metrics
        )

        fatigue_thresholds = request.fatigue_thresholds_kj
        fatigued_period_best: dict[str, dict[str, float | None]] = {
            str(t): {str(d): None for d in durations} for t in fatigue_thresholds
        }
        per_activity_fatigued = []

        for item in activities_with_metrics:
            act = item["activity"]
            act_id = act["id"]
            fatigued_act = analytics.calculate_fatigued_pdc_for_activity(
                item["samples"],
                durations,
                fatigue_thresholds,
                activity_duration_s=act.get("duration_s"),
            )
            per_activity_fatigued.append(
                {
                    "activity_id": act_id,
                    "date": act["start_time"][:10],
                    "modality": act.get("modality", "unknown"),
                    "total_work_kj": fatigued_act["total_work_kj"],
                    "watts_after_thresholds": fatigued_act["watts_after_thresholds"],
                }
            )

            for t in fatigue_thresholds:
                t_str = str(t)
                act_watts = fatigued_act["watts_after_thresholds"].get(t_str, {})
                for d in durations:
                    pw = act_watts.get(str(d))
                    if pw is not None:
                        if (
                            fatigued_period_best[t_str][str(d)] is None
                            or pw > fatigued_period_best[t_str][str(d)]
                        ):
                            fatigued_period_best[t_str][str(d)] = pw

        fatigued_pdc_summary = []
        for t in fatigue_thresholds:
            t_str = str(t)
            watts_map = fatigued_period_best[t_str]
            has_any = any(w is not None for w in watts_map.values())
            fatigued_pdc_summary.append(
                {
                    "threshold_kj": t,
                    "watts": watts_map,
                    "available": has_any,
                    "reason": "Best observed power after work threshold"
                    if has_any
                    else "No qualifying activity reached work threshold with valid contiguous power",
                }
            )

        fatigued_pdc = {
            "fatigue_thresholds_kj": fatigue_thresholds,
            "durations_s": durations,
            "period_maxima": fatigued_pdc_summary,
            "per_activity": per_activity_fatigued,
        }

        threshold_evidence_trend = []
        for item in activities_with_metrics:
            act = item["activity"]
            act_id = act["id"]
            threshold_data = item["metrics"].get("threshold_estimate") or {}
            threshold_evidence_trend.append(
                {
                    "activity_id": act_id,
                    "date": act["start_time"][:10],
                    "modality": act.get("modality", "unknown"),
                    "observed_20m_w": threshold_data.get("observed_20m_w"),
                    "ftp_estimate_w": threshold_data.get("watts"),
                    "declared_ftp_w": item["declared_parameters"].get("ftp_w"),
                    "available": threshold_data.get("available", False),
                    "reason": threshold_data.get("reason"),
                }
            )

        return self.result(
            "progress",
            {
                "period": request.period,
                "modality": request.modality,
                "activities_evaluated": len(activities),
                "power_duration_evolution": power_duration_evolution,
                "fixed_hr_ef_trend": fixed_hr_ef_trend,
                "durability_trend": durability_trend,
                "decoupling_trend": decoupling_trend,
                "weekly_composition": weekly_composition,
                "fatigued_pdc": fatigued_pdc,
                "threshold_evidence_trend": threshold_evidence_trend,
            },
            parameter_mode=request.parameter_mode,
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

    def get_progress(self, request: ProgressRequest):
        return self.progress(request)
