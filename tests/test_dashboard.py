"""Exercise every dashboard selector without a browser or private data."""

import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from cycling.dashboard import (
    _aerobic_durability_plot_payload,
    _durability_plot_payload,
    _power_curve_figure,
    _power_curve_position,
)
from cycling.ingestion import ingest
from cycling.storage import Store
from tests.test_parsers import tcx_text


class DashboardTests(unittest.TestCase):
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

                # Overview
                app.sidebar.selectbox[0].set_value("Overview").run()
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
