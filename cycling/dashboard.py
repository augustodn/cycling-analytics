"""Streamlit adapter: visualization only, all metrics come from the service."""

import argparse
from datetime import UTC, datetime, timedelta

from cycling.models import (
    ActivityRequest,
    CurveRequest,
    DurabilityRequest,
    LoadRequest,
    PeriodPowerCurveRequest,
    StreamRequest,
)
from cycling.service import CyclingService
from cycling.storage import Store

LOAD_CURVE_EXPLANATION = (
    "CTL (fitness) is a 42-day exponentially weighted average of daily load; "
    "ATL (fatigue) is the same calculation over 7 days; TSB (form) is yesterday's "
    "CTL minus ATL. Each day's load uses power first (when coverage is sufficient), "
    "then HR, then session RPE. Global load combines all modalities before these "
    "daily calculations."
)


def run():
    import plotly.graph_objects as go
    import streamlit as st

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
            ["Overview", "Activity", "Power curve", "Durability", "Load", "Calendar"],
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
                period = st.selectbox("Period", ["30d", "90d", "365d", "all"])
                modality = st.selectbox(
                    "Modality",
                    ["all"]
                    + sorted({a.get("modality", "unknown") for a in activities}),
                )
                try:
                    result = service.period_power_curve(
                        PeriodPowerCurveRequest(period=period, modality=modality)
                    )
                    watts = result.data.get("watts", {})
                    durations = [int(d) for d in watts.keys() if watts[d] is not None]
                    values = [watts[str(d)] for d in durations]
                    if values:
                        chart = go.Figure(
                            go.Scatter(
                                x=durations,
                                y=values,
                                mode="lines+markers",
                            )
                        )
                        chart.update_layout(
                            xaxis_title="Duration (seconds)",
                            xaxis_type="log",
                            yaxis_title="Best observed power (W)",
                        )
                        st.plotly_chart(chart, width="stretch")
                    else:
                        st.warning(
                            "No power data found for the selected period/modality."
                        )
                    st.json(result.model_dump(mode="json"))
                except Exception as exc:
                    st.error(f"Error computing period power curve: {exc}")
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
                durations = [int(d) for d in curve.keys() if curve[d] is not None]
                values = [curve[str(d)] for d in durations]
                if values:
                    chart = go.Figure(
                        go.Scatter(
                            x=durations,
                            y=values,
                            mode="lines+markers",
                        )
                    )
                    chart.update_layout(
                        xaxis_title="Duration (seconds)",
                        xaxis_type="log",
                        yaxis_title="Best observed power (W)",
                    )
                    st.plotly_chart(chart, width="stretch")
                else:
                    st.warning("No power data available for this activity.")
                st.json(result.model_dump(mode="json"))
            except Exception as exc:
                st.error(f"Power curve unavailable: {exc}")

        elif page == "Durability":
            try:
                result = service.durability(DurabilityRequest(activity_id=ident))
                st.warning(
                    "Observed efforts by prior work; not a controlled fatigue test. Missing-power MTB has no power durability."
                )
                if not result.data.get("available", True):
                    st.warning(
                        f"Durability unavailable: {result.data.get('reason', 'Missing power data')}"
                    )
                st.json(result.model_dump(mode="json"))
            except Exception as exc:
                st.error(f"Durability unavailable: {exc}")


if __name__ == "__main__":
    try:
        run()
    except (ValueError, KeyError, OSError) as exc:
        import streamlit as st

        st.error(str(exc))
