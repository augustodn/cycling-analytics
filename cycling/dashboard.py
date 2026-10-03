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
    PowerHRRequest,
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
POWER_SKILL_LEVELS = (
    ("Aspiring", 1),
    ("Intermediate", 31),
    ("Athletic", 46),
    ("Sport", 56),
    ("Elite", 71),
    ("Semi-Pro", 85),
    ("National Star", 94),
    ("World Class", 98),
)
POWER_SKILL_REFERENCE_WATTS = {
    15: (165, 340, 395, 440, 515, 620, 765, 930),
    30: (155, 295, 345, 375, 435, 515, 625, 745),
    60: (145, 260, 295, 320, 365, 430, 510, 600),
    120: (130, 230, 265, 285, 325, 375, 440, 520),
    180: (125, 215, 245, 265, 300, 345, 405, 475),
    300: (115, 200, 225, 240, 270, 315, 365, 425),
    600: (105, 180, 205, 220, 250, 285, 335, 385),
    900: (105, 175, 200, 215, 240, 275, 320, 375),
    1200: (100, 175, 195, 210, 235, 270, 315, 365),
    1800: (100, 170, 190, 205, 230, 265, 310, 360),
    2700: (100, 170, 190, 205, 230, 265, 305, 355),
    3600: (100, 165, 190, 200, 225, 260, 305, 355),
}
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


@st.cache_data(ttl=300, max_entries=16, show_spinner=False)
def _cached_power_hr_zone_mismatch(
    data_dir: str, period: str, environment: str, parameter_mode: str
) -> dict:
    with Store(data_dir) as store:
        result = CyclingService(store).power_hr_zone_mismatch(
            period=period,
            environment=environment,
            parameter_mode=parameter_mode,
        )
        return result.model_dump(mode="json")


