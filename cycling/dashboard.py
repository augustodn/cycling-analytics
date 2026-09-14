"""Streamlit adapter: visualization only, all metrics come from the service."""

import argparse
from datetime import UTC, date, datetime, timedelta

import streamlit as st

from cycling.models import (
    ActivityRequest,
    CurveRequest,
    DurabilityRequest,
    LoadRequest,
    PeriodHRDistributionRequest,
    PeriodPowerCurveRequest,
    StreamRequest,
)
from cycling.service import CyclingService
from cycling.storage import Store

POWER_CURVE_TIME_EXPONENT = 0.2
POWER_CURVE_TICKS = (
    (1, "1s"),
    (15, "15s"),
    (60, "1m"),
    (300, "5m"),
    (600, "10m"),
    (1200, "20m"),
    (1800, "30m"),
    (2700, "45m"),
    (3600, "1h"),
    (5400, "1.5h"),
    (7200, "2h"),
    (10800, "3h"),
    (14400, "4h"),
    (18000, "5h"),
    (21600, "6h"),
    (36000, "10h"),
    (86400, "24h"),
)

DURABILITY_STATE_LABELS = (
    (1000.0, "Fresh"),
    (1500.0, "Early endurance"),
    (1800.0, "Meaningful fatigue"),
    (2100.0, "Late-race"),
    (float("inf"), "Deep fatigue"),
)

LOAD_CURVE_EXPLANATION = (
    "CTL (fitness) is a 42-day exponentially weighted average of daily load; "
    "ATL (fatigue) is the same calculation over 7 days; TSB (form) is yesterday's "
    "CTL minus ATL. Each day's load uses power first (when coverage is sufficient), "
    "then HR, then session RPE. Global load combines all modalities before these "
    "daily calculations."
)

PERFORMANCE_DURABILITY_EXPLANATION = (
    "Performance durability estimates how much observed power remains available "
    "after accumulating significant prior work. It depends on the athlete "
    "actually producing hard efforts late in the ride."
)
AEROBIC_DURABILITY_EXPLANATION = (
    "Aerobic durability tracks how much efficiency (power per heart-rate beat) "
    "remains in stable windows as accumulated work increases."
)


@st.cache_data(max_entries=32, show_spinner=False)
def _cached_performance_durability(
    data_dir: str,
    activity_id: str,
    durations_s: tuple[int, ...],
    thresholds_kj: tuple[float, ...],
) -> dict:
    with Store(data_dir) as store:
        result = CyclingService(store).durability(
            DurabilityRequest(
                activity_id=activity_id,
                durations=list(durations_s),
                thresholds_kj=list(thresholds_kj),
            )
        )
        return result.model_dump(mode="json")


def _format_power_curve_duration(duration_s: int) -> str:
    if duration_s < 60:
        return f"{duration_s}s"
    minutes, seconds = divmod(duration_s, 60)
    if minutes < 60:
        return f"{minutes}m" if seconds == 0 else f"{minutes}m {seconds}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h" if minutes == 0 else f"{hours}h {minutes}m"


def _power_curve_position(duration_s: int) -> float:
    if duration_s < 1:
        raise ValueError("duration must be at least one second")
    return duration_s**POWER_CURVE_TIME_EXPONENT


def _power_curve_figure(
    go,
    watts: dict[str, float | None],
    requested_durations: list[int] | None = None,
):
    points = sorted(
        (int(duration), value) for duration, value in watts.items() if value is not None
    )
    if not points:
        return None

    max_duration = max(
        requested_durations or [],
        default=points[-1][0],
    )
    ticks = [
        (duration, label)
        for duration, label in POWER_CURVE_TICKS
        if duration <= max_duration
    ]
    if not ticks or ticks[-1][0] != max_duration:
        ticks.append((max_duration, _format_power_curve_duration(max_duration)))

    return go.Figure(
        go.Scatter(
            x=[_power_curve_position(duration) for duration, _ in points],
            y=[value for _, value in points],
            customdata=[
                [_format_power_curve_duration(duration)] for duration, _ in points
            ],
            hovertemplate=(
                "Duration: %{customdata[0]}<br>Power: %{y:.0f} W<extra></extra>"
            ),
            mode="lines+markers",
        )
    ).update_layout(
        xaxis={
            "title": "Duration",
            "type": "linear",
            "range": [1, _power_curve_position(max_duration)],
            "tickmode": "array",
            "tickvals": [_power_curve_position(duration) for duration, _ in ticks],
            "ticktext": [label for _, label in ticks],
        },
        yaxis_title="Best observed power (W)",
    )


