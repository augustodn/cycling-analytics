"""Exercise every dashboard selector without a browser or private data."""

import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from cycling.dashboard import _power_curve_figure, _power_curve_position
from cycling.ingestion import ingest
from cycling.storage import Store
from tests.test_parsers import tcx_text


class DashboardTests(unittest.TestCase):
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
