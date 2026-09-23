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
    ProgressRequest,
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
POWER_SKILL_GROUPS = (
    ("Sprinting", ((15, "15s"), (30, "30s"), (60, "1m")), "#2863C8"),
    (
        "Attacking",
        ((120, "2m"), (180, "3m"), (300, "5m"), (600, "10m")),
        "#67A936",
    ),
    (
        "Climbing",
        (
            (900, "15m"),
            (1200, "20m"),
            (1800, "30m"),
            (2700, "45m"),
            (3600, "60m"),
        ),
        "#EF783A",
    ),
)
POWER_SKILL_INTERVALS = tuple(
    (duration, label, skill, color)
    for skill, intervals, color in POWER_SKILL_GROUPS
    for duration, label in intervals
)
POWER_SKILL_DURATIONS = tuple(duration for duration, _, _, _ in POWER_SKILL_INTERVALS)

DURABILITY_STATE_LABELS = (
    (1000.0, "Fresh"),
    (1500.0, "Early endurance"),
    (1800.0, "Meaningful fatigue"),
    (2100.0, "Late-race"),
    (float("inf"), "Deep fatigue"),
)
OVERVIEW_PERIODS = ("7d", "21d", "30d", "90d", "365d", "all")
OVERVIEW_DURABILITY_MIN_DURATION_S = 90 * 60
HR_ZONE_COLORS = {
    "1 Recovery": "#808080",
    "2 Aerobic": "#87CEEB",
    "3 Tempo": "#228B22",
    "4 SubThreshold": "#FFD700",
    "5a Threshold": "#FF69B4",
    "5b Aerobic Capacity": "#FF0000",
    "5c Anaerobic": "#8A2BE2",
}
UNCLASSIFIED_HR_COLOR = "#D3D3D3"

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


@st.cache_data(max_entries=8, show_spinner=False)
def _cached_progress(data_dir: str, request_json: str) -> dict:
    with Store(data_dir) as store:
        result = CyclingService(store).progress(
            ProgressRequest.model_validate_json(request_json)
        )
        return result.model_dump(mode="json")


@st.cache_data(ttl=300, max_entries=8, show_spinner=False)
def _cached_weekly_cycling_training(data_dir: str, end_date: str) -> dict:
    with Store(data_dir) as store:
        result = CyclingService(store).weekly_cycling_training(
            date.fromisoformat(end_date)
        )
        return result.model_dump(mode="json")