def _durability_state_label(threshold_kj: float) -> str:
    for upper_bound, label in DURABILITY_STATE_LABELS:
        if threshold_kj < upper_bound:
            return label
    return "Deep fatigue"


def _durability_plot_payload(data: dict) -> dict:
    thresholds = [float(value) for value in data.get("thresholds_kj", [])]
    durations = [int(value) for value in data.get("durations_s", [])]
    points = {
        (float(point["threshold_kj"]), int(point["duration_s"])): point
        for point in data.get("points", [])
    }
    x_labels = ["Fresh"] + [
        f"{_durability_state_label(threshold)}\n{threshold:g} kJ"
        for threshold in thresholds
    ]
    retention = {}
    heatmap_values = []
    heatmap_text = []
    for duration in durations:
        values = [100.0]
        for threshold in thresholds:
            point = points.get((threshold, duration))
            values.append(point.get("retention_pct") if point else None)
        retention[duration] = values
        heatmap_values.append(values)
        heatmap_text.append(
            ["100%"]
            + [f"{value:.1f}%" if value is not None else "N/A" for value in values[1:]]
        )
    return {
        "durations_s": durations,
        "x_labels": x_labels,
        "retention": retention,
        "heatmap_values": heatmap_values,
        "heatmap_text": heatmap_text,
    }


def _aerobic_durability_plot_payload(data: dict) -> dict:
    thresholds = [float(value) for value in data.get("thresholds_kj", [])]
    points = {float(point["threshold_kj"]): point for point in data.get("points", [])}
    labels = ["Fresh"] + [
        f"{_durability_state_label(threshold)}\n{threshold:g} kJ"
        for threshold in thresholds
    ]
    baseline_ef = data.get("baseline", {}).get("efficiency_factor")
    retention = [100.0]
    efficiency = [baseline_ef]
    for threshold in thresholds:
        point = points.get(threshold, {})
        retention.append(point.get("retention_pct"))
        efficiency.append(point.get("efficiency_factor"))
    return {
        "labels": labels,
        "retention": retention,
        "efficiency": efficiency,
        "thresholds_kj": thresholds,
    }


def _render_hr_distribution(st, go, data: dict, no_data_message: str) -> None:
    st.caption(
        "Basis: percentage of known heart-rate time; missing or unclassified samples are unknown."
    )
    col1, col2 = st.columns(2)
    col1.metric(
        "Known HR time",
        _format_power_curve_duration(data.get("total_seconds", 0)),
    )
    col2.metric(
        "Unknown HR time",
        _format_power_curve_duration(data.get("unknown_seconds", 0)),
    )

    seconds = data.get("seconds", [])
    percentages = data.get("percentages", [])
    zones = data.get("zones", [])
    labels = [zone["label"] for zone in zones]

    if not sum(seconds):
        st.warning(no_data_message)
        return

    table_data = [
        {
            "Zone": zone["label"],
            "Intensity": zone["percentage_range"],
            "HR range": zone["hr_range"],
            "Time": _format_power_curve_duration(seconds[index]),
            "Percentage": f"{percentages[index]:.1f}%",
        }
        for index, zone in enumerate(zones)
    ]
    st.dataframe(table_data)

    chart = go.Figure(
        go.Bar(
            x=labels,
            y=percentages,
            text=[f"{percentage:.1f}%" for percentage in percentages],
            textposition="auto",
        )
    ).update_layout(
        xaxis_title="HR zone",
        yaxis_title="Known HR time (%)",
        title="Heart rate zone distribution",
    )
    st.plotly_chart(chart, width="stretch")


def _period_filter(st, activities: list[dict], key: str):
    period = st.selectbox(
        "Period",
        ["30d", "90d", "365d", "all", "Custom range"],
        key=f"{key}_period",
    )
    if period != "Custom range":
        return period, None, None

    activity_dates = [
        date.fromisoformat(activity["start_time"][:10]) for activity in activities
    ]
    selected = st.date_input(
        "Start and end dates",
        value=(min(activity_dates), max(activity_dates)),
        min_value=min(activity_dates),
        max_value=max(activity_dates),
        key=f"{key}_dates",
    )
    if isinstance(selected, (tuple, list)) and len(selected) == 2:
        return "custom", selected[0], selected[1]
    return "custom", None, None


