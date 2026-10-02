"""Synthetic public fixtures only; no Strava account or private downloads."""

import gzip
import struct
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from fitdecode import FitCRCError
from fitdecode.utils import compute_crc

from cycling import parsers
from cycling.normalization import normalize
from cycling.parsers import parse_file
from cycling.storage import Store


def tcx_text_with_laps():
    start = datetime(2026, 1, 1, tzinfo=UTC)
    points = []
    for i in range(30):
        points.append(f"""<Trackpoint><Time>{(start + timedelta(seconds=i)).isoformat()}</Time>
          <DistanceMeters>{i * 4}</DistanceMeters><HeartRateBpm><Value>140</Value></HeartRateBpm>
          <AltitudeMeters>-12</AltitudeMeters><Cadence>90</Cadence>
          <Extensions><TPX xmlns='extensions'><Speed>4</Speed><Watts>200</Watts></TPX></Extensions></Trackpoint>""")
    return f"""<?xml version='1.0'?><TrainingCenterDatabase xmlns='tcx'>
      <Activities><Activity Sport='Biking'><Id>2026-01-01T00:00:00Z</Id>
      <Lap StartTime="2026-01-01T00:00:00Z">
        <TotalTimeSeconds>30.0</TotalTimeSeconds>
        <DistanceMeters>120.0</DistanceMeters>
        <AverageHeartRateBpm><Value>140</Value></AverageHeartRateBpm>
        <MaximumHeartRateBpm><Value>160</Value></MaximumHeartRateBpm>
        <Cadence>90</Cadence>
        <Extensions><LX xmlns='extensions'><Watts>200</Watts><MaxWatts>250</MaxWatts></LX></Extensions>
        <Track>{"".join(points)}</Track>
      </Lap>
      <Lap StartTime="2026-01-01T00:00:30Z">
        <TotalTimeSeconds>30.0</TotalTimeSeconds>
      </Lap>
      <Creator><Name>Synthetic device</Name></Creator></Activity></Activities></TrainingCenterDatabase>"""


def tcx_text(power=True, count=61):
    start = datetime(2026, 1, 1, tzinfo=UTC)
    points = []
    for i in range(count):
        watts = "<Watts>200</Watts>" if power else ""
        points.append(f"""<Trackpoint><Time>{(start + timedelta(seconds=i)).isoformat()}</Time>
          <DistanceMeters>{i * 4}</DistanceMeters><HeartRateBpm><Value>140</Value></HeartRateBpm>
          <Position><LatitudeDegrees>-34.5</LatitudeDegrees><LongitudeDegrees>-58.5</LongitudeDegrees></Position>
          <AltitudeMeters>-12</AltitudeMeters><Cadence>90</Cadence>
          <Extensions><TPX xmlns='extensions'><Speed>4</Speed>{watts}</TPX></Extensions></Trackpoint>""")
    return f"""<?xml version='1.0'?><TrainingCenterDatabase xmlns='tcx'>
      <Activities><Activity Sport='Biking'><Id>2026-01-01T00:00:00Z</Id>
      <Lap><Track>{"".join(points)}</Track></Lap>
      <Creator><Name>Synthetic device</Name></Creator></Activity></Activities></TrainingCenterDatabase>"""


def fit_bytes():
    fields = [
        (253, 4, 0x86),
        (7, 2, 0x84),
        (3, 1, 2),
        (4, 1, 2),
        (5, 4, 0x86),
        (0, 4, 0x85),
        (1, 4, 0x85),
        (2, 2, 0x84),
    ]
    definition = bytes([0x40, 0, 0]) + struct.pack("<H", 20) + bytes([len(fields)])
    definition += b"".join(bytes(f) for f in fields)
    timestamp = int(
        (
            datetime(2026, 1, 1, tzinfo=UTC) - datetime(1989, 12, 31, tzinfo=UTC)
        ).total_seconds()
    )
    records = b"".join(
        bytes([0])
        + struct.pack(
            "<IHBBIiiH",
            timestamp + i,
            200,
            140,
            90,
            400 * i,
            int(-34.5 / 180 * 2**31),
            int(-58.5 / 180 * 2**31),
            2440,
        )
        for i in range(61)
    )
    body = definition + records
    content = struct.pack("<BBHI4s", 12, 0x20, 2160, len(body), b".FIT") + body
    return content + struct.pack("<H", compute_crc(content))


