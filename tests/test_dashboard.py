"""Exercise every dashboard selector without a browser or private data."""

import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from cycling.ingestion import ingest
from cycling.storage import Store
from tests.test_parsers import tcx_text


class DashboardTests(unittest.TestCase):
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