def run():
    import plotly.graph_objects as go

    args = argparse.ArgumentParser()
    args.add_argument("--data-dir", default=".cycling")
    settings, _ = args.parse_known_args()
    st.set_page_config(page_title="Local cycling analytics", layout="wide")
    st.title("Local cycling analytics")
    st.caption(
        "Recorded evidence, not physiological certainty. UTC dates. Originals stay local."
    )
    with Store(settings.data_dir) as store:
        service = CyclingService(store)
        page = st.sidebar.selectbox(
            "View",
            [
                "Overview",
                "Activity",
                "Power curve",
                "Heart rate distribution",
                "Durability",
                "Load",
                "Calendar",
            ],
        )
        mode = st.sidebar.selectbox("Declared settings", ["historical", "current"])

        activities = service.list_activities().data.get("activities", [])
        if not activities:
            st.info(
                "No activities. Run: uv run python -m cycling ingest downloads/strava"
            )
            return

        if page == "Overview":
            st.subheader("System Status")
            try:
                st.json(service.status().model_dump(mode="json"))
            except Exception as exc:
                st.error(f"Status unavailable: {exc}")

            st.subheader("Recent Activities")
            st.dataframe(activities[:10])

            modalities = sorted(
                {a.get("modality", "unknown") for a in activities if a.get("modality")}
            )
            st.subheader("Global Load (all modalities)")
            end = datetime.now(UTC).date()
            start = end - timedelta(days=90)
            try:
                global_load = service.load(
                    LoadRequest(
                        start=start, end=end, modality="all", parameter_mode=mode
                    )
                )
                global_days = global_load.data.get("days", [])
                if global_days:
                    last_day = global_days[-1]
                    st.metric(
                        label="Global Load (CTL/ATL/TSB)",
                        value=f"CTL {last_day.get('ctl', 0):.1f}",
                        delta=f"TSB {last_day.get('tsb', 0):.1f}",
                    )
                    st.markdown(LOAD_CURVE_EXPLANATION)
                    chart = go.Figure()
                    for metric in ("ctl", "atl", "tsb"):
                        chart.add_scatter(
                            x=[d["date"] for d in global_days],
                            y=[d.get(metric) for d in global_days],
                            name=metric.upper(),
                        )
                    chart.update_layout(
                        xaxis_title="Date",
                        yaxis_title="Load",
                    )
                    st.plotly_chart(chart, width="stretch")
                else:
                    st.info("No load data available for the selected period.")
            except Exception as exc:
                st.error(f"Global load unavailable: {exc}")

            if modalities:
                st.subheader("Load by modality")
                for mod in modalities:
                    try:
                        load_res = service.load(
                            LoadRequest(
                                start=start, end=end, modality=mod, parameter_mode=mode
                            )
                        )
                        days = load_res.data.get("days", [])
                        if days:
                            last_day = days[-1]
                            st.metric(
                                label=f"{mod.upper()} Load (CTL/ATL/TSB)",
                                value=f"CTL {last_day.get('ctl', 0):.1f}",
                                delta=f"TSB {last_day.get('tsb', 0):.1f}",
                            )
                    except Exception:
                        pass
            return

        if page == "Calendar":
            st.subheader("Activity Calendar Table")
            mod_filter = st.selectbox(
                "Filter Modality",
                ["all"] + sorted({a.get("modality", "unknown") for a in activities}),
            )
            filtered = activities
            if mod_filter != "all":
                filtered = [
                    a for a in activities if a.get("modality", "unknown") == mod_filter
                ]
            st.dataframe(filtered)
            return

        if page == "Load":
            modalities = sorted({a.get("modality", "unknown") for a in activities})
            modality = st.selectbox(
                "Modality (all = global load)",
                ["all"] + modalities,
            )
            start = st.date_input(
                "Start", datetime.now(UTC).date() - timedelta(days=90)
            )
            end = st.date_input("End", datetime.now(UTC).date())
            basis = st.selectbox(
                "Three-zone distribution basis", ["time", "sessions", "load"]
            )
            try:
                result = service.load(
                    LoadRequest(
                        start=start,
                        end=end,
                        modality=modality,
                        basis=basis,
                        parameter_mode=mode,
                    )
                )
                days = result.data.get("days", [])
                if days:
                    st.markdown(LOAD_CURVE_EXPLANATION)
                    chart = go.Figure()
                    for metric in ("ctl", "atl", "tsb"):
                        chart.add_scatter(
                            x=[d["date"] for d in days],
                            y=[d.get(metric) for d in days],
                            name=metric.upper(),
                        )
                    st.plotly_chart(chart, width="stretch")
                st.json(result.model_dump(mode="json"))
            except Exception as exc:
                st.error(f"Error computing load: {exc}")
            return

        if page == "Power curve":
            curve_type = st.selectbox("Curve type", ["Single Activity", "Period"])
            if curve_type == "Period":
                period, start_date, end_date = _period_filter(
                    st, activities, "power_curve"
                )
                if period == "custom" and (start_date is None or end_date is None):
                    st.warning("Select both a start date and an end date.")
                    return
                modality = st.selectbox(
                    "Modality",
                    ["all"]
                    + sorted({a.get("modality", "unknown") for a in activities}),
                )
                try:
                    result = service.period_power_curve(
                        PeriodPowerCurveRequest(
                            period=period,
                            modality=modality,
                            start_date=start_date,
                            end_date=end_date,
                        )
                    )
                    watts = result.data.get("watts", {})
                    chart = _power_curve_figure(
                        go, watts, result.data.get("durations_s")
                    )
                    if chart is not None:
                        st.plotly_chart(chart, width="stretch")
                    else:
                        st.warning(
                            "No power data found for the selected period/modality."
                        )
                    st.json(result.model_dump(mode="json"))
                except Exception as exc:
                    st.error(f"Error computing period power curve: {exc}")
                return

        if page == "Heart rate distribution":
            dist_type = st.selectbox("Distribution type", ["Single Activity", "Period"])
            if dist_type == "Period":
                period, start_date, end_date = _period_filter(
                    st, activities, "hr_distribution"
                )
                if period == "custom" and (start_date is None or end_date is None):
                    st.warning("Select both a start date and an end date.")
                    return
                modality = st.selectbox(
                    "Modality",
                    ["all"]
                    + sorted({a.get("modality", "unknown") for a in activities}),
                )
                try:
                    result = service.period_hr_distribution(
                        PeriodHRDistributionRequest(
                            period=period,
                            modality=modality,
                            parameter_mode=mode,
                            start_date=start_date,
                            end_date=end_date,
                        )
                    )
                    _render_hr_distribution(
                        st,
                        go,
                        result.data,
                        "No heart rate data found for the selected period/modality.",
                    )
                    st.json(result.model_dump(mode="json"))
                except Exception as exc:
                    st.error(f"Error computing period HR distribution: {exc}")
                return

        chosen = st.selectbox(
            "Activity",
            activities,
            format_func=lambda a: (
                f"{a.get('start_time', 'N/A')} · {a.get('source_name', a.get('id', '')[:12])} · {a.get('modality', 'unknown')}"
            ),
        )
        ident = chosen["id"]
        st.write("Quality flags", chosen.get("quality_flags", []))

        if page == "Activity":
            try:
                result = service.analyze_activity(
                    ActivityRequest(activity_id=ident, parameter_mode=mode)
                )
                st.json(result.model_dump(mode="json"))
            except Exception as exc:
                st.error(f"Analysis unavailable: {exc}")

            try:
                stream_res = service.stream(StreamRequest(activity_id=ident))
                stream = stream_res.data.get("samples", [])
                if stream:
                    chart = go.Figure()
                    for key in ("power_w", "hr_bpm", "cadence_rpm"):
                        y_vals = [r.get(key) for r in stream]
                        if any(v is not None for v in y_vals):
                            chart.add_scatter(
                                x=[r["elapsed_s"] for r in stream],
                                y=y_vals,
                                name=key,
                                connectgaps=False,
                            )
                    chart.update_layout(
                        xaxis_title="Elapsed seconds",
                        yaxis_title="W / bpm / rpm (see series)",
                    )
                    st.plotly_chart(chart, width="stretch")
                    st.caption(
                        "Display-only stride sampling may omit peaks. Analytics use full resolution."
                    )
            except Exception as exc:
                st.warning(f"Stream unavailable: {exc}")

            try:
                thresh = service.thresholds(
                    ActivityRequest(activity_id=ident, parameter_mode=mode)
                )
                st.json(thresh.model_dump(mode="json"))
            except Exception as exc:
                st.warning(f"Threshold estimate unavailable: {exc}")

        elif page == "Power curve":
            try:
                result = service.power_curve(CurveRequest(activity_id=ident))
                curve = result.data.get("watts", {})
                chart = _power_curve_figure(go, curve, result.data.get("durations_s"))
                if chart is not None:
                    st.plotly_chart(chart, width="stretch")
                else:
                    st.warning("No power data available for this activity.")
                st.json(result.model_dump(mode="json"))
            except Exception as exc:
                st.error(f"Power curve unavailable: {exc}")

        elif page == "Heart rate distribution":
            try:
                result = service.hr_distribution(
                    ActivityRequest(activity_id=ident, parameter_mode=mode)
                )
                _render_hr_distribution(
                    st,
                    go,
                    result.data,
                    "No heart rate data available for this activity.",
                )
                st.json(result.model_dump(mode="json"))
            except Exception as exc:
                st.error(f"Heart rate distribution unavailable: {exc}")

        elif page == "Durability":
            try:
                mode_options = ["Performance durability", "Aerobic durability"]
                if hasattr(st, "segmented_control"):
                    durability_mode = st.segmented_control(
                        "Durability mode",
                        mode_options,
                        default=mode_options[0],
                        key="durability_mode",
                    )
                else:
                    durability_mode = st.radio(
                        "Durability mode",
                        mode_options,
                        horizontal=True,
                        key="durability_mode",
                    )
                if durability_mode == "Performance durability":
                    st.caption(PERFORMANCE_DURABILITY_EXPLANATION)
                    with st.spinner("Computing performance durability..."):
                        request = DurabilityRequest(activity_id=ident)
                        result_payload = _cached_performance_durability(
                            settings.data_dir,
                            ident,
                            tuple(request.durations),
                            tuple(request.thresholds_kj),
                        )
                    st.warning(
                        "Observed efforts by prior work; not a controlled fatigue test. Missing-power MTB has no power durability."
                    )
                    data = result_payload["data"]
                    if not data.get("available", True):
                        st.warning(
                            f"Durability unavailable: {data.get('reason', 'Missing power data')}"
                        )
                    else:
                        plot = _durability_plot_payload(data)
                        durations = plot["durations_s"]

                        fig_ret = go.Figure()
                        for d in durations:
                            fig_ret.add_trace(
                                go.Scatter(
                                    x=plot["x_labels"],
                                    y=plot["retention"][d],
                                    mode="lines+markers",
                                    name=_format_power_curve_duration(d),
                                    connectgaps=False,
                                )
                            )
                        fig_ret.update_layout(
                            title="Power Retention vs Accumulated Work",
                            xaxis_title="Accumulated Work",
                            yaxis_title="Power Retention (%)",
                            yaxis=dict(range=[0, 110]),
                        )
                        st.plotly_chart(fig_ret, width="stretch")

                        fig_hm = go.Figure(
                            data=go.Heatmap(
                                z=plot["heatmap_values"],
                                x=plot["x_labels"],
                                y=[_format_power_curve_duration(d) for d in durations],
                                text=plot["heatmap_text"],
                                texttemplate="%{text}",
                                textfont={"size": 12},
                                colorscale="Viridis",
                                showscale=True,
                            )
                        )
                        fig_hm.update_layout(
                            title="Power Retention Heatmap",
                            xaxis_title="Work Threshold",
                            yaxis_title="Duration",
                        )
                        st.plotly_chart(fig_hm, width="stretch")
                    st.json(result_payload)
                else:
                    st.caption(AEROBIC_DURABILITY_EXPLANATION)
                    result = service.aerobic_durability(
                        ActivityRequest(activity_id=ident, parameter_mode=mode)
                    )
                    data = result.data
                    if not data.get("available", False):
                        st.warning(
                            data.get(
                                "reason",
                                "Aerobic durability requires sufficient power and heart-rate data.",
                            )
                        )
                    else:
                        plot = _aerobic_durability_plot_payload(data)
                        if not any(
                            value is not None for value in plot["retention"][1:]
                        ):
                            st.info(
                                "No complete aerobic durability window remains after the selected work thresholds."
                            )
                        else:
                            fig_ret = go.Figure(
                                go.Scatter(
                                    x=plot["labels"],
                                    y=plot["retention"],
                                    mode="lines+markers",
                                    name="EF retention",
                                    connectgaps=False,
                                )
                            )
                            fig_ret.add_hline(
                                y=100,
                                line_dash="dash",
                                annotation_text="Fresh = 100%",
                            )
                            fig_ret.update_layout(
                                title="Aerobic Efficiency Retention vs Accumulated Work",
                                xaxis_title="Accumulated Work",
                                yaxis_title="EF retention (%)",
                                yaxis=dict(range=[0, 110]),
                            )
                            st.plotly_chart(fig_ret, width="stretch")

                            fig_ef = go.Figure(
                                go.Scatter(
                                    x=plot["labels"],
                                    y=plot["efficiency"],
                                    mode="lines+markers",
                                    name="Efficiency factor",
                                    connectgaps=False,
                                )
                            )
                            fig_ef.update_layout(
                                title="Efficiency Factor vs Accumulated Work",
                                xaxis_title="Accumulated Work",
                                yaxis_title="Efficiency factor (W/bpm)",
                            )
                            st.plotly_chart(fig_ef, width="stretch")
                    st.json(result.model_dump(mode="json"))
            except Exception as exc:
                st.error(f"Durability unavailable: {exc}")


if __name__ == "__main__":
    try:
        run()
    except (ValueError, KeyError, OSError) as exc:
        st.error(str(exc))