class ParserTests(unittest.TestCase):
    def test_gzip_decompression_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "compressed.tcx.gz"
            path.write_bytes(gzip.compress(b"x" * 65))
            with patch.object(parsers, "MAX_SOURCE_BYTES", 64):
                with self.assertRaisesRegex(ValueError, "Decompressed source exceeds"):
                    parse_file(path)

    def test_zwift_and_mywhoosh_source_names_classify_as_indoor(self):
        with tempfile.TemporaryDirectory() as directory:
            for platform in ("Zwift", "MyWhoosh"):
                path = Path(directory) / f"ride_{platform}.tcx"
                path.write_text(tcx_text())
                metadata, rows = normalize(*parse_file(path))

                self.assertEqual(metadata["modality"], "indoor")
                self.assertNotIn("unknown_modality", metadata["quality_flags"])

    def test_tcx_sensors_coordinates_embedded_time(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "1999-01-01_wrong.tcx"
            path.write_text(tcx_text())
            metadata, raw = parse_file(path)
            metadata, rows = normalize(metadata, raw)
        self.assertEqual(metadata["start_time"], "2026-01-01T00:00:00Z")
        self.assertEqual(metadata["sport"], "cycling")
        self.assertEqual(metadata["device"], "Synthetic device")
        self.assertEqual(rows[0]["latitude"], -34.5)
        self.assertEqual(rows[0]["altitude_m"], -12)
        self.assertEqual(rows[0]["power_w"], 200)
        self.assertEqual(rows[0]["timestamp"].tzinfo, UTC)

    def test_fit_real_decoder_and_crc(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic.fit"
            path.write_bytes(fit_bytes())
            metadata, rows = normalize(*parse_file(path))
            self.assertEqual(len(rows), 61)
            self.assertAlmostEqual(rows[0]["longitude"], -58.5, places=5)
            self.assertEqual(rows[0]["power_w"], 200)
            self.assertEqual(metadata["format"], "fit")
            path.write_bytes(fit_bytes()[:-2] + b"\x00\x00")
            with self.assertRaises(FitCRCError):
                parse_file(path)

    def test_tcx_missing_power_stays_null(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ride.tcx"
            path.write_text(tcx_text(power=False))
            metadata, rows = normalize(*parse_file(path))
        self.assertTrue(all(row["power_w"] is None for row in rows))
        self.assertIn("missing_power_w", metadata["quality_flags"])

    def test_xml_entities_rejected_in_utf8_and_utf16(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.tcx"
            for encoding in ("utf-8", "utf-16"):
                path.write_bytes(
                    '<!DOCTYPE doc [<!ENTITY x "secret">]><doc/>'.encode(encoding)
                )
                with self.assertRaisesRegex(ValueError, "DTD/entity"):
                    parse_file(path)

    def test_duplicates_gaps_pause_and_timestamps(self):
        start = datetime(2026, 1, 1, tzinfo=UTC)
        metadata, rows = normalize(
            {
                "timer_events": [
                    {"timestamp": start + timedelta(seconds=2), "event": "stop"},
                    {"timestamp": start + timedelta(seconds=8), "event": "start"},
                ]
            },
            [
                {"timestamp": start, "power_w": 200},
                {"timestamp": start, "cadence_rpm": 90},
                {"timestamp": start + timedelta(seconds=10), "power_w": float("nan")},
            ],
        )
        by_time = {r["elapsed_s"]: r for r in rows}
        self.assertEqual(by_time[1]["timestamp"], start + timedelta(seconds=1))
        self.assertEqual(by_time[0]["cadence_rpm"], 90)
        self.assertFalse(by_time[2]["active"])
        self.assertNotIn(8, by_time)  # Never carry a pre-pause sensor after restart.
        self.assertIsNone(by_time[10]["power_w"])
        self.assertIn("duplicate_timestamps", metadata["quality_flags"])
        self.assertIn("invalid_power_w", metadata["quality_flags"])

    def test_hold_limit_and_open_ended_pause(self):
        start = datetime(2026, 1, 1, tzinfo=UTC)
        _, rows = normalize(
            {},
            [
                {"timestamp": start, "power_w": 0},
                {"timestamp": start + timedelta(seconds=10), "power_w": 100},
            ],
        )
        self.assertEqual([r["elapsed_s"] for r in rows], [0, 1, 2, 3, 4, 10])
        self.assertEqual(rows[0]["power_w"], 0)
        _, rows = normalize(
            {"timer_events": [{"timestamp": start, "event": "stop_all"}]},
            [{"timestamp": start, "power_w": 100}],
        )
        self.assertFalse(rows[0]["active"])

    def test_tcx_lap_metadata_parsing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "laps.tcx"
            path.write_text(tcx_text_with_laps())
            metadata, raw = parse_file(path)
            self.assertIn("laps", metadata)
            self.assertEqual(len(metadata["laps"]), 2)
            lap0 = metadata["laps"][0]
            self.assertEqual(lap0["lap_index"], 0)
            self.assertEqual(lap0["start_time"], "2026-01-01T00:00:00Z")
            self.assertEqual(lap0["end_time"], "2026-01-01T00:00:30Z")
            self.assertEqual(lap0["duration_s"], 30.0)
            self.assertEqual(lap0["distance_m"], 120.0)
            self.assertEqual(lap0["avg_hr_bpm"], 140)
            self.assertEqual(lap0["max_hr_bpm"], 160)
            self.assertEqual(lap0["avg_cadence_rpm"], 90)
            self.assertEqual(lap0["avg_power_w"], 200)
            self.assertEqual(lap0["max_power_w"], 250)

            lap1 = metadata["laps"][1]
            self.assertEqual(lap1["lap_index"], 1)
            self.assertEqual(lap1["duration_s"], 30.0)
            self.assertIsNone(lap1["avg_power_w"])
            self.assertIsNone(lap1["avg_hr_bpm"])

    def test_activity_laps_duckdb_storage(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(root=Path(directory) / ".cycling")
            path = Path(directory) / "laps.tcx"
            path.write_text(tcx_text_with_laps())
            metadata, raw = normalize(*parse_file(path))
            store.write_activity("act123", metadata, raw)

            laps = store.db.execute(
                "SELECT activity_id, lap_index, duration_s, avg_power_w FROM activity_laps WHERE activity_id='act123' ORDER BY lap_index"
            ).fetchall()
            self.assertEqual(len(laps), 2)
            self.assertEqual(laps[0], ("act123", 0, 30.0, 200.0))
            self.assertEqual(laps[1], ("act123", 1, 30.0, None))
            store.close()


if __name__ == "__main__":
    unittest.main()