@st.cache_data(ttl=300, max_entries=8, show_spinner=False)
def _cached_all_time_power_skills(data_dir: str, modality: str) -> dict:
    with Store(data_dir) as store:
        result = CyclingService(store).period_power_curve(
            PeriodPowerCurveRequest(
                period="all",
                modality=modality,
                durations=list(POWER_SKILL_DURATIONS),
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
        yaxis={
            "title": "Best observed power (W)",
            "rangemode": "tozero",
        },
    )


def _power_skills_figure(
    go, historical_watts: dict, selected_watts: dict, selected_label: str
):
    labels = [label for _, label, _, _ in POWER_SKILL_INTERVALS]
    durations = [duration for duration, _, _, _ in POWER_SKILL_INTERVALS]
    group_colors = [color for _, _, _, color in POWER_SKILL_INTERVALS]
    history = [historical_watts.get(str(duration)) for duration in durations]
    selected = [selected_watts.get(str(duration)) for duration in durations]
    available = [value for value in (*history, *selected) if value is not None]
    if not available:
        return None

    max_watts = max(float(value) for value in available)
    if max_watts <= 0:
        return None

    ring_base = max_watts * 1.08
    ring_width = max_watts * 0.12
    figure = go.Figure(
        go.Barpolar(
            r=[ring_width] * len(labels),
            theta=labels,
            width=[360 / len(labels)] * len(labels),
            base=[ring_base] * len(labels),
            marker_color=group_colors,
            marker_line_color="white",
            marker_line_width=1,
            hoverinfo="skip",
            showlegend=False,
            name="Power skill groups",
        )
    )
    figure.add_trace(
        go.Scatterpolar(
            r=history,
            theta=labels,
            mode="lines+markers",
            fill="toself",
            fillcolor="rgba(126, 68, 165, 0.62)",
            line={"color": "#7E44A5", "width": 2},
            connectgaps=False,
            name="All-time maximum",
            hovertemplate="%{theta}: %{r:.0f} W<extra>%{fullData.name}</extra>",
        )
    )
    figure.add_trace(
        go.Scatterpolar(
            r=selected,
            theta=labels,
            mode="lines+markers",
            fill="toself",
            fillcolor="rgba(40, 150, 220, 0.25)",
            line={"color": "#2896DC", "width": 2},
            connectgaps=False,
            name=selected_label,
            hovertemplate="%{theta}: %{r:.0f} W<extra>%{fullData.name}</extra>",
        )
    )
    figure.add_trace(
        go.Scatterpolar(
            r=[max_watts * 1.29 if value is not None else None for value in history],
            theta=labels,
            text=[
                f"{float(value):.0f} W" if value is not None else ""
                for value in history
            ],
            mode="text",
            textfont={"size": 10},
            hoverinfo="skip",
            showlegend=False,
            name="All-time watt labels",
        )
    )
    figure.update_layout(
        title="Power Skills — Best Power by Interval",
        height=680,
        legend={"orientation": "h", "y": -0.12},
        polar={
            "radialaxis": {
                "visible": True,
                "range": [0, max_watts * 1.55],
                "ticksuffix": " W",
                "gridcolor": "rgba(128, 128, 128, 0.35)",
            },
            "angularaxis": {
                "categoryorder": "array",
                "categoryarray": labels,
                "direction": "clockwise",
                "rotation": 90,
            },
        },
    )
    return figure


def _render_power_skills(
    st, go, historical_data: dict, selected_data: dict, selected_label: str
) -> None:
    st.subheader("Power Skills")
    st.caption(
        "Intervals: Sprinting 15s–1m (blue), Attacking 2–10m (green), and Climbing "
        "15–60m (orange). Solid fill: all-time maximum; translucent fill: selection. "
        "All-time reference uses the same modality. Values are watts; Strava's personalized "
        "milestone thresholds are not public."
    )
    figure = _power_skills_figure(
        go,
        historical_data.get("watts", {}),
        selected_data.get("watts", {}),
        selected_label,
    )
    if figure is None:
        st.info("No valid power efforts are available for the Power Skills intervals.")
        return
    st.plotly_chart(figure, width="stretch")


def _weekly_training_figure(go, data: dict):
    weeks = data.get("weeks", [])
    labels = [week["iso_week"] for week in weeks]
    total_hours = [week["total_seconds"] / 3600 for week in weeks]
    figure = go.Figure()

    for zone_index, zone in enumerate(data.get("zones", [])):
        figure.add_bar(
            x=labels,
            y=[week["hr_zone_seconds"][zone_index] / 3600 for week in weeks],
            name=zone["label"],
            marker_color=HR_ZONE_COLORS[zone["label"]],
            customdata=[[hours] for hours in total_hours],
            hovertemplate=(
                "%{x}<br>%{fullData.name}: %{y:.2f} h"
                "<br>Total cycling: %{customdata[0]:.2f} h<extra></extra>"
            ),
        )

    figure.add_bar(
        x=labels,
        y=[week["unclassified_seconds"] / 3600 for week in weeks],
        name="Unclassified / no HR",
        marker_color=UNCLASSIFIED_HR_COLOR,
        customdata=[[hours] for hours in total_hours],
        hovertemplate=(
            "%{x}<br>%{fullData.name}: %{y:.2f} h"
            "<br>Total cycling: %{customdata[0]:.2f} h<extra></extra>"
        ),
    )
    figure.update_layout(
        barmode="stack",
        title="Weekly Cycling Hours and HR Zones (Last 12 Weeks)",
        xaxis_title="ISO week",
        yaxis_title="Training time (hours)",
        yaxis={"rangemode": "tozero"},
        legend_title="Heart rate zone",
    )
    return figure


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


def _render_hr_distribution(
    st, go, data: dict, no_data_message: str, show_table: bool = True
) -> None:
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
    if show_table:
        st.dataframe(table_data)

    chart = go.Figure(
        go.Bar(
            x=labels,
            y=percentages,
            marker_color=[HR_ZONE_COLORS[label] for label in labels],
            text=[f"{percentage:.1f}%" for percentage in percentages],
            textposition="auto",
        )
    ).update_layout(
        xaxis_title="HR zone",
        yaxis_title="Known HR time (%)",
        title="Heart rate zone distribution",
    )
    st.plotly_chart(chart, width="stretch")


def _render_performance_durability(st, go, result_payload: dict) -> None:
    data = result_payload.get("data", {})
    st.warning(
        "Observed efforts by prior work; not a controlled fatigue test. "
        "Missing-power MTB has no power durability."
    )
    if not data.get("available", True):
        st.warning(
            f"Durability unavailable: {data.get('reason', 'Missing power data')}"
        )
        return

    plot = _durability_plot_payload(data)
    durations = plot["durations_s"]

    fig_ret = go.Figure()
    for duration in durations:
        fig_ret.add_trace(
            go.Scatter(
                x=plot["x_labels"],
                y=plot["retention"][duration],
                mode="lines+markers",
                name=_format_power_curve_duration(duration),
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
            y=[_format_power_curve_duration(duration) for duration in durations],
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


def _period_filter(st, activities: list[dict], key: str):
    period = st.selectbox(
        "Period",
        ["7d", "21d", "30d", "90d", "365d", "all", "Custom range"],
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
                "Progress",
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
            load_result = None
            power_result = None
            hr_result = None
            durability_payload = None

            end = datetime.now(UTC).date()
            st.subheader("Weekly Cycling Training")
            st.caption(
                "All cycling modalities combined. HR-zone segments use the same zones "
                "as Heart Rate Distribution; remainder includes time without classified HR."
            )
            try:
                training_payload = _cached_weekly_cycling_training(
                    settings.data_dir, end.isoformat()
                )
                st.plotly_chart(
                    _weekly_training_figure(go, training_payload["data"]),
                    width="stretch",
                )
            except Exception as exc:
                st.error(f"Weekly cycling training unavailable: {exc}")

            st.subheader("CTL")
            try:
                start = end - timedelta(days=90)
                load_result = service.load(
                    LoadRequest(
                        start=start, end=end, modality="all", parameter_mode=mode
                    )
                )
                global_days = load_result.data.get("days", [])
                if global_days:
                    last_day = global_days[-1]
                    max_ctl = max(
                        (float(day.get("ctl") or 0) for day in global_days),
                        default=0.0,
                    )
                    st.metric(
                        label="Global CTL",
                        value=f"{last_day.get('ctl', 0):.1f}",
                    )
                    chart = go.Figure(
                        go.Scatter(
                            x=[day["date"] for day in global_days],
                            y=[day.get("ctl") for day in global_days],
                            name="CTL",
                            mode="lines",
                        )
                    )
                    chart.update_layout(
                        title="Chronic Training Load (42-day)",
                        xaxis_title="Date",
                        yaxis_title="CTL",
                        yaxis={"range": [0, max(1.0, max_ctl * 1.05)]},
                    )
                    st.plotly_chart(chart, width="stretch")
                else:
                    st.info("No CTL data available for the selected period.")
            except Exception as exc:
                st.error(f"CTL unavailable: {exc}")

            st.subheader("Power Curve and Heart Rate Distribution")
            overview_period = st.selectbox(
                "Period",
                OVERVIEW_PERIODS,
                index=0,
                key="overview_period",
            )
            power_col, hr_col = st.columns(2)
            with power_col.container(border=True, height="stretch"):
                st.markdown("#### Power Curve")
                try:
                    power_result = service.period_power_curve(
                        PeriodPowerCurveRequest(period=overview_period, modality="all")
                    )
                    watts = power_result.data.get("watts", {})
                    chart = _power_curve_figure(
                        go, watts, power_result.data.get("durations_s")
                    )
                    if chart is not None:
                        chart.update_layout(
                            title=f"Power Curve ({overview_period})",
                            height=600,
                        )
                        st.plotly_chart(chart, width="stretch")
                    else:
                        st.warning("No power data found for the selected period.")
                except Exception as exc:
                    st.error(f"Power curve unavailable: {exc}")

            with hr_col.container(border=True, height="stretch"):
                st.markdown("#### Heart Rate Distribution")
                try:
                    hr_result = service.period_hr_distribution(
                        PeriodHRDistributionRequest(
                            period=overview_period,
                            modality="all",
                            parameter_mode=mode,
                        )
                    )
                    _render_hr_distribution(
                        st,
                        go,
                        hr_result.data,
                        "No heart rate data found for the selected period.",
                        show_table=False,
                    )
                except Exception as exc:
                    st.error(f"Heart rate distribution unavailable: {exc}")

            st.subheader("Durability")
            try:
                durability_activity = service.latest_activity_with_power_and_hr(
                    OVERVIEW_DURABILITY_MIN_DURATION_S
                )
                if durability_activity is None:
                    st.info(
                        "No workout with power, heart rate, and at least 1h30m found."
                    )
                else:
                    activity_label = durability_activity.get(
                        "source_name", durability_activity["id"]
                    )
                    st.caption(
                        "Latest qualifying workout: "
                        f"{durability_activity.get('start_time', 'N/A')} · {activity_label}"
                    )
                    request = DurabilityRequest(activity_id=durability_activity["id"])
                    with st.spinner("Computing durability..."):
                        durability_payload = _cached_performance_durability(
                            settings.data_dir,
                            durability_activity["id"],
                            tuple(request.durations),
                            tuple(request.thresholds_kj),
                        )
                    _render_performance_durability(st, go, durability_payload)
            except Exception as exc:
                st.error(f"Durability unavailable: {exc}")

            st.subheader("Recent Activities")
            st.dataframe(activities[:10])

            st.subheader("JSONs")
            status_payload = None
            try:
                status_payload = service.status().model_dump(mode="json")
            except Exception as exc:
                st.error(f"Status unavailable: {exc}")
            json_results = (
                ("Status", status_payload),
                (
                    "CTL",
                    load_result.model_dump(mode="json") if load_result else None,
                ),
                (
                    "Power Curve",
                    power_result.model_dump(mode="json") if power_result else None,
                ),
                (
                    "Heart Rate Distribution",
                    hr_result.model_dump(mode="json") if hr_result else None,
                ),
                ("Durability", durability_payload),
            )
            for label, payload in json_results:
                if payload is not None:
                    with st.expander(label):
                        st.json(payload)
            return

        if page == "Progress":
            st.subheader("Longitudinal Progress")
            modalities = ["all"] + sorted(
                {a.get("modality", "unknown") for a in activities if a.get("modality")}
            )
            period, start_date, end_date = _period_filter(st, activities, "progress")
            if period == "custom" and (start_date is None or end_date is None):
                st.warning("Select both a start date and an end date.")
                return

            modality = st.selectbox("Modality", modalities, key="progress_modality")
            compare_prev = st.checkbox(
                "Compare previous period for PDC",
                value=False,
                key="progress_compare_prev",
                disabled=period == "all",
            )
            if period == "all":
                st.caption("Previous-period comparison requires a bounded period.")

            try:
                progress_request = ProgressRequest(
                    period=period,
                    modality=modality,
                    parameter_mode=mode,
                    start_date=start_date,
                    end_date=end_date,
                    compare_previous=compare_prev,
                )
                with st.spinner("Computing progress..."):
                    progress_payload = _cached_progress(
                        settings.data_dir, progress_request.model_dump_json()
                    )
                data = progress_payload["data"]
            except Exception as exc:
                st.error(f"Progress data unavailable: {exc}")
                return

            if not data.get("activities_evaluated", 0):
                st.info("No activities found for the selected filter.")
                with st.expander("Raw Progress Data"):
                    st.json(progress_payload)
                return

            # Stage 1: PDC evolution, Fixed-HR/EF trend, Durability trend
            st.markdown("### Stage 1: Power & Efficiency Trends")

            # 1. PDC evolution
            pdc_evo = data.get("power_duration_evolution", {})
            durations_s = pdc_evo.get("durations_s", [])
            cur_p = pdc_evo.get("current_period", {})
            cur_watts = cur_p.get("watts", {})
            prev_p = pdc_evo.get("previous_period")
            per_act_pdc = pdc_evo.get("per_activity", [])

            st.markdown("#### Power-Duration Curve Evolution")
            if cur_watts:
                cols = st.columns(len(durations_s))
                for idx, d in enumerate(durations_s):
                    d_str = str(d)
                    label = _format_power_curve_duration(d)
                    val = cur_watts.get(d_str)
                    val_str = f"{val:.0f} W" if val is not None else "N/A"
                    delta_str = None
                    if prev_p and prev_p.get("watts"):
                        prev_val = prev_p["watts"].get(d_str)
                        if val is not None and prev_val is not None and prev_val > 0:
                            diff = val - prev_val
                            pct = (diff / prev_val) * 100
                            delta_str = f"{diff:+.0f} W ({pct:+.1f}%)"
                    cols[idx % len(cols)].metric(
                        label=label, value=val_str, delta=delta_str
                    )

            if per_act_pdc:
                fig_pdc = go.Figure()
                for d in durations_s:
                    d_str = str(d)
                    dates = [
                        a["date"]
                        for a in per_act_pdc
                        if a.get("watts", {}).get(d_str) is not None
                    ]
                    watts_vals = [
                        a["watts"][d_str]
                        for a in per_act_pdc
                        if a.get("watts", {}).get(d_str) is not None
                    ]
                    if watts_vals:
                        fig_pdc.add_scatter(
                            x=dates,
                            y=watts_vals,
                            mode="lines+markers",
                            name=_format_power_curve_duration(d),
                        )
                fig_pdc.update_layout(
                    title="Per-Activity Best Power by Duration Over Time",
                    xaxis_title="Date",
                    yaxis_title="Power (W)",
                )
                st.plotly_chart(fig_pdc, width="stretch")
            else:
                st.info("No per-activity power duration data available.")

            # 2. Fixed-HR/EF trend
            st.markdown("#### Fixed-HR Power and Efficiency Factor (EF) Trend")
            st.caption(
                "Each point uses a 10-minute contiguous stable window within ±5 bpm of "
                "the target. Indoor and outdoor activities are shown separately."
            )
            ef_pts = data.get("fixed_hr_ef_trend", [])
            fixed_hr_points = []
            for point in ef_pts:
                if not point.get("available"):
                    continue
                environment = "Indoor" if point.get("is_indoor") else "Outdoor"
                for target, values in (point.get("fixed_hr_targets") or {}).items():
                    if values and values.get("power_w") is not None:
                        fixed_hr_points.append(
                            {
                                "date": point["date"],
                                "modality": point.get("modality", "unknown"),
                                "environment": environment,
                                "target": target,
                                "power_w": values["power_w"],
                                "ef": values.get("ef"),
                            }
                        )

            if not fixed_hr_points:
                st.info("No stable Fixed-HR EF points available in this period.")
            else:
                series = {}
                for point in fixed_hr_points:
                    key = (
                        point["modality"],
                        point["environment"],
                        point["target"],
                    )
                    series.setdefault(key, []).append(point)

                fig_fixed_power = go.Figure()
                fig_fixed_ef = go.Figure()
                for (mod_name, environment, target), points in sorted(series.items()):
                    label = f"{mod_name} · {environment} · {target} bpm"
                    fig_fixed_power.add_scatter(
                        x=[p["date"] for p in points],
                        y=[p["power_w"] for p in points],
                        mode="lines+markers",
                        name=label,
                    )
                    ef_points = [p for p in points if p["ef"] is not None]
                    if ef_points:
                        fig_fixed_ef.add_scatter(
                            x=[p["date"] for p in ef_points],
                            y=[p["ef"] for p in ef_points],
                            mode="lines+markers",
                            name=label,
                        )

                fig_fixed_power.update_layout(
                    title="Power at Fixed Heart Rate",
                    xaxis_title="Date",
                    yaxis_title="Power (W)",
                )
                st.plotly_chart(fig_fixed_power, width="stretch")
                if fig_fixed_ef.data:
                    fig_fixed_ef.update_layout(
                        title="Efficiency Factor at Fixed Heart Rate",
                        xaxis_title="Date",
                        yaxis_title="Efficiency Factor (W/bpm)",
                    )
                    st.plotly_chart(fig_fixed_ef, width="stretch")

            # 3. Durability trend
            st.markdown("#### Durability Trend")
            dur_pts = data.get("durability_trend", [])
            avail_dur = [p for p in dur_pts if p.get("available")]
            unavail_count = len(dur_pts) - len(avail_dur)
            if unavail_count > 0:
                st.warning(
                    f"{unavail_count} of {len(dur_pts)} activities lack sufficient work volume for 20m retention calculation."
                )

            if not dur_pts:
                st.info("No durability points evaluated.")
            else:
                fig_dur = go.Figure()
                # 1500 kJ retention
                pts_1500 = [
                    (p["date"], p["retention_20m_1500kj"])
                    for p in dur_pts
                    if p.get("retention_20m_1500kj") is not None
                ]
                if pts_1500:
                    fig_dur.add_scatter(
                        x=[x[0] for x in pts_1500],
                        y=[x[1] for x in pts_1500],
                        mode="lines+markers",
                        name="Power Retention 20m @ 1500 kJ (%)",
                    )
                # 1800 kJ retention
                pts_1800 = [
                    (p["date"], p["retention_20m_1800kj"])
                    for p in dur_pts
                    if p.get("retention_20m_1800kj") is not None
                ]
                if pts_1800:
                    fig_dur.add_scatter(
                        x=[x[0] for x in pts_1800],
                        y=[x[1] for x in pts_1800],
                        mode="lines+markers",
                        name="Power Retention 20m @ 1800 kJ (%)",
                    )
                # EF retention 1500 kJ
                ef_pts_1500 = [
                    (p["date"], p["ef_retention_1500kj"])
                    for p in dur_pts
                    if p.get("ef_retention_1500kj") is not None
                ]
                if ef_pts_1500:
                    fig_dur.add_scatter(
                        x=[x[0] for x in ef_pts_1500],
                        y=[x[1] for x in ef_pts_1500],
                        mode="lines+markers",
                        name="EF Retention @ 1500 kJ (%)",
                    )
                # EF retention 1800 kJ
                ef_pts_1800 = [
                    (p["date"], p["ef_retention_1800kj"])
                    for p in dur_pts
                    if p.get("ef_retention_1800kj") is not None
                ]
                if ef_pts_1800:
                    fig_dur.add_scatter(
                        x=[x[0] for x in ef_pts_1800],
                        y=[x[1] for x in ef_pts_1800],
                        mode="lines+markers",
                        name="EF Retention @ 1800 kJ (%)",
                    )

                if pts_1500 or pts_1800 or ef_pts_1500 or ef_pts_1800:
                    fig_dur.update_layout(
                        title="Longitudinal Durability Retention Trend",
                        xaxis_title="Date",
                        yaxis_title="Retention (%)",
                    )
                    st.plotly_chart(fig_dur, width="stretch")
                else:
                    st.info(
                        "No complete retention data points accumulated in this period."
                    )

            # Stage 2: Decoupling, Weekly composition, Fatigued PDC, Threshold evidence
            st.markdown("---")
            st.markdown("### Stage 2: Aerobic Drift, Volume Composition & FTP Evidence")

            # 1. Decoupling trend
            st.markdown("#### Decoupling (Aerobic Drift) Trend")
            dec_pts = data.get("decoupling_trend", [])
            avail_dec = [
                p
                for p in dec_pts
                if p.get("available") and p.get("drift_percent") is not None
            ]
            if not avail_dec:
                st.info("No decoupling/drift points available in this period.")
            else:
                fig_dec = go.Figure()
                fig_dec.add_scatter(
                    x=[p["date"] for p in avail_dec],
                    y=[p["drift_percent"] for p in avail_dec],
                    mode="lines+markers",
                    name="Drift (%)",
                )
                fig_dec.add_hline(
                    y=5.0,
                    line_dash="dash",
                    line_color="orange",
                    annotation_text="5% threshold",
                )
                fig_dec.update_layout(
                    title="Aerobic Decoupling Trend",
                    xaxis_title="Date",
                    yaxis_title="Drift (%)",
                )
                st.plotly_chart(fig_dec, width="stretch")

            # 2. Weekly composition
            st.markdown("#### Weekly Composition & Zone Distribution")
            weekly = data.get("weekly_composition", [])
            if not weekly:
                st.info("No weekly composition data available.")
            else:
                fig_week = go.Figure()
                weeks = [w["iso_week"] for w in weekly]
                z1_pct = [w.get("three_zone_percentages", [0, 0, 0])[0] for w in weekly]
                z2_pct = [w.get("three_zone_percentages", [0, 0, 0])[1] for w in weekly]
                z3_pct = [w.get("three_zone_percentages", [0, 0, 0])[2] for w in weekly]

                fig_week.add_bar(x=weeks, y=z1_pct, name="Zone 1 (%)")
                fig_week.add_bar(x=weeks, y=z2_pct, name="Zone 2 (%)")
                fig_week.add_bar(x=weeks, y=z3_pct, name="Zone 3 (%)")
                fig_week.update_layout(
                    barmode="stack",
                    title="Weekly 3-Zone Intensity Composition (%)",
                    xaxis_title="ISO Week",
                    yaxis_title="Percentage (%)",
                )
                st.plotly_chart(fig_week, width="stretch")

                # Show contextual details & caveats per week
                weekly_summary = []
                for w in weekly:
                    caveats_text = "; ".join(w.get("caveats", [])) or "None"
                    longest = w.get("longest_ride") or {}
                    longest_str = (
                        f"{longest.get('duration_s', 0) / 3600:.1f}h ({longest.get('modality', 'N/A')})"
                        if longest
                        else "None"
                    )
                    weekly_summary.append(
                        {
                            "Week": w["iso_week"],
                            "Start Date": w["start_date"],
                            "Hours": w.get("total_hours"),
                            "Load": w.get("total_load"),
                            "Longest Ride": longest_str,
                            "Caveats": caveats_text,
                        }
                    )
                st.dataframe(weekly_summary)

            # 3. Fatigued PDC
            st.markdown("#### Fatigued Power-Duration Curve")
            fat_pdc = data.get("fatigued_pdc", {})
            maxima = fat_pdc.get("period_maxima", [])
            if not maxima:
                st.info("No fatigued PDC data available.")
            else:
                fig_fat = go.Figure()
                for item in maxima:
                    thresh_kj = item["threshold_kj"]
                    watts_map = item.get("watts", {})
                    if item.get("available") and watts_map:
                        pts = [
                            (int(d), w) for d, w in watts_map.items() if w is not None
                        ]
                        pts.sort(key=lambda x: x[0])
                        if pts:
                            fig_fat.add_scatter(
                                x=[_power_curve_position(d) for d, _ in pts],
                                y=[w for _, w in pts],
                                mode="lines+markers",
                                name=f"After {thresh_kj:g} kJ",
                                customdata=[
                                    [_format_power_curve_duration(d)] for d, _ in pts
                                ],
                                hovertemplate="Duration: %{customdata[0]}<br>Power: %{y:.0f} W<extra></extra>",
                            )
                    else:
                        st.caption(f"After {thresh_kj:g} kJ: {item.get('reason')}")

                if fig_fat.data:
                    fig_fat.update_layout(
                        title="Period Best Fatigued Power Curve",
                        xaxis_title="Duration",
                        yaxis_title="Power (W)",
                    )
                    st.plotly_chart(fig_fat, width="stretch")

            # 4. Threshold / FTP evidence trend
            st.markdown("#### Threshold / FTP Evidence Trend")
            thresh_pts = data.get("threshold_evidence_trend", [])
            if not thresh_pts:
                st.info("No threshold evidence points available.")
            else:
                fig_th = go.Figure()
                obs_pts = [
                    (p["date"], p["observed_20m_w"])
                    for p in thresh_pts
                    if p.get("observed_20m_w") is not None
                ]
                if obs_pts:
                    fig_th.add_scatter(
                        x=[x[0] for x in obs_pts],
                        y=[x[1] for x in obs_pts],
                        mode="lines+markers",
                        name="Observed 20m Power (W)",
                    )

                est_pts = [
                    (p["date"], p["ftp_estimate_w"])
                    for p in thresh_pts
                    if p.get("ftp_estimate_w") is not None
                ]
                if est_pts:
                    fig_th.add_scatter(
                        x=[x[0] for x in est_pts],
                        y=[x[1] for x in est_pts],
                        mode="lines+markers",
                        name="FTP Estimate (95% of 20m) (W)",
                    )

                dec_pts = [
                    (p["date"], p["declared_ftp_w"])
                    for p in thresh_pts
                    if p.get("declared_ftp_w") is not None
                ]
                if dec_pts:
                    fig_th.add_scatter(
                        x=[x[0] for x in dec_pts],
                        y=[x[1] for x in dec_pts],
                        mode="lines",
                        line=dict(dash="dash"),
                        name="Declared FTP (W)",
                    )

                if fig_th.data:
                    fig_th.update_layout(
                        title="FTP & 20-Minute Power Evidence Trend",
                        xaxis_title="Date",
                        yaxis_title="Power (W)",
                    )
                    st.plotly_chart(fig_th, width="stretch")
                else:
                    st.info("No observed 20-minute power or FTP evidence available.")

            with st.expander("Raw Progress Data"):
                st.json(progress_payload)
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
                    curve_durations = sorted(
                        set(PeriodPowerCurveRequest().durations)
                        | set(POWER_SKILL_DURATIONS)
                    )
                    result = service.period_power_curve(
                        PeriodPowerCurveRequest(
                            period=period,
                            modality=modality,
                            durations=curve_durations,
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
                    historical_data = (
                        result.data
                        if period == "all"
                        else _cached_all_time_power_skills(settings.data_dir, modality)[
                            "data"
                        ]
                    )
                    _render_power_skills(
                        st,
                        go,
                        historical_data,
                        result.data,
                        "Selected period",
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
                curve_durations = sorted(
                    set(CurveRequest(activity_id=ident).durations)
                    | set(POWER_SKILL_DURATIONS)
                )
                result = service.power_curve(
                    CurveRequest(activity_id=ident, durations=curve_durations)
                )
                curve = result.data.get("watts", {})
                chart = _power_curve_figure(go, curve, result.data.get("durations_s"))
                if chart is not None:
                    st.plotly_chart(chart, width="stretch")
                else:
                    st.warning("No power data available for this activity.")
                modality = chosen.get("modality") or "unknown"
                historical_data = _cached_all_time_power_skills(
                    settings.data_dir, modality
                )["data"]
                _render_power_skills(
                    st,
                    go,
                    historical_data,
                    result.data,
                    "Selected activity",
                )
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
                    _render_performance_durability(st, go, result_payload)
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
