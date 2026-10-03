"""Exercise every dashboard selector without a browser or private data."""

import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from streamlit.testing.v1 import AppTest

from cycling.dashboard import (
    POWER_SKILL_DURATIONS,
    POWER_SKILL_GROUPS,
    POWER_SKILL_INTERVALS,
    POWER_SKILL_LEVELS,
    _aerobic_durability_plot_payload,
    _durability_plot_payload,
    _estimate_power_skill_percentile,
    _power_curve_figure,
    _power_curve_position,
    _power_hr_curve_figure,
    _power_hr_delta_rows,
    _power_hr_mismatch_figure,
    _power_hr_mismatch_matrix,
    _power_hr_trend_figure,
    _power_skill_assessments,
    _power_skill_benchmark_table,
    _power_skill_progress_figure,
    _power_skills_figure,
    _render_hr_distribution,
    _weekly_training_figure,
)
from cycling.ingestion import ingest
from cycling.storage import Store
from tests.test_parsers import tcx_text


class DashboardTests(unittest.TestCase):
    def test_power_hr_curve_marks_hr_zones_and_delta_requires_confidence(self):
        import plotly.graph_objects as go

        group = {
            "bins": [
                {
                    "power_low_w": 200,
                    "power_high_w": 210,
                    "power_w": 205,
                    "hr_median_bpm": 135,
                    "hr_p25_bpm": 130,
                    "hr_p75_bpm": 140,
                    "activity_count": 3,
                    "valid_seconds": 600,
                    "status": "ok",
                }
            ]
        }
        chart = _power_hr_curve_figure(
            go, [{"name": "Outdoor", **group}], [127, 141, 147, 158]
        )
        self.assertEqual(chart.layout.xaxis.title.text, "Power (W)")
        self.assertEqual(chart.layout.yaxis.title.text, "Heart rate (bpm)")
        self.assertEqual(list(chart.data[0].x), [205])
        self.assertEqual(list(chart.data[0].y), [135])
        self.assertEqual(
            [shape.line.color for shape in chart.layout.shapes],
            ["#87CEEB", "#228B22", "#FFD700", "#FF69B4"],
        )
        earlier = {
            "bins": [
                {**group["bins"][0], "hr_median_bpm": 140},
                {**group["bins"][0], "power_low_w": 210, "status": "low_confidence"},
            ]
        }
        delta = _power_hr_delta_rows(earlier, group)
        self.assertEqual(delta[0]["Δ HR (bpm)"], -5)
        self.assertEqual(len(delta), 1)

    def test_fixed_power_trend_shows_activity_points_and_rolling_median(self):
        import plotly.graph_objects as go

        chart = _power_hr_trend_figure(
            go,
            [
                {
                    "name": "Current",
                    "points": [
                        {
                            "date": "2026-01-01",
                            "activity_id": "ride-1",
                            "target_power_w": 200,
                            "hr_median_bpm": 136,
                            "rolling_median_hr_bpm": 136,
                            "valid_seconds": 120,
                        },
                        {
                            "date": "2026-01-02",
                            "activity_id": "ride-2",
                            "target_power_w": 200,
                            "hr_median_bpm": None,
                            "rolling_median_hr_bpm": 136,
                            "valid_seconds": 0,
                        },
                    ],
                }
            ],
            [200],
        )

        self.assertEqual(len(chart.data), 2)
        self.assertEqual(chart.data[0].mode, "markers")
        self.assertEqual(chart.data[0].y, (136,))
        self.assertEqual(chart.data[1].mode, "lines+markers")
        self.assertEqual(chart.data[1].y, (136, 136))

    def test_zone_mismatch_charts_show_rolling_series_and_heatmap_minutes(self):
        import plotly.graph_objects as go

        data = {
            "points": [
                {
                    "timestamp": "2026-01-01T00:10:00+00:00",
                    "mismatch": 1,
                    "mismatch_30d": 0.5,
                    "representative_power_w": 200,
                    "representative_hr_bpm": 130,
                    "power_zone": 3,
                    "hr_zone": 2,
                }
            ],
            "matrix_seconds": [[0, 0, 0, 0, 0, 0], [0, 0, 60, 0, 0, 0]],
            "power_zones": [{"label": f"Power Z{index}"} for index in range(1, 7)],
            "hr_zones": [{"label": f"HR Z{index}"} for index in range(1, 6)],
        }

        mismatch = _power_hr_mismatch_figure(go, data)
        matrix = _power_hr_mismatch_matrix(go, data)

        self.assertEqual(mismatch.data[0].name, "Valid windows")
        self.assertEqual(mismatch.data[1].name, "30-day rolling mean")
        self.assertEqual(matrix.data[0].z[1][2], 1)

    def test_aerobic_durability_payload_contains_retention_and_efficiency(self):
        payload = _aerobic_durability_plot_payload(
            {
                "thresholds_kj": [1000, 1500],
                "baseline": {"efficiency_factor": 1.6},
                "points": [
                    {
                        "threshold_kj": 1000,
                        "retention_pct": 98.0,
                        "efficiency_factor": 1.568,
                    },
                    {
                        "threshold_kj": 1500,
                        "retention_pct": None,
                        "efficiency_factor": None,
                    },
                ],
            }
        )
        self.assertEqual(payload["labels"][0], "Fresh")
        self.assertEqual(payload["retention"], [100.0, 98.0, None])
        self.assertEqual(payload["efficiency"], [1.6, 1.568, None])

    def test_durability_payload_keeps_fresh_and_kj_labels(self):
        payload = _durability_plot_payload(
            {
                "thresholds_kj": [1000, 1500, 1800, 2100, 2200],
                "durations_s": [300],
                "points": [
                    {
                        "threshold_kj": 1500,
                        "duration_s": 300,
                        "retention_pct": 90.0,
                    }
                ],
            }
        )
        self.assertEqual(payload["x_labels"][0], "Fresh")
        self.assertIn("1000 kJ", payload["x_labels"][1])
        self.assertEqual(payload["heatmap_text"][0][0], "100%")
        self.assertEqual(payload["heatmap_text"][0][1], "N/A")
        self.assertEqual(payload["heatmap_text"][0][2], "90.0%")

    def test_power_curve_uses_strava_style_pseudo_log_axis(self):
        import plotly.graph_objects as go

        chart = _power_curve_figure(
            go,
            {"5": 250, "30": 220, "60": 210, "300": 190, "1200": 170},
        )

        self.assertIsNotNone(chart)
        self.assertEqual(chart.layout.xaxis.type, "linear")
        self.assertEqual(chart.layout.yaxis.rangemode, "tozero")
        self.assertEqual(
            list(chart.layout.xaxis.ticktext),
            ["1s", "15s", "1m", "5m", "10m", "20m"],
        )
        self.assertEqual(
            list(chart.data[0].x),
            [_power_curve_position(duration) for duration in (5, 30, 60, 300, 1200)],
        )
        self.assertEqual(chart.data[0].customdata[0][0], "5s")

        extended_chart = _power_curve_figure(
            go,
            {"5": 250, "1200": 170, "21600": None},
            [5, 1200, 21600],
        )
        self.assertEqual(
            extended_chart.layout.xaxis.range,
            (1, _power_curve_position(21600)),
        )
        self.assertEqual(extended_chart.layout.xaxis.ticktext[-1], "6h")

    def test_power_skills_compares_all_time_and_selected_scope(self):
        import plotly.graph_objects as go

        historical = {
            str(duration): 600 - index * 20
            for index, duration in enumerate(POWER_SKILL_DURATIONS)
        }
        selected = {
            str(duration): 500 - index * 15
            for index, duration in enumerate(POWER_SKILL_DURATIONS)
        }
        selected.pop(str(180))

        chart = _power_skills_figure(go, historical, selected, "Selected period")

        labels = [label for _, label, _, _ in POWER_SKILL_INTERVALS]
        colors = [color for _, _, color in POWER_SKILL_GROUPS]
        interval_angles = [index * 30 for index in range(len(labels))]
        self.assertEqual(len(POWER_SKILL_DURATIONS), 12)
        self.assertEqual(
            [trace.name for trace in chart.data[:3]],
            ["Sprinting (15s–1m)", "Attacking (2m–10m)", "Climbing (15m–60m)"],
        )
        self.assertEqual([trace.theta[0] for trace in chart.data[:3]], [30, 135, 270])
        self.assertEqual([trace.width[0] for trace in chart.data[:3]], [86, 116, 146])
        self.assertEqual([trace.marker.color[0] for trace in chart.data[:3]], colors)
        self.assertTrue(all(trace.marker.line.width == 0 for trace in chart.data[:3]))
        self.assertEqual(chart.data[3].name, "All-time maximum")
        self.assertEqual(list(chart.data[3].theta), interval_angles)
        self.assertEqual(chart.data[3].customdata[labels.index("3m")], "3m")
        self.assertEqual(chart.data[4].name, "Selected period")
        self.assertIsNone(list(chart.data[4].r)[labels.index("3m")])
        self.assertIn("0.25", chart.data[4].fillcolor)
        self.assertEqual(chart.data[5].text[labels.index("3m")], "520 W")
        self.assertEqual(chart.layout.polar.angularaxis.type, "linear")
        self.assertEqual(list(chart.layout.polar.angularaxis.tickvals), interval_angles)
        self.assertEqual(list(chart.layout.polar.angularaxis.ticktext), labels)
        self.assertEqual(chart.layout.legend.orientation, "v")

    def test_power_skill_percentile_interpolates_reference_cutoffs(self):
        self.assertEqual(_estimate_power_skill_percentile(0, 15), 0)
        self.assertEqual(_estimate_power_skill_percentile(165, 15), 1)
        self.assertEqual(_estimate_power_skill_percentile(252.5, 15), 16)
        self.assertEqual(_estimate_power_skill_percentile(340, 15), 31)
        self.assertEqual(_estimate_power_skill_percentile(1000, 15), 98)

    def test_power_skill_assessments_average_available_interval_percentiles(self):
        historical = {
            "15": 340,
            "30": 295,
            "60": 260,
            "120": 265,
            "180": 245,
            "300": 225,
            "600": 205,
            "900": 275,
            "1200": 270,
            "1800": 265,
            "2700": 265,
            "3600": 260,
        }
        historical.pop("60")

        assessments = _power_skill_assessments(historical)

        self.assertEqual(assessments[0]["percentile"], 31)
        self.assertEqual(assessments[0]["level"], "Intermediate")
        self.assertEqual(assessments[0]["level_number"], 2)
        self.assertEqual(assessments[0]["intervals_available"], 2)
        self.assertAlmostEqual(assessments[0]["progress"], (30 / 97) * 100)
        self.assertEqual(assessments[1]["percentile"], 46)
        self.assertEqual(assessments[1]["level"], "Athletic")
        self.assertEqual(assessments[2]["percentile"], 85)
        self.assertEqual(assessments[2]["level"], "Semi-Pro")

    def test_power_skill_level_figure_and_reference_tables_show_current_values(self):
        import plotly.graph_objects as go

        assessments = _power_skill_assessments({"15": 340, "30": 295, "60": 260})
        progress = _power_skill_progress_figure(go, assessments)
        sprinting = _power_skill_benchmark_table(
            "Sprinting", {"15": 400, "30": 300, "60": 250}
        )
        attacking = _power_skill_benchmark_table("Attacking", {"120": 443})
        climbing = _power_skill_benchmark_table("Climbing", {"3600": 355})

        self.assertEqual(list(progress.data[0].x), [100, 100, 100])
        self.assertEqual(list(progress.data[1].x), [30 / 97 * 100, 0, 0])
        self.assertEqual(progress.layout.barmode, "overlay")
        self.assertEqual(len(progress.layout.shapes), len(POWER_SKILL_LEVELS) - 1)
        self.assertEqual(len(sprinting), 3)
        self.assertEqual(sprinting[0]["Interval"], "15s")
        self.assertEqual(sprinting[0]["Aspiring P1"], "165 W")
        self.assertEqual(sprinting[0]["Intermediate P31"], "340 W")
        self.assertEqual(sprinting[0]["World Class P98"], "930 W")
        self.assertEqual(sprinting[0]["Current best (W)"], "400 W")
        self.assertEqual(sprinting[-1]["Current best (W)"], "250 W")
        self.assertEqual(len(attacking), 4)
        self.assertEqual(attacking[0]["Interval"], "2m")
        self.assertEqual(attacking[0]["Current best (W)"], "443 W")
        self.assertEqual(attacking[1]["Current best (W)"], "—")
        self.assertEqual(len(climbing), 5)
        self.assertEqual(climbing[-1]["Interval"], "60m")
        self.assertEqual(climbing[-1]["Current best (W)"], "355 W")

    def test_weekly_training_figure_uses_requested_hr_zone_colors(self):
        import plotly.graph_objects as go

        zones = [
            {"label": label}
            for label in (
                "1 Recovery",
                "2 Aerobic",
                "3 Tempo",
                "4 SubThreshold",
                "5a Threshold",
                "5b Aerobic Capacity",
                "5c Anaerobic",
            )
        ]
        data = {
            "weeks": [
                {
                    "iso_week": "2026-W01",
                    "total_seconds": 3600,
                    "hr_zone_seconds": [1] * 7,
                    "unclassified_seconds": 3593,
                }
            ],
            "zones": zones,
        }

        figure = _weekly_training_figure(go, data)

        colors = {trace.name: trace.marker.color for trace in figure.data}
        self.assertEqual(colors["1 Recovery"], "#808080")
        self.assertEqual(colors["2 Aerobic"], "#87CEEB")
        self.assertEqual(colors["3 Tempo"], "#228B22")
        self.assertEqual(colors["4 SubThreshold"], "#FFD700")
        self.assertEqual(colors["5a Threshold"], "#FF69B4")
        self.assertEqual(colors["5b Aerobic Capacity"], "#FF0000")
        self.assertEqual(colors["5c Anaerobic"], "#8A2BE2")
        self.assertEqual(colors["Unclassified / no HR"], "#D3D3D3")

    def test_hr_distribution_uses_weekly_training_zone_colors(self):
        import plotly.graph_objects as go

        st = Mock()
        st.columns.return_value = [Mock(), Mock()]
        zones = [
            {
                "label": label,
                "percentage_range": "",
                "hr_range": "",
            }
            for label in (
                "1 Recovery",
                "2 Aerobic",
                "3 Tempo",
                "4 SubThreshold",
                "5a Threshold",
                "5b Aerobic Capacity",
                "5c Anaerobic",
            )
        ]

        _render_hr_distribution(
            st,
            go,
            {
                "seconds": [1] * len(zones),
                "percentages": [100 / len(zones)] * len(zones),
                "zones": zones,
            },
            "No HR data",
            show_table=False,
        )

        chart = st.plotly_chart.call_args.args[0]
        self.assertEqual(
            list(chart.data[0].marker.color),
            [
                "#808080",
                "#87CEEB",
                "#228B22",
                "#FFD700",
                "#FF69B4",
                "#FF0000",
                "#8A2BE2",
            ],
        )

    def test_all_views_and_empty_catalog(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = root / "data"
            script = Path(__file__).parents[1] / "cycling" / "dashboard.py"
            with patch.object(sys, "argv", [str(script), "--data-dir", str(data)]):
                app = AppTest.from_file(str(script), default_timeout=20).run()
                self.assertEqual(len(app.exception), 0)
                self.assertIn("No activities", app.info[0].value)

                source = root / "synthetic.tcx"
                source.write_text(tcx_text())
                with Store(data) as store:
                    ingest(store, source)

                app.run()
                self.assertEqual(len(app.exception), 0)

                # FTP calibration handles activities with no valid windows.
                app.sidebar.selectbox[0].set_value("FTP Calibration").run()
                self.assertEqual(len(app.exception), 0)

                # Power–HR runs through the same service from the local dashboard.
                app.sidebar.selectbox[0].set_value("Power ↔ Heart Rate").run()
                self.assertEqual(len(app.exception), 0)
                app.selectbox[0].set_value("Single activity").run()
                app.button[0].click().run()
                self.assertEqual(len(app.exception), 0)
                app.selectbox[0].set_value("Period").run()
                app.button[0].click().run()
                self.assertEqual(len(app.exception), 0)

                # Overview
                app.sidebar.selectbox[0].set_value("Overview").run()
                self.assertEqual(len(app.exception), 0)

                # Progress
                app.sidebar.selectbox[0].set_value("Progress").run()
                self.assertEqual(len(app.exception), 0)
                if len(app.selectbox) > 0:
                    app.selectbox[0].set_value("90d").run()
                    self.assertEqual(len(app.exception), 0)
                    app.selectbox[0].set_value("Custom range").run()
                    self.assertEqual(len(app.exception), 0)
                if len(app.selectbox) > 1:
                    app.selectbox[1].set_value("all").run()
                    self.assertEqual(len(app.exception), 0)
                if len(app.checkbox) > 0:
                    app.checkbox[0].set_value(True).run()
                    self.assertEqual(len(app.exception), 0)

                # Calendar
                app.sidebar.selectbox[0].set_value("Calendar").run()
                self.assertEqual(len(app.exception), 0)
                if len(app.selectbox) > 0:
                    app.selectbox[0].set_value("all").run()
                    self.assertEqual(len(app.exception), 0)

                # Activity
                app.sidebar.selectbox[0].set_value("Activity").run()
                self.assertEqual(len(app.exception), 0)
                if len(app.selectbox) > 0:
                    app.selectbox[0].set_value(app.selectbox[0].options[0]).run()
                    self.assertEqual(len(app.exception), 0)

                # Power curve - Single Activity & Period
                app.sidebar.selectbox[0].set_value("Power curve").run()
                self.assertEqual(len(app.exception), 0)
                if len(app.selectbox) > 0:
                    app.selectbox[0].set_value("Single Activity").run()
                    self.assertEqual(len(app.exception), 0)
                    app.selectbox[0].set_value("Period").run()
                    self.assertEqual(len(app.exception), 0)
                    if len(app.selectbox) > 1:
                        app.selectbox[1].set_value("90d").run()
                        self.assertEqual(len(app.exception), 0)
                        app.selectbox[1].set_value("Custom range").run()
                        self.assertEqual(len(app.exception), 0)
                    if len(app.selectbox) > 2:
                        app.selectbox[2].set_value("all").run()
                        self.assertEqual(len(app.exception), 0)

                # Heart rate distribution - Single Activity & Period
                app.sidebar.selectbox[0].set_value("Heart rate distribution").run()
                self.assertEqual(len(app.exception), 0)
                if len(app.selectbox) > 0:
                    app.selectbox[0].set_value("Single Activity").run()
                    self.assertEqual(len(app.exception), 0)
                    app.selectbox[0].set_value("Period").run()
                    self.assertEqual(len(app.exception), 0)
                    if len(app.selectbox) > 1:
                        app.selectbox[1].set_value("90d").run()
                        self.assertEqual(len(app.exception), 0)
                        app.selectbox[1].set_value("Custom range").run()
                        self.assertEqual(len(app.exception), 0)
                    if len(app.selectbox) > 2:
                        app.selectbox[2].set_value("all").run()
                        self.assertEqual(len(app.exception), 0)

                # Durability
                app.sidebar.selectbox[0].set_value("Durability").run()
                self.assertEqual(len(app.exception), 0)
                if len(app.segmented_control) > 0:
                    app.segmented_control[0].set_value("Aerobic durability").run()
                    self.assertEqual(len(app.exception), 0)

                # Load
                app.sidebar.selectbox[0].set_value("Load").run()
                self.assertEqual(len(app.exception), 0)
                if len(app.selectbox) > 0:
                    app.selectbox[0].set_value(app.selectbox[0].options[0]).run()
                    self.assertEqual(len(app.exception), 0)
                if len(app.date_input) >= 2:
                    today = datetime.now(UTC).date()
                    app.date_input[0].set_value(today - timedelta(days=30)).run()
                    self.assertEqual(len(app.exception), 0)
                    app.date_input[1].set_value(today).run()
                    self.assertEqual(len(app.exception), 0)
                if len(app.selectbox) > 1:
                    app.selectbox[1].set_value("load").run()
                    self.assertEqual(len(app.exception), 0)

                # Mode selector switch
                app.sidebar.selectbox[1].set_value("current").run()
                self.assertEqual(len(app.exception), 0)


if __name__ == "__main__":
    unittest.main()