@st.cache_data(ttl=300, max_entries=16, show_spinner=False)
def _cached_power_hr(data_dir: str, request_json: str) -> dict:
    with Store(data_dir) as store:
        result = CyclingService(store).power_hr(
            PowerHRRequest.model_validate_json(request_json)
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
    degrees_per_interval = 360 / len(labels)
    sector_angles = []
    sector_widths = []
    sector_colors = []
    sector_start = 0
    for _, intervals, color in POWER_SKILL_GROUPS:
        interval_count = len(intervals)
        sector_angles.append(
            (sector_start + (interval_count - 1) / 2) * degrees_per_interval
        )
        sector_widths.append(interval_count * degrees_per_interval - 4)
        sector_colors.append(color)
        sector_start += interval_count

    interval_angles = [index * degrees_per_interval for index in range(len(labels))]
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
    figure = go.Figure()
    for index, (skill, intervals, _) in enumerate(POWER_SKILL_GROUPS):
        figure.add_trace(
            go.Barpolar(
                r=[ring_width],
                theta=[sector_angles[index]],
                width=[sector_widths[index]],
                base=[ring_base],
                marker_color=[sector_colors[index]],
                marker_line_width=0,
                hoverinfo="skip",
                name=f"{skill} ({intervals[0][1]}–{intervals[-1][1]})",
            )
        )
    figure.add_trace(
        go.Scatterpolar(
            r=history,
            theta=interval_angles,
            customdata=labels,
            mode="lines+markers",
            fill="toself",
            fillcolor="rgba(126, 68, 165, 0.62)",
            line={"color": "#7E44A5", "width": 2},
            connectgaps=False,
            name="All-time maximum",
            hovertemplate="%{customdata}: %{r:.0f} W<extra>%{fullData.name}</extra>",
        )
    )
    figure.add_trace(
        go.Scatterpolar(
            r=selected,
            theta=interval_angles,
            customdata=labels,
            mode="lines+markers",
            fill="toself",
            fillcolor="rgba(40, 150, 220, 0.25)",
            line={"color": "#2896DC", "width": 2},
            connectgaps=False,
            name=selected_label,
            hovertemplate="%{customdata}: %{r:.0f} W<extra>%{fullData.name}</extra>",
        )
    )
    figure.add_trace(
        go.Scatterpolar(
            r=[max_watts * 1.29 if value is not None else None for value in history],
            theta=interval_angles,
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
        legend={
            "orientation": "v",
            "x": 1.03,
            "xanchor": "left",
            "y": 1,
            "yanchor": "top",
        },
        margin={"l": 35, "r": 185, "t": 55, "b": 45},
        polar={
            "radialaxis": {
                "visible": True,
                "range": [0, max_watts * 1.55],
                "ticksuffix": " W",
                "gridcolor": "rgba(128, 128, 128, 0.35)",
            },
            "angularaxis": {
                "type": "linear",
                "tickmode": "array",
                "tickvals": interval_angles,
                "ticktext": labels,
                "direction": "clockwise",
                "rotation": 90,
            },
        },
    )
    return figure


def _estimate_power_skill_percentile(watts: float, duration_s: int) -> float:
    references = POWER_SKILL_REFERENCE_WATTS[duration_s]
    if watts <= 0:
        return 0.0
    if watts < references[0]:
        return watts / references[0]

    for index in range(1, len(references)):
        if watts <= references[index]:
            lower_watts, upper_watts = references[index - 1 : index + 1]
            lower_percentile = POWER_SKILL_LEVELS[index - 1][1]
            upper_percentile = POWER_SKILL_LEVELS[index][1]
            fraction = (watts - lower_watts) / (upper_watts - lower_watts)
            return lower_percentile + fraction * (upper_percentile - lower_percentile)
    return float(POWER_SKILL_LEVELS[-1][1])


def _power_skill_assessments(historical_watts: dict) -> list[dict]:
    assessments = []
    for skill, intervals, color in POWER_SKILL_GROUPS:
        percentiles = [
            _estimate_power_skill_percentile(float(watts), duration)
            for duration, _ in intervals
            if (watts := historical_watts.get(str(duration))) is not None
        ]
        percentile = sum(percentiles) / len(percentiles) if percentiles else None
        level, level_number = "Below Aspiring", 0
        if percentile is not None:
            for index, (name, cutoff) in enumerate(POWER_SKILL_LEVELS, start=1):
                if percentile >= cutoff:
                    level, level_number = name, index

        progress = (
            0.0
            if percentile is None
            else max(0.0, min(100.0, (percentile - 1) / (98 - 1) * 100))
        )
        assessments.append(
            {
                "skill": skill,
                "color": color,
                "percentile": percentile,
                "level": level,
                "level_number": level_number,
                "progress": progress,
                "intervals_available": len(percentiles),
                "intervals_total": len(intervals),
            }
        )
    return assessments


def _power_skill_progress_figure(go, assessments: list[dict]):
    figure = go.Figure()
    skills = [item["skill"] for item in assessments]
    figure.add_bar(
        x=[100] * len(assessments),
        y=skills,
        orientation="h",
        marker_color="rgba(128, 128, 128, 0.3)",
        hoverinfo="skip",
        showlegend=False,
        name="P1 to P98 range",
    )
    figure.add_bar(
        x=[item["progress"] for item in assessments],
        y=skills,
        orientation="h",
        marker_color=[item["color"] for item in assessments],
        customdata=[
            [
                item["level"],
                item["level_number"],
                item["percentile"] if item["percentile"] is not None else 0,
                item["intervals_available"],
                item["intervals_total"],
            ]
            for item in assessments
        ],
        hovertemplate=(
            "%{y}: %{customdata[0]} · Level %{customdata[1]}"
            " · P%{customdata[2]:.1f}<br>"
            "%{customdata[3]}/%{customdata[4]} intervals<extra></extra>"
        ),
        showlegend=False,
        name="Estimated progress",
    )

    for _, percentile in POWER_SKILL_LEVELS[1:]:
        position = (percentile - 1) / (98 - 1) * 100
        figure.add_vline(
            x=position,
            line_color="rgba(128, 128, 128, 0.8)",
            line_width=1,
        )

    for item in assessments:
        if item["percentile"] is None:
            text = "No data"
        else:
            rank = "<P1" if item["percentile"] < 1 else f"P{item['percentile']:.0f}"
            text = (
                f"{item['level']} · Level {item['level_number']} · {rank} · "
                f"{item['progress']:.0f}% "
                f"({item['intervals_available']}/{item['intervals_total']})"
            )
        figure.add_annotation(
            x=102,
            y=item["skill"],
            xref="x",
            yref="y",
            text=text,
            showarrow=False,
            xanchor="left",
            align="left",
        )

    figure.update_layout(
        title="Estimated skill level",
        barmode="overlay",
        height=260,
        margin={"l": 100, "r": 15, "t": 45, "b": 15},
        xaxis={"range": [0, 160], "visible": False, "fixedrange": True},
        yaxis={
            "categoryorder": "array",
            "categoryarray": skills,
            "autorange": "reversed",
            "fixedrange": True,
        },
    )
    return figure


def _power_skill_benchmark_table(skill: str, current_watts: dict) -> list[dict]:
    _, intervals, _ = next(group for group in POWER_SKILL_GROUPS if group[0] == skill)
    rows = []
    for duration, label in intervals:
        row = {"Interval": label}
        row.update(
            {
                f"{name} P{percentile}": f"{POWER_SKILL_REFERENCE_WATTS[duration][index]} W"
                for index, (name, percentile) in enumerate(POWER_SKILL_LEVELS)
            }
        )
        watts = current_watts.get(str(duration))
        row["Current best (W)"] = f"{float(watts):.0f} W" if watts is not None else "—"
        rows.append(row)
    return rows


def _render_power_skills(
    st, go, historical_data: dict, selected_data: dict, selected_label: str
) -> None:
    st.subheader("Power Skills")
    st.caption(
        "The ring legend identifies Sprinting (15s–1m), Attacking (2–10m), and "
        "Climbing (15–60m). Solid fill: all-time maximum; translucent fill: selection."
    )
    historical_watts = historical_data.get("watts", {})
    figure = _power_skills_figure(
        go,
        historical_watts,
        selected_data.get("watts", {}),
        selected_label,
    )
    if figure is None:
        st.info("No valid power efforts are available for the Power Skills intervals.")
        return
    st.plotly_chart(figure, width="stretch")

    assessments = _power_skill_assessments(historical_watts)
    st.plotly_chart(_power_skill_progress_figure(go, assessments), width="stretch")
    st.caption(
        "Each available all-time duration contributes equally; missing intervals are "
        "omitted. Percentiles interpolate between your cutoffs and cap at P98. The fill "
        "maps P1 to 0% and P98 to 100%; levels are estimates, not official Strava ratings."
    )
    st.subheader("Reference levels and current values")
    st.caption(f"Current best values use the selected scope: {selected_label}.")
    current_watts = selected_data.get("watts", {})
    for skill, _, _ in POWER_SKILL_GROUPS:
        st.markdown(f"#### {skill}")
        st.dataframe(
            _power_skill_benchmark_table(skill, current_watts),
            width="stretch",
            hide_index=True,
        )


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


def _power_hr_mismatch_figure(go, data: dict):
    points = data.get("points", [])
    if not points:
        return None
    figure = go.Figure(
        go.Scatter(
            x=[point["timestamp"] for point in points],
            y=[point["mismatch"] for point in points],
            mode="markers",
            marker={"size": 5, "opacity": 0.45},
            customdata=[
                [
                    point["representative_power_w"],
                    point["representative_hr_bpm"],
                    point["power_zone"],
                    point["hr_zone"],
                ]
                for point in points
            ],
            hovertemplate=(
                "%{x}<br>Mismatch: %{y:+.0f} zones"
                "<br>Power: %{customdata[0]:.0f} W (Z%{customdata[2]})"
                "<br>HR: %{customdata[1]:.0f} bpm (Z%{customdata[3]})"
                "<extra></extra>"
            ),
            name="Valid windows",
        )
    )
    figure.add_scatter(
        x=[point["timestamp"] for point in points],
        y=[point["mismatch_30d"] for point in points],
        mode="lines",
        line={"width": 3},
        name="30-day rolling mean",
    )
    figure.add_hline(y=0, line_dash="dash", line_color="gray")
    figure.update_layout(
        title="HR–Power Zone Mismatch",
        xaxis_title="Date",
        yaxis_title="Power zone − HR zone",
        yaxis={"dtick": 1},
    )
    return figure


def _power_hr_mismatch_matrix(go, data: dict):
    seconds = data.get("matrix_seconds", [])
    if not seconds or not any(any(row) for row in seconds):
        return None
    values = [[value / 60 for value in row] for row in seconds]
    text = [[f"{value:.0f}" if value else "" for value in row] for row in values]
    figure = go.Figure(
        go.Heatmap(
            z=values,
            x=[zone["label"] for zone in data.get("power_zones", [])],
            y=[zone["label"] for zone in data.get("hr_zones", [])],
            text=text,
            texttemplate="%{text}",
            colorscale="Blues",
            colorbar={"title": "Minutes"},
            hovertemplate="%{y} × %{x}: %{z:.0f} min<extra></extra>",
        )
    )
    figure.update_layout(
        title="Valid exposure by HR and power zone",
        xaxis_title="Power zone",
        yaxis_title="Heart-rate zone",
    )
    return figure


def _power_hr_curve_figure(go, curves: list[dict], hr_zone_bounds: list[float]):
    if not any(curve.get("bins") for curve in curves):
        return None

    figure = go.Figure()
    colors = ["#167c52", "#dc7433", "#5966bd", "#b65d21", "#8d3478"]
    for index, curve in enumerate(curves):
        bins = curve.get("bins", [])
        if not bins:
            continue
        medians = [point["hr_median_bpm"] for point in bins]
        figure.add_scatter(
            x=[point["power_w"] for point in bins],
            y=medians,
            mode="lines+markers",
            name=curve["name"],
            line={
                "color": colors[index % len(colors)],
                "dash": "dash" if curve.get("comparison") else "solid",
            },
            error_y={
                "type": "data",
                "symmetric": False,
                "array": [
                    max(0, point["hr_p75_bpm"] - point["hr_median_bpm"])
                    for point in bins
                ],
                "arrayminus": [
                    max(0, point["hr_median_bpm"] - point["hr_p25_bpm"])
                    for point in bins
                ],
                "visible": True,
                "thickness": 1,
                "width": 2,
            },
            customdata=[
                [
                    point["power_low_w"],
                    point["power_high_w"],
                    point.get("activity_count", 1),
                    point["valid_seconds"],
                    point.get("status", "activity"),
                    point["hr_p25_bpm"],
                    point["hr_p75_bpm"],
                    curve.get("detail", ""),
                ]
                for point in bins
            ],
            hovertemplate=(
                "%{customdata[0]:.0f}–%{customdata[1]:.0f} W"
                "<br>Median HR: %{y:.1f} bpm"
                "<br>P25–P75: %{customdata[5]:.1f}–%{customdata[6]:.1f} bpm"
                "<br>Activities: %{customdata[2]}"
                "<br>Valid: %{customdata[3]} s"
                "<br>Confidence: %{customdata[4]}<br>%{customdata[7]}"
                "<extra>%{fullData.name}</extra>"
            ),
        )

    zone_colors = list(HR_ZONE_COLORS.values())
    for index, bound in enumerate(hr_zone_bounds):
        figure.add_hline(
            y=bound,
            line_color=zone_colors[min(index + 1, len(zone_colors) - 1)],
            line_width=2,
            line_dash="dot",
            annotation_text=f"HR zone boundary · {bound:g} bpm",
            annotation_position="top left",
        )
    figure.update_layout(
        title="Power vs. heart rate",
        xaxis_title="Power (W)",
        yaxis_title="Heart rate (bpm)",
        hovermode="closest",
    )
    return figure


def _power_hr_trend_figure(go, periods: list[dict], targets: list[int]):
    if not any(period.get("points") for period in periods):
        return None
    figure = go.Figure()
    colors = ["#167c52", "#5966bd", "#dc7433", "#8d3478", "#246c83", "#b65d21"]
    for target_index, target in enumerate(targets):
        for period in periods:
            points = sorted(
                (
                    point
                    for point in period.get("points", [])
                    if point["target_power_w"] == target
                ),
                key=lambda point: point["date"],
            )
            activity_points = [
                point for point in points if point["hr_median_bpm"] is not None
            ]
            rolling_points = [
                point
                for point in points
                if point.get("rolling_median_hr_bpm") is not None
            ]
            if not activity_points and not rolling_points:
                continue
            if activity_points:
                figure.add_scatter(
                    x=[point["date"] for point in activity_points],
                    y=[point["hr_median_bpm"] for point in activity_points],
                    mode="markers",
                    name=f"{period['name']} · {target} W per activity",
                    line={"color": colors[target_index % len(colors)]},
                    customdata=[
                        [point["activity_id"], point["valid_seconds"]]
                        for point in activity_points
                    ],
                    hovertemplate=(
                        "%{x}<br>HR @ %{fullData.name}: %{y:.1f} bpm"
                        "<br>Activity: %{customdata[0]}"
                        "<br>Valid: %{customdata[1]} s<extra></extra>"
                    ),
                )
            if rolling_points:
                figure.add_scatter(
                    x=[point["date"] for point in rolling_points],
                    y=[point["rolling_median_hr_bpm"] for point in rolling_points],
                    mode="lines+markers",
                    name=f"{period['name']} · {target} W 3-activity median",
                    line={
                        "color": colors[target_index % len(colors)],
                        "dash": "dash" if period.get("comparison") else "solid",
                    },
                )
    figure.update_layout(
        title="Heart rate at fixed power",
        xaxis_title="Activity date",
        yaxis_title="Heart rate (bpm)",
        hovermode="closest",
    )
    return figure


def _power_hr_delta_rows(first_group: dict | None, second_group: dict | None):
    if first_group is None or second_group is None:
        return []
    first = {point["power_low_w"]: point for point in first_group.get("bins", [])}
    second = {point["power_low_w"]: point for point in second_group.get("bins", [])}
    rows = []
    for low_w in sorted(first.keys() & second.keys()):
        earlier, later = first[low_w], second[low_w]
        if earlier.get("status") != "ok" or later.get("status") != "ok":
            continue
        rows.append(
            {
                "Power (W)": f"{low_w:g}–{earlier['power_high_w']:g}",
                "Earlier HR (bpm)": earlier["hr_median_bpm"],
                "Later HR (bpm)": later["hr_median_bpm"],
                "Δ HR (bpm)": round(
                    later["hr_median_bpm"] - earlier["hr_median_bpm"], 1
                ),
                "Earlier activities": earlier["activity_count"],
                "Later activities": later["activity_count"],
            }
        )
    return rows


def _power_hr_groups(bucket: dict, activity_id: str | None, grouping: str):
    if activity_id:
        groups = bucket.get("activities", {}).get(activity_id, {}).get("analysis", {})
        groups = groups.get("groups", []) if groups else []
    else:
        groups = bucket.get("groups", [])
    expected_kind = {
        "All samples": "all",
        "By accumulated work": "work_band",
        "First vs second half": "elapsed_split",
        "First/middle/last third": "elapsed_split",
    }[grouping]
    return [group for group in groups if group.get("kind") == expected_kind]


def _power_hr_period_name(group: dict):
    if group.get("kind") == "work_band":
        start = group.get("start_kj", 0)
        end = group.get("end_kj")
        label = (
            "Fresh"
            if start == 0
            else "Early endurance"
            if start < 1000
            else "Meaningful fatigue"
            if start < 1500
            else "Late-race"
            if start < 1800
            else "Deep fatigue"
        )
        return f"{label} · {start:g}–{end if end is not None else '∞'} kJ"
    if group.get("kind") == "elapsed_split":
        parts = group.get("group_id", "").split("_")
        part, total = parts[1], parts[-1]
        labels = {
            "2": ("First half", "Second half"),
            "3": ("First third", "Middle third", "Final third"),
        }
        return labels[total][int(part) - 1]
    return "All eligible samples"


def _render_power_hr_view(
    st, go, activities: list[dict], data_dir: str, parameter_mode: str
):
    st.subheader("Power ↔ Heart Rate")
    st.caption(
        "Descriptive, lag-aligned evidence. HR at fixed power and Power–HR curves "
        "are not causal fitness tests. Indoor and outdoor are analyzed separately."
    )
    dates = [date.fromisoformat(item["start_time"][:10]) for item in activities]
    latest = max(dates)
    cycling_activities = [
        item
        for item in activities
        if item.get("modality", "unknown")
        in {"indoor", "road", "mtb", "gravel", "unknown"}
    ]
    if not cycling_activities:
        st.info("No cycling activities are available for Power–HR analysis.")
        return
    default_end = latest
    default_start = latest - timedelta(days=27)
    earlier_end = default_start - timedelta(days=1)
    earlier_start = earlier_end - timedelta(days=27)

    with st.form("power_hr_controls"):
        view = st.selectbox(
            "Mode",
            ["Single activity", "Period", "Compare periods"],
            index=1,
            key="power_hr_view",
        )
        activity_id = None
        period_data = {}
        compare_data = None
        if view == "Single activity":
            options = sorted(
                cycling_activities, key=lambda item: item["start_time"], reverse=True
            )
            activity_by_id = {item["id"]: item for item in options}
            activity_id = st.selectbox(
                "Activity",
                list(activity_by_id),
                format_func=lambda item: (
                    f"{activity_by_id[item]['start_time'][:10]} · "
                    f"{activity_by_id[item].get('source_name', item)} · "
                    f"{activity_by_id[item].get('modality', 'unknown')}"
                ),
                key="power_hr_activity",
            )
        elif view == "Period":
            period = st.selectbox(
                "Period", ["7d", "28d", "42d", "90d", "all", "custom"], index=1
            )
            if period == "custom":
                period_data = None
                period_range = st.date_input(
                    "Period date range", value=(default_start, default_end)
                )
            else:
                period_range = None
                if period != "all":
                    period_data = {"period": period, "end_date": default_end}
                else:
                    period_data = {"period": period}
            if (
                period_range is not None
                and isinstance(period_range, (tuple, list))
                and len(period_range) == 2
            ):
                period_data = {
                    "period": "custom",
                    "start_date": period_range[0],
                    "end_date": period_range[1],
                }
        else:
            period_data = None
            earlier_range = st.date_input(
                "Period A (earlier)", value=(earlier_start, earlier_end)
            )
            current_range = st.date_input(
                "Period B (current)", value=(default_start, default_end)
            )
            if (
                isinstance(earlier_range, (tuple, list))
                and len(earlier_range) == 2
                and isinstance(current_range, (tuple, list))
                and len(current_range) == 2
            ):
                period_data = {
                    "period": "custom",
                    "start_date": current_range[0],
                    "end_date": current_range[1],
                }
                compare_data = {
                    "period": "custom",
                    "start_date": earlier_range[0],
                    "end_date": earlier_range[1],
                }

        environment = st.selectbox("Environment", ["both", "indoor", "outdoor"])
        curve = st.selectbox("Curve", ["observed", "stable"])
        grouping = st.selectbox(
            "Fatigue grouping",
            [
                "All samples",
                "By accumulated work",
                "First vs second half",
                "First/middle/last third",
            ],
        )
        with st.expander("Analysis settings"):
            lag_s = st.slider("HR lag (seconds)", 15, 60, 30)
            power_window_s = st.number_input(
                "Power rolling mean (seconds)", 1, 3600, 30
            )
            hr_window_s = st.number_input("HR rolling mean (seconds)", 1, 3600, 30)
            bin_size_w = st.number_input("Power bin width (W)", 1.0, 100.0, 10.0, 1.0)
            stable_window_s = st.number_input("Stable window (seconds)", 30, 3600, 180)
            max_power_cv = st.number_input(
                "Maximum power CV (fraction)", 0.01, 1.0, 0.08, 0.01
            )
            min_power_ftp_fraction = st.number_input(
                "Minimum stable power (FTP fraction)", 0.0, 1.0, 0.4, 0.01
            )
            min_cadence_rpm = st.number_input(
                "Minimum stable cadence (rpm)", 0, 200, 50
            )
            aerobic_only = st.checkbox("Restrict to 50–90% FTP", value=False)
            min_activities = st.number_input(
                "Minimum activities per bin",
                1,
                100,
                1 if view == "Single activity" else 3,
            )
            min_total_seconds = st.number_input(
                "Minimum valid seconds per bin", 0, 86400, 300
            )
            targets_text = st.text_input(
                "Fixed power targets (W)", "180,190,200,210,220,240"
            )
            fatigue_text = st.text_input(
                "Work band boundaries (kJ)", "500,1000,1500,1800"
            )
        submitted = st.form_submit_button("Analyze")

    if not submitted:
        st.info("Choose analysis settings and select Analyze.")
        return
    if period_data is None:
        st.error("Choose valid dates for the selected period.")
        return

    try:
        targets = [
            int(value.strip()) for value in targets_text.split(",") if value.strip()
        ]
        fatigue_thresholds = [
            float(value.strip()) for value in fatigue_text.split(",") if value.strip()
        ]
        payload = {
            **period_data,
            "activity_id": activity_id,
            "compare_period": compare_data,
            "environment": environment,
            "parameter_mode": parameter_mode,
            "mode": curve,
            "lag_s": lag_s,
            "power_window_s": power_window_s,
            "hr_window_s": hr_window_s,
            "bin_size_w": bin_size_w,
            "stable_window_s": stable_window_s,
            "max_power_cv": max_power_cv,
            "min_power_ftp_fraction": min_power_ftp_fraction,
            "min_cadence_rpm": min_cadence_rpm,
            "aerobic_floor_ftp_fraction": 0.5 if aerobic_only else None,
            "aerobic_ceiling_ftp_fraction": 0.9 if aerobic_only else None,
            "fatigue_thresholds_kj": fatigue_thresholds
            if grouping == "By accumulated work"
            else [],
            "elapsed_splits": 2
            if grouping == "First vs second half"
            else 3
            if grouping == "First/middle/last third"
            else None,
            "min_activities": min_activities,
            "min_total_seconds": min_total_seconds,
            "target_power_w": targets,
        }
        request = PowerHRRequest.model_validate(payload)
        result = _cached_power_hr(data_dir, request.model_dump_json(exclude_unset=True))
        data = result["data"]
    except (ValueError, TypeError) as exc:
        st.error(f"Power–HR analysis unavailable: {exc}")
        return
    except Exception as exc:
        st.error(f"Power–HR analysis unavailable: {exc}")
        return

    environments = (
        [environment] if environment != "both" else ["indoor", "outdoor", "unknown"]
    )
    periods = [
        (
            "Selected activity"
            if activity_id
            else "Period B / current"
            if data.get("comparison_period")
            else "Selected period",
            data["current_period"],
        ),
        ("Period A / earlier", data.get("comparison_period")),
    ]
    for environment_name in environments:
        available_periods = [
            (label, period["environments"].get(environment_name))
            for label, period in periods
            if period and period.get("environments", {}).get(environment_name)
        ]
        if not available_periods or not any(
            bucket.get("activities") for _, bucket in available_periods
        ):
            continue
        st.markdown(f"### {environment_name.title()}")
        references = sorted(
            (
                activity
                for _, bucket in available_periods
                for activity in bucket.get("activities", {}).values()
                if activity.get("hr_zone_bounds")
            ),
            key=lambda item: item["date"],
        )
        zone_bounds = references[-1]["hr_zone_bounds"] if references else []
        if references:
            reference = references[-1]
            st.caption(
                f"Zone lines use {reference['date']} effective HR boundaries "
                f"({', '.join(str(value) for value in zone_bounds)} bpm). "
                "Historical boundaries can vary between activities."
            )
        curves = []
        selected_groups_by_period = {}
        for label, bucket in available_periods:
            groups = _power_hr_groups(bucket, activity_id, grouping)
            activity_info = bucket.get("activities", {}).get(activity_id)
            activity_detail = ""
            if activity_info:
                context = activity_info.get("context", {})
                activity_detail = " · ".join(
                    value
                    for value in (
                        f"{activity_info['date']} · {activity_info['modality']}",
                        f"{activity_info['duration_s'] / 60:.0f} min",
                        f"FTP {activity_info['effective_ftp_w']:.0f} W"
                        if activity_info.get("effective_ftp_w") is not None
                        else None,
                        f"avg {context['avg_power_w']:.0f} W"
                        if context.get("avg_power_w") is not None
                        else None,
                        f"{context['avg_hr_bpm']:.0f} bpm avg HR"
                        if context.get("avg_hr_bpm") is not None
                        else None,
                        f"{activity_info['total_work_kj']:.0f} kJ"
                        if activity_info.get("total_work_kj") is not None
                        else None,
                        f"{context['temperature_c']:.0f} °C"
                        if context.get("temperature_c") is not None
                        else None,
                    )
                    if value
                )
            selected_groups_by_period[label] = {
                group["group_id"]: group for group in groups
            }
            for group in groups:
                curves.append(
                    {
                        "name": f"{label} · {_power_hr_period_name(group)}",
                        "comparison": label.startswith("Period A"),
                        "bins": group.get("bins", []),
                        "detail": activity_detail,
                    }
                )
        figure = _power_hr_curve_figure(go, curves, zone_bounds)
        if figure is None:
            st.info("No eligible paired power/HR samples for this selection.")
        else:
            st.plotly_chart(figure, width="stretch")

        trend_periods = [
            {
                "name": label,
                "comparison": label.startswith("Period A"),
                "points": bucket.get("hr_at_fixed_power", []),
            }
            for label, bucket in available_periods
        ]
        trend = _power_hr_trend_figure(go, trend_periods, targets)
        if trend is not None:
            for index, bound in enumerate(zone_bounds):
                zone_colors = list(HR_ZONE_COLORS.values())
                trend.add_hline(
                    y=bound,
                    line_color=zone_colors[min(index + 1, len(zone_colors) - 1)],
                    line_width=1,
                    line_dash="dot",
                    annotation_text=f"HR zone · {bound:g} bpm",
                )
            st.plotly_chart(trend, width="stretch")
            st.caption(
                f"HR at fixed power uses aligned samples (lag {lag_s}s), "
                "one point per activity; missing matches are not interpolated."
            )
            st.dataframe(
                [
                    {
                        "Period": label,
                        "Date": point["date"],
                        "Activity": point["activity_id"],
                        "Target power (W)": point["target_power_w"],
                        "Median HR (bpm)": point["hr_median_bpm"],
                        "3-activity rolling median HR": point["rolling_median_hr_bpm"],
                        "Efficiency (W/bpm)": point["efficiency_w_per_bpm"],
                        "Matched power (W)": point["matched_power_median_w"],
                        "Valid seconds": point["valid_seconds"],
                        "Confidence": point["status"],
                    }
                    for label, bucket in available_periods
                    for point in bucket.get("hr_at_fixed_power", [])
                ],
                hide_index=True,
            )

        if data.get("comparison_period"):
            earlier = selected_groups_by_period.get("Period A / earlier", {})
            current = selected_groups_by_period.get("Period B / current", {})
            delta_rows = [
                {"Curve": _power_hr_period_name(current[group_id]), **row}
                for group_id in current.keys() & earlier.keys()
                for row in _power_hr_delta_rows(earlier[group_id], current[group_id])
            ]
            if delta_rows:
                st.markdown(
                    "**Current − earlier HR at matched power (sufficient-confidence bins)**"
                )
                st.dataframe(delta_rows, hide_index=True)

        st.dataframe(
            [
                {
                    "Period": label,
                    "Date": activity["date"],
                    "Activity": activity.get("context", {}).get(
                        "source_name", activity["activity_id"]
                    ),
                    "Modality": activity["modality"],
                    "Duration (min)": round(activity["duration_s"] / 60),
                    "Average power (W)": activity["context"].get("avg_power_w"),
                    "Average HR (bpm)": activity["context"].get("avg_hr_bpm"),
                    "Temperature (°C)": activity["context"].get("temperature_c"),
                    "FTP (W)": activity["effective_ftp_w"],
                    "Work (kJ)": activity["total_work_kj"],
                    "HR bounds (bpm)": activity["hr_zone_bounds"],
                    "Data status": activity["reason"] or "available",
                }
                for label, bucket in available_periods
                for activity in bucket.get("activities", {}).values()
            ],
            hide_index=True,
        )
    if environment == "both":
        current_environments = data["current_period"]["environments"]
        indoor_groups = {
            item["group_id"]: item
            for item in current_environments.get("indoor", {}).get("groups", [])
        }
        outdoor_groups = {
            item["group_id"]: item
            for item in current_environments.get("outdoor", {}).get("groups", [])
        }
        penalty = []
        for group_id in indoor_groups.keys() & outdoor_groups.keys():
            indoor_bins = {
                point["power_low_w"]: point for point in indoor_groups[group_id]["bins"]
            }
            outdoor_bins = {
                point["power_low_w"]: point
                for point in outdoor_groups[group_id]["bins"]
            }
            for low_w in indoor_bins.keys() & outdoor_bins.keys():
                inside, outside = indoor_bins[low_w], outdoor_bins[low_w]
                if inside.get("status") == outside.get("status") == "ok":
                    penalty.append(
                        {
                            "Curve": _power_hr_period_name(indoor_groups[group_id]),
                            "Power (W)": f"{low_w:g}–{inside['power_high_w']:g}",
                            "Indoor HR": inside["hr_median_bpm"],
                            "Outdoor HR": outside["hr_median_bpm"],
                            "Indoor penalty (bpm)": round(
                                inside["hr_median_bpm"] - outside["hr_median_bpm"], 1
                            ),
                        }
                    )
        if penalty:
            st.markdown("### Indoor vs outdoor HR penalty")
            st.dataframe(penalty, hide_index=True)
    st.caption(
        data.get(
            "caveat",
            "Heat, hydration, terrain, sensors and lag are potential confounders.",
        )
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
                "FTP Calibration",
                "Power ↔ Heart Rate",
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

        if page == "Power ↔ Heart Rate":
            _render_power_hr_view(st, go, activities, settings.data_dir, mode)
            return

        if page == "FTP Calibration":
            st.subheader("FTP calibration evidence")
            st.caption(
                "Zone mismatch is an accumulating comparison, not an FTP test. "
                "Indoor and outdoor data are analyzed separately."
            )
            period_col, environment_col = st.columns(2)
            period = period_col.selectbox(
                "Period", ["30d", "90d", "365d", "all"], index=1
            )
            environment = environment_col.selectbox(
                "Environment", ["indoor", "outdoor"], index=1
            )
            try:
                with st.spinner("Analyzing HR–power windows..."):
                    result = _cached_power_hr_zone_mismatch(
                        settings.data_dir, period, environment, mode
                    )
                data = result["data"]
            except Exception as exc:
                st.error(f"FTP calibration evidence unavailable: {exc}")
                return

            chart_col, evidence_col = st.columns([2, 1])
            with chart_col:
                figure = _power_hr_mismatch_figure(go, data)
                if figure is None:
                    st.info("No windows meet the data-coverage criteria.")
                else:
                    st.plotly_chart(figure, width="stretch")
                st.caption(
                    "Each point is an 8-minute window stepped every minute. Windows are "
                    "valid with ≥90% power and HR coverage. Power uses a 5%-trimmed mean; "
                    "HR uses the median. Stability and cadence affect quality, not validity."
                )
            with evidence_col:
                valid_hours = data.get("valid_seconds", 0) / 3600
                mismatch_30d = data.get("mismatch_30d")
                st.metric(
                    "30-day mismatch",
                    f"{mismatch_30d:+.2f}" if mismatch_30d is not None else "N/A",
                )
                st.metric(
                    "Valid sampled exposure",
                    f"{valid_hours:.1f} h · {data.get('valid_windows', 0)} windows",
                )
                st.metric(
                    "HR Z2 / power Z3",
                    f"{data['hr_zone_2_power_zone_3_pct']:.1f}%"
                    if data.get("hr_zone_2_power_zone_3_pct") is not None
                    else "N/A",
                )
                st.metric(
                    "Median Pw:HR drift",
                    f"{data['median_pw_hr_drift_pct']:.1f}%"
                    if data.get("median_pw_hr_drift_pct") is not None
                    else "N/A",
                )
                st.metric("Data-volume confidence", data.get("data_confidence", "LOW"))
                st.caption(
                    f"Drift available in {data.get('pw_hr_drift_activities', 0)} activities. "
                    "Confidence reflects valid-window volume only, not physiological certainty."
                )

                declared_ftp = data.get("declared_ftp_w")
                observed_ftp = data.get("best_20m_ftp_estimate_w")
                st.markdown("**Power-duration evidence**")
                st.write(
                    f"Declared FTP: {declared_ftp:.0f} W"
                    if declared_ftp is not None
                    else "Declared FTP: unavailable"
                )
                st.write(
                    f"Best observed 20 min: {data['best_observed_20m_w']:.0f} W"
                    if data.get("best_observed_20m_w") is not None
                    else "Best observed 20 min: unavailable"
                )
                st.write(
                    f"Best observed 60 min: {data['best_observed_60m_w']:.0f} W"
                    if data.get("best_observed_60m_w") is not None
                    else "Best observed 60 min: unavailable"
                )
                st.write(
                    f"95% of best 20 min: {observed_ftp:.0f} W"
                    if observed_ftp is not None
                    else "95% of best 20 min: unavailable"
                )
                st.caption(
                    "20-minute estimate is a low-confidence heuristic, not a measured FTP."
                )

                if mismatch_30d is None:
                    st.info("Not enough valid windows to assess mismatch direction.")
                elif mismatch_30d >= 0.3:
                    st.info(
                        "Positive zone bias can support an under-set FTP hypothesis; "
                        "it does not justify changing FTP by itself."
                    )
                elif mismatch_30d <= -0.3:
                    st.info(
                        "Negative zone bias can reflect an over-set FTP, fatigue, heat, "
                        "or hydration; inspect recovery and sustained efforts."
                    )
                else:
                    st.info("No strong average zone bias; this does not validate FTP.")

            diagnostics = data.get("diagnostics", {})
            st.subheader("Window diagnostics")
            candidate_col, valid_col, rejected_col = st.columns(3)
            candidate_col.metric(
                "Candidate windows", diagnostics.get("candidate_windows", 0)
            )
            valid_col.metric("Valid windows", diagnostics.get("valid_windows", 0))
            rejected_col.metric(
                "Rejected windows", diagnostics.get("rejected_windows", 0)
            )
            reason_labels = {
                "insufficient_samples": "Insufficient samples",
                "missing_power": "Missing power coverage",
                "missing_hr": "Missing HR coverage",
                "missing_parameters": "Missing zone settings",
            }
            reason_data = [
                {
                    "Reason": label,
                    "Windows": diagnostics.get("rejected_by_reason", {}).get(key, 0),
                }
                for key, label in reason_labels.items()
            ]
            quality_data = [
                {"Quality (valid only)": level, "Windows": count}
                for level, count in diagnostics.get("quality_counts", {}).items()
            ]
            diagnostic_col, quality_col = st.columns(2)
            with diagnostic_col:
                st.dataframe(reason_data, hide_index=True)
                st.caption(
                    "Coverage failures can overlap when a window lacks both sensors: "
                    f"power {diagnostics.get('coverage_failures', {}).get('power', 0)}, "
                    f"HR {diagnostics.get('coverage_failures', {}).get('hr', 0)}. "
                    f"Inactive pause seconds: {diagnostics.get('inactive_seconds', 0)}; "
                    f"uncovered seconds: {diagnostics.get('uncovered_seconds', 0)}."
                )
            with quality_col:
                st.dataframe(quality_data, hide_index=True)
                st.caption(
                    "Quality is descriptive, not a validity filter. HIGH: CV ≤8%, "
                    "zero power ≤5%, cadence >70 with ≥90% coverage, zone dominance ≥60%; "
                    "LOW: CV >15%, zero power >10%, cadence ≤60, or zone dominance <50%. "
                    "Zero power may be "
                    "coasting or a sensor dropout; isolated zero samples are flagged."
                )
            with st.expander("Diagnostics by activity"):
                st.dataframe(
                    [
                        {
                            "Date": item["date"],
                            "Activity": item["name"],
                            "Candidates": item["candidate_windows"],
                            "Valid": item["valid_windows"],
                            "Rejected": item["rejected_windows"],
                            "Active runs": item["active_runs"],
                            "Inactive seconds": item["inactive_seconds"],
                            "Uncovered seconds": item["uncovered_seconds"],
                            "Reasons": ", ".join(
                                f"{name}: {count}"
                                for name, count in item["rejected_by_reason"].items()
                                if count
                            )
                            or "None",
                        }
                        for item in data.get("diagnostics_by_activity", [])
                    ],
                    hide_index=True,
                )
            with st.expander("Recent valid-window quality details"):
                st.caption(
                    "Latest 100 valid windows; zero-power samples may be coasting or dropouts."
                )
                st.dataframe(
                    [
                        {
                            "Date/time": point["timestamp"],
                            "Quality": point["quality"],
                            "Power CV %": point["power_cv_pct"],
                            "Power CV % (nonzero)": point["power_cv_nonzero_pct"],
                            "Zero-power %": point["zero_power_pct"],
                            "Max zero run (s)": point["zero_power_max_run_s"],
                            "Cadence (rpm)": point["avg_cadence_rpm"],
                            "HR slope (bpm/min)": point["hr_slope_bpm_per_min"],
                            "HR zone dominance %": point["hr_zone_dominance_pct"],
                            "Power zone dominance %": point["power_zone_dominance_pct"],
                        }
                        for point in data.get("points", [])[-100:]
                    ],
                    hide_index=True,
                )

            matrix = _power_hr_mismatch_matrix(go, data)
            if matrix is None:
                st.info("No valid-window time available for the zone matrix.")
            else:
                st.plotly_chart(matrix, width="stretch")
            st.caption(
                "Matrix cells show valid sampled minutes (one represented minute per "
                "accepted window), avoiding overlap double-counting. Zone boundaries come "
                "from each activity's selected parameter settings."
            )
            with st.expander("Zone boundaries and analysis details"):
                st.write("Heart-rate zones", data.get("hr_zones", []))
                st.write("Power zones", data.get("power_zones", []))
                st.json(data.get("criteria", {}))
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
