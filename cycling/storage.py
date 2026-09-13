"""Single-process local catalog. Source files are never written here."""

import json
import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from cycling.config import NORMALIZER_VERSION
from cycling.models import ActivityContext, AthleteParameters


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def encode(value) -> str:
    return json.dumps(value, default=str, allow_nan=False)


def _to_date(val: date | datetime | str | None) -> date | None:
    if val is None:
        return None
    if isinstance(val, date) and not isinstance(val, datetime):
        return val
    if isinstance(val, datetime):
        return val.date()
    return datetime.fromisoformat(str(val)).date()


class Store:
    def __init__(self, root: str | Path = ".cycling"):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "samples").mkdir(exist_ok=True)
        self.db = duckdb.connect(str(self.root / "catalog.duckdb"))
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS sources (
                path VARCHAR PRIMARY KEY, hash VARCHAR, size BIGINT,
                status VARCHAR, error VARCHAR, last_seen VARCHAR);
            CREATE TABLE IF NOT EXISTS activities (
                id VARCHAR PRIMARY KEY, source_hash VARCHAR, strava_id_hint VARCHAR,
                start_time VARCHAR, duration_s BIGINT, modality VARCHAR,
                metadata VARCHAR, sample_path VARCHAR,
                normalizer_version VARCHAR, ingested_at VARCHAR);
            CREATE TABLE IF NOT EXISTS parameters (
                id VARCHAR PRIMARY KEY, effective_date DATE, recorded_at VARCHAR,
                settings VARCHAR);
            CREATE TABLE IF NOT EXISTS metrics (
                id VARCHAR PRIMARY KEY, activity_id VARCHAR, cache_key VARCHAR,
                computed_at VARCHAR, result VARCHAR);
            CREATE TABLE IF NOT EXISTS intervals (
                id VARCHAR PRIMARY KEY, activity_id VARCHAR, interval_type VARCHAR,
                start_s INT, end_s INT, metrics VARCHAR);
            CREATE TABLE IF NOT EXISTS power_curve (
                activity_id VARCHAR, duration_s INT, max_power_w DOUBLE,
                PRIMARY KEY (activity_id, duration_s));
            CREATE TABLE IF NOT EXISTS daily_training_load (
                date DATE PRIMARY KEY, ctl DOUBLE, atl DOUBLE, tsb DOUBLE,
                total_load DOUBLE);
            CREATE TABLE IF NOT EXISTS processing_runs (
                id VARCHAR PRIMARY KEY, run_at VARCHAR, status VARCHAR, summary VARCHAR);
            CREATE TABLE IF NOT EXISTS quality_flags (
                activity_id VARCHAR, flag VARCHAR, PRIMARY KEY (activity_id, flag));
            CREATE TABLE IF NOT EXISTS activity_context (
                id VARCHAR PRIMARY KEY, activity_id VARCHAR,
                recorded_at VARCHAR, context VARCHAR);
            CREATE TABLE IF NOT EXISTS acquisition_audits (
                root VARCHAR PRIMARY KEY, audited_at VARCHAR, summary VARCHAR);
            CREATE TABLE IF NOT EXISTS activity_laps (
                activity_id VARCHAR, lap_index INT, start_time VARCHAR,
                end_time VARCHAR, duration_s DOUBLE, distance_m DOUBLE,
                avg_power_w DOUBLE, max_power_w DOUBLE, avg_hr_bpm DOUBLE,
                max_hr_bpm DOUBLE, avg_cadence_rpm DOUBLE, max_cadence_rpm DOUBLE,
                PRIMARY KEY (activity_id, lap_index));
        """)

        # Migration: ensure missing columns exist for legacy activities table
        cols = {
            r[1] for r in self.db.execute("PRAGMA table_info('activities')").fetchall()
        }
        for col_name, col_type in [
            ("source_hash", "VARCHAR"),
            ("strava_id_hint", "VARCHAR"),
            ("start_time", "VARCHAR"),
            ("duration_s", "BIGINT"),
            ("modality", "VARCHAR"),
        ]:
            if col_name not in cols:
                self.db.execute(
                    f"ALTER TABLE activities ADD COLUMN {col_name} {col_type}"
                )

        # Backfill legacy activity rows if metadata JSON exists
        rows = self.db.execute(
            "SELECT id, metadata FROM activities WHERE start_time IS NULL OR duration_s IS NULL OR modality IS NULL OR source_hash IS NULL"
        ).fetchall()
        for act_id, meta_str in rows:
            if not meta_str:
                continue
            try:
                meta = json.loads(meta_str)
            except Exception:
                continue
            s_hash = meta.get("source_hash") or act_id
            s_hint = meta.get("strava_id_hint")
            s_time = meta.get("start_time")
            dur = (
                meta.get("elapsed_seconds")
                if meta.get("elapsed_seconds") is not None
                else meta.get("duration_s")
            )
            mod = meta.get("modality", "unknown")
            self.db.execute(
                "UPDATE activities SET source_hash = COALESCE(source_hash, ?), strava_id_hint = COALESCE(strava_id_hint, ?), start_time = COALESCE(start_time, ?), duration_s = COALESCE(duration_s, ?), modality = COALESCE(modality, ?) WHERE id = ?",
                [s_hash, s_hint, s_time, dur, mod, act_id],
            )
        if not self.db.execute("SELECT count(*) FROM parameters").fetchone()[0]:
            self.add_parameters(AthleteParameters())

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def add_parameters(self, parameters: AthleteParameters) -> str:
        ident = uuid4().hex
        self.db.execute(
            "INSERT INTO parameters VALUES (?, ?, ?, ?)",
            [ident, parameters.effective_date, now(), parameters.model_dump_json()],
        )
        return ident

    def parameters(
        self,
        when: date | datetime | str | None = None,
        mode: Literal["historical", "current"] = "historical",
    ):
        dt = _to_date(when)
        if mode == "current" or dt is None:
            row = self.db.execute(
                "SELECT id, settings FROM parameters ORDER BY effective_date DESC, recorded_at DESC, id DESC LIMIT 1"
            ).fetchone()
        else:
            row = self.db.execute(
                "SELECT id, settings FROM parameters WHERE effective_date <= ? "
                "ORDER BY effective_date DESC, recorded_at DESC, id DESC LIMIT 1",
                [dt],
            ).fetchone()
            if not row:
                row = self.db.execute(
                    "SELECT id, settings FROM parameters ORDER BY effective_date ASC, recorded_at ASC, id ASC LIMIT 1"
                ).fetchone()
        return (
            (row[0], AthleteParameters.model_validate_json(row[1]))
            if row
            else (None, None)
        )

    def parameter_history(self):
        return [
            {
                "id": r[0],
                "effective_date": str(r[1]),
                "recorded_at": r[2],
                "settings": json.loads(r[3]),
            }
            for r in self.db.execute(
                "SELECT * FROM parameters ORDER BY effective_date, recorded_at"
            ).fetchall()
        ]

    def source(
        self, path: Path, digest: str | None, size: int | None, status: str, error=None
    ):
        self.db.execute(
            "INSERT OR REPLACE INTO sources VALUES (?, ?, ?, ?, ?, ?)",
            [str(path), digest, size, status, error, now()],
        )

    def activity(self, ident: str):
        row = self.db.execute(
            "SELECT id, source_hash, strava_id_hint, start_time, duration_s, modality, metadata, sample_path, normalizer_version, ingested_at FROM activities WHERE id = ?",
            [ident],
        ).fetchone()
        if not row:
            raise KeyError(f"Activity not found: {ident}")
        try:
            meta = json.loads(row[6]) if row[6] else {}
        except Exception:
            meta = {}
        context = self.db.execute(
            "SELECT id, context FROM activity_context WHERE activity_id=? "
            "ORDER BY recorded_at DESC, id DESC LIMIT 1",
            [ident],
        ).fetchone()
        flags = [
            r[0]
            for r in self.db.execute(
                "SELECT flag FROM quality_flags WHERE activity_id=?", [ident]
            ).fetchall()
        ]
        start_time = row[3] or meta.get("start_time", "")
        duration_s = row[4] if row[4] is not None else meta.get("elapsed_seconds", 0)
        modality = row[5] or meta.get("modality", "unknown")
        source_hash = row[1] or meta.get("source_hash", row[0])
        strava_id_hint = row[2] or meta.get("strava_id_hint")
        context_data = {}
        if context and context[1]:
            try:
                context_data = json.loads(context[1])
            except Exception:
                context_data = {}

        return {
            "id": row[0],
            "source_hash": source_hash,
            "strava_id_hint": strava_id_hint,
            "start_time": start_time,
            "duration_s": duration_s,
            "modality": modality,
            **meta,
            "quality_flags": flags or meta.get("quality_flags", []),
            **context_data,
            "context_id": context[0] if context else None,
            "sample_path": row[7],
            "normalizer_version": row[8],
            "ingested_at": row[9],
        }

    def set_context(self, ident: str, context: ActivityContext):
        activity = self.activity(ident)
        values = {k: activity[k] for k in ("rpe", "modality") if k in activity}
        values.update(context.model_dump(exclude_none=True))
        context_id = uuid4().hex
        self.db.execute(
            "INSERT INTO activity_context VALUES (?, ?, ?, ?)",
            [context_id, ident, now(), encode(values)],
        )
        return context_id

    def activities(self):
        return sorted(
            (
                self.activity(r[0])
                for r in self.db.execute("SELECT id FROM activities").fetchall()
            ),
            key=lambda a: a.get("start_time") or "",
            reverse=True,
        )

    def write_activity(self, ident: str, metadata: dict, rows: list[dict]):
        # Store per-activity normalized samples to .cycling/samples/YYYY/MM/<stable-id>.parquet atomically
        start_time_iso = metadata.get("start_time", now())
        dt = datetime.fromisoformat(start_time_iso.replace("Z", "+00:00"))
        year_str = f"{dt.year:04d}"
        month_str = f"{dt.month:02d}"

        relative = Path("samples") / year_str / month_str / f"{ident}.parquet"
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".parquet.tmp")

        fields = [
            pa.field("timestamp", pa.timestamp("us", tz="UTC")),
            pa.field("elapsed_s", pa.int64()),
            pa.field("segment", pa.int64()),
            pa.field("active", pa.bool_()),
        ]
        fields += [
            pa.field(name, pa.float64())
            for name in (
                "power_w",
                "hr_bpm",
                "cadence_rpm",
                "speed_mps",
                "distance_m",
                "altitude_m",
                "latitude",
                "longitude",
            )
        ]
        try:
            pq.write_table(
                pa.Table.from_pylist(rows, schema=pa.schema(fields)),
                temporary,
                compression="zstd",
            )
            with temporary.open("rb") as handle:
                os.fsync(handle.fileno())
            os.replace(temporary, target)
            directory_fd = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)

            source_hash = metadata.get("source_hash", ident)
            strava_id_hint = metadata.get("strava_id_hint")
            start_time = metadata.get("start_time", start_time_iso)
            duration_s = metadata.get("elapsed_seconds", len(rows))
            modality = metadata.get("modality", "unknown")

            self.db.execute(
                "INSERT OR REPLACE INTO activities (id, source_hash, strava_id_hint, start_time, duration_s, modality, metadata, sample_path, normalizer_version, ingested_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    ident,
                    source_hash,
                    strava_id_hint,
                    start_time,
                    duration_s,
                    modality,
                    encode(metadata),
                    str(relative),
                    NORMALIZER_VERSION,
                    now(),
                ],
            )
            self.db.execute("DELETE FROM quality_flags WHERE activity_id=?", [ident])
            for flag in metadata.get("quality_flags", []):
                self.db.execute(
                    "INSERT OR REPLACE INTO quality_flags VALUES (?, ?)",
                    [ident, flag],
                )
            self.db.execute("DELETE FROM activity_laps WHERE activity_id=?", [ident])
            if "laps" in metadata and isinstance(metadata["laps"], list):
                for lap in metadata["laps"]:
                    self.db.execute(
                        "INSERT OR REPLACE INTO activity_laps VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        [
                            ident,
                            lap.get("lap_index"),
                            lap.get("start_time"),
                            lap.get("end_time"),
                            lap.get("duration_s"),
                            lap.get("distance_m"),
                            lap.get("avg_power_w"),
                            lap.get("max_power_w"),
                            lap.get("avg_hr_bpm"),
                            lap.get("max_hr_bpm"),
                            lap.get("avg_cadence_rpm"),
                            lap.get("max_cadence_rpm"),
                        ],
                    )
        finally:
            temporary.unlink(missing_ok=True)

    def samples(self, ident: str):
        act = self.activity(ident)
        if not act.get("sample_path"):
            return []
        sample_file = self.root / act["sample_path"]
        if not sample_file.is_file():
            return []
        return pq.read_table(sample_file).to_pylist()

    def cached(self, ident: str, key: str):
        row = self.db.execute(
            "SELECT result FROM metrics WHERE activity_id=? AND cache_key=? "
            "ORDER BY computed_at DESC LIMIT 1",
            [ident, key],
        ).fetchone()
        return json.loads(row[0]) if row else None

    def save_metrics(self, ident: str, key: str, result: dict):
        self.db.execute(
            "INSERT INTO metrics VALUES (?, ?, ?, ?, ?)",
            [uuid4().hex, ident, key, now(), encode(result)],
        )

    def record_run(self, status: str, summary: dict) -> str:
        run_id = uuid4().hex
        self.db.execute(
            "INSERT INTO processing_runs VALUES (?, ?, ?, ?)",
            [run_id, now(), status, encode(summary)],
        )
        return run_id

    def status(self):
        sources = [
            {
                "path": r[0],
                "hash": r[1],
                "size": r[2],
                "status": r[3],
                "error": r[4],
                "last_seen": r[5],
            }
            for r in self.db.execute("SELECT * FROM sources ORDER BY path").fetchall()
        ]
        counts = {}
        for source in sources:
            counts[source["status"]] = counts.get(source["status"], 0) + 1
        missing_samples = [
            a["id"]
            for a in self.activities()
            if not a.get("sample_path") or not (self.root / a["sample_path"]).is_file()
        ]
        duplicate_hashes = [
            {"hash": r[0], "paths": r[1]}
            for r in self.db.execute(
                "SELECT hash, list(path) FROM sources WHERE hash IS NOT NULL GROUP BY hash HAVING count(*) > 1"
            ).fetchall()
        ]
        quality = {}
        for activity in self.activities():
            for flag in activity.get("quality_flags", []):
                quality[flag] = quality.get(flag, 0) + 1
        acquisition_audits = []
        for r in self.db.execute("SELECT * FROM acquisition_audits").fetchall():
            audit_meta = {}
            if r[2]:
                try:
                    audit_meta = json.loads(r[2])
                except Exception:
                    audit_meta = {}
            acquisition_audits.append({"root": r[0], "audited_at": r[1], **audit_meta})

        return {
            "activities": self.db.execute("SELECT count(*) FROM activities").fetchone()[
                0
            ],
            "metric_snapshots": self.db.execute(
                "SELECT count(*) FROM metrics"
            ).fetchone()[0],
            "source_counts": counts,
            "sources": sources,
            "missing_samples": missing_samples,
            "duplicate_hashes": duplicate_hashes,
            "quality_counts": quality,
            "acquisition_audits": acquisition_audits,
        }
