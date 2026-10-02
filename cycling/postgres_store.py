"""Owner-scoped catalog; originals and Parquet live outside PostgreSQL.

Apply migrations/002_catalog.sql separately. Requires psycopg 3 and, for remote
objects, vercel==0.11.4. Supply user_id ONLY from verified server authentication,
never from request data. A store/connection belongs to one request, not a global
singleton. The auth layer owns invitation checks and account lifecycle.
"""

import hashlib
import json
import os
import re
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal, Sequence
from uuid import uuid4

import pyarrow as pa
import pyarrow.parquet as pq

from cycling.config import NORMALIZER_VERSION, SUPPORTED_EXTENSIONS
from cycling.models import ActivityContext, AthleteParameters
from cycling.normalization import normalize
from cycling.parsers import FIELDS, parse_file


def _user_id(value):
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError("An authenticated user_id is required")
    if len(value) > 256 or "\x00" in value:
        raise ValueError("Invalid user_id")
    return value


def _json(value):
    # Preserve legacy datetime encoding, reject NaN instead of persisting it.
    return json.dumps(value, default=str, allow_nan=False)


class _ObjectStore:
    """Private, immutable, server-generated keys; no URL-based retrieval."""

    def __init__(self, user_id: str, root: str | Path | None = None):
        owner = hashlib.sha256(_user_id(user_id).encode()).hexdigest()
        self.prefix = f"users/{owner}/"
        self.client = None
        self.root = None
        token = os.environ.get("BLOB_READ_WRITE_TOKEN")
        on_vercel = "VERCEL" in os.environ
        local_override = os.environ.get("CYCLING_LOCAL_OBJECTS") == "1"
        oidc_configured = bool(
            os.environ.get("VERCEL_OIDC_TOKEN") and os.environ.get("BLOB_STORE_ID")
        )
        if (
            local_override
            and on_vercel
            and os.environ.get("VERCEL_ENV") != "development"
        ):
            raise ValueError(
                "CYCLING_LOCAL_OBJECTS is allowed only outside Vercel deployments"
            )
        if local_override:
            self.root = Path(
                root or os.environ.get("CYCLING_OBJECT_ROOT", ".cycling/objects")
            ).resolve()
            self.root.mkdir(parents=True, exist_ok=True)
        elif on_vercel or token or oidc_configured:
            if on_vercel and not token and not oidc_configured:
                raise ValueError(
                    "Vercel Blob credentials are not configured; local fallback is forbidden"
                )
            from vercel.blob import BlobClient

            self.client = BlobClient(token=token) if token else BlobClient()
        else:
            self.root = Path(
                root or os.environ.get("CYCLING_OBJECT_ROOT", ".cycling/objects")
            ).resolve()
            self.root.mkdir(parents=True, exist_ok=True)

    def _validate(self, key):
        if not isinstance(key, str) or not key.startswith(self.prefix):
            raise ValueError("Object does not belong to this user")
        relative = key[len(self.prefix) :]
        if not re.fullmatch(
            r"(?:samples/[0-9a-f]{32}\.parquet|originals/[0-9a-f]{32}\.(?:fit|tcx)(?:\.gz)?)",
            relative,
        ):
            raise ValueError("Invalid object key")
        if self.root is not None:
            target = (self.root / key).resolve()
            if not target.is_relative_to(self.root):
                raise ValueError("Object path escapes local storage")
            return target

    def put(self, content: bytes, kind: str, suffix: str) -> str:
        key = f"{self.prefix}{kind}/{uuid4().hex}{suffix}"
        target = self._validate(key)
        if self.client is not None:
            self.client.put(
                key, content, access="private", add_random_suffix=False, overwrite=False
            )
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            # Exclusive creation; DB references are published only after this closes.
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            fd = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        return key

    def read(self, key: str) -> bytes:
        target = self._validate(key)
        if self.client is None:
            return target.read_bytes()
        from vercel.blob import BlobNotFoundError

        try:
            return self.client.get(key, access="private").content
        except BlobNotFoundError as exc:
            raise FileNotFoundError(key) from exc

    def exists(self, key: str) -> bool:
        target = self._validate(key)
        if self.client is None:
            return target.is_file()
        from vercel.blob import BlobNotFoundError

        try:
            self.client.head(key)
            return True
        except BlobNotFoundError:
            return False

    def delete(self, key: str) -> None:
        target = self._validate(key)
        if self.client is not None:
            self.client.delete(key)
        else:
            target.unlink(missing_ok=True)

    def close(self):
        if self.client is not None:
            self.client.close()


class PostgresStore:
    def __init__(
        self, database_url: str, user_id: str, *, object_root: str | Path | None = None
    ):
        self.user_id = _user_id(user_id)
        self.personalized_hr_zones = True
        self._sample_paths = {}
        import psycopg
        from psycopg.rows import dict_row

        self.objects = _ObjectStore(self.user_id, object_root)
        try:
            self.db = psycopg.connect(
                database_url, autocommit=True, row_factory=dict_row
            )
            user = self.db.execute(
                "SELECT 1 FROM cycling_users WHERE user_id=%s AND email_verified=true AND is_active=true",
                (self.user_id,),
            ).fetchone()
            if user is None:
                raise PermissionError("Authenticated app user is not active")
            self.db.execute(
                "INSERT INTO cycling_athletes (user_id) VALUES (%s) ON CONFLICT DO NOTHING",
                (self.user_id,),
            )
        except Exception:
            if hasattr(self, "db"):
                self.db.close()
            self.objects.close()
            raise

    def close(self):
        try:
            self.db.close()
        finally:
            self.objects.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _lock(self):
        # ponytail: serialize publications per athlete; use per-activity locks if needed.
        self.db.execute(
            "SELECT user_id FROM cycling_athletes WHERE user_id=%s FOR UPDATE",
            (self.user_id,),
        ).fetchone()

    def add_parameters(self, parameters: AthleteParameters) -> str:
        ident = uuid4().hex
        self.db.execute(
            "INSERT INTO cycling_parameters (user_id, id, effective_date, settings) VALUES (%s,%s,%s,%s::jsonb)",
            (
                self.user_id,
                ident,
                parameters.effective_date,
                parameters.model_dump_json(),
            ),
        )
        return ident

    def import_parameters(
        self,
        ident: str,
        effective_date: date,
        recorded_at: datetime,
        settings: dict,
    ) -> None:
        """Import legacy append-only settings without changing their provenance."""
        parameters = AthleteParameters.model_validate(settings)
        self.db.execute(
            "INSERT INTO cycling_parameters (user_id,id,effective_date,recorded_at,settings) "
            "VALUES (%s,%s,%s,%s,%s::jsonb) ON CONFLICT (user_id,id) DO NOTHING",
            (
                self.user_id,
                ident,
                effective_date,
                recorded_at,
                parameters.model_dump_json(),
            ),
        )

    def import_context(
        self,
        activity_id: str,
        ident: str,
        recorded_at: datetime,
        context: dict,
    ) -> bool:
        """Import one legacy activity-context revision idempotently."""
        values = ActivityContext.model_validate(context).model_dump(exclude_none=True)
        inserted = self.db.execute(
            "INSERT INTO cycling_activity_context (user_id,id,activity_id,recorded_at,context) "
            "VALUES (%s,%s,%s,%s,%s::jsonb) ON CONFLICT (user_id,id) DO NOTHING RETURNING id",
            (self.user_id, ident, activity_id, recorded_at, _json(values)),
        ).fetchone()
        return inserted is not None

    def parameters(
        self,
        when: date | datetime | str | None = None,
        mode: Literal["historical", "current"] = "historical",
    ):
        if mode not in {"historical", "current"}:
            raise ValueError("Invalid parameter mode")
        dt = datetime.fromisoformat(str(when)).date() if when is not None else None
        row = None
        if mode == "historical" and dt is not None:
            row = self.db.execute(
                "SELECT id, settings FROM cycling_parameters WHERE user_id=%s AND effective_date<=%s "
                "ORDER BY effective_date DESC, recorded_at DESC, id DESC LIMIT 1",
                (self.user_id, dt),
            ).fetchone()
            if row is None:
                row = self.db.execute(
                    "SELECT id, settings FROM cycling_parameters WHERE user_id=%s "
                    "ORDER BY effective_date, recorded_at, id LIMIT 1",
                    (self.user_id,),
                ).fetchone()
        else:
            row = self.db.execute(
                "SELECT id, settings FROM cycling_parameters WHERE user_id=%s "
                "ORDER BY effective_date DESC, recorded_at DESC, id DESC LIMIT 1",
                (self.user_id,),
            ).fetchone()
        return (
            (row["id"], AthleteParameters.model_validate(row["settings"]))
            if row
            else (None, None)
        )

    def parameter_history(self):
        rows = self.db.execute(
            "SELECT id, effective_date, recorded_at, settings FROM cycling_parameters "
            "WHERE user_id=%s ORDER BY effective_date, recorded_at, id",
            (self.user_id,),
        ).fetchall()
        return [
            {
                **r,
                "effective_date": str(r["effective_date"]),
                "recorded_at": r["recorded_at"].isoformat(),
            }
            for r in rows
        ]

    def source(
        self,
        path: Path | str,
        digest: str | None,
        size: int | None,
        status: str,
        error=None,
    ):
        # Path is display/provenance text, never a path to open or an object key.
        self.db.execute(
            "INSERT INTO cycling_sources (user_id,path,hash,size,status,error) VALUES (%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (user_id,path) DO UPDATE SET hash=EXCLUDED.hash,size=EXCLUDED.size,"
            "status=EXCLUDED.status,error=EXCLUDED.error,last_seen=clock_timestamp()",
            (self.user_id, str(path), digest, size, status, error),
        )

    def activity(self, ident: str):
        row = self.db.execute(
            "SELECT a.*, c.id AS context_id, c.context, ARRAY(SELECT flag FROM cycling_quality_flags "
            "WHERE user_id=%s AND activity_id=a.id ORDER BY flag) AS quality_flags "
            "FROM cycling_activities a LEFT JOIN LATERAL (SELECT id,context FROM cycling_activity_context "
            "WHERE user_id=%s AND activity_id=a.id ORDER BY recorded_at DESC,id DESC LIMIT 1) c ON true "
            "WHERE a.user_id=%s AND a.id=%s",
            (self.user_id, self.user_id, self.user_id, ident),
        ).fetchone()
        if row is None:
            raise KeyError(f"Activity not found: {ident}")
        context = row.pop("context") or {}
        metadata = row.pop("metadata")
        row.pop("user_id")
        self._sample_paths[ident] = row["sample_path"]
        # Untrusted metadata must not override ownership, IDs or storage references.
        return {
            **metadata,
            **row,
            "ingested_at": row["ingested_at"].isoformat(),
            **context,
        }

    def activities(self):
        rows = self.db.execute(
            "SELECT id FROM cycling_activities WHERE user_id=%s ORDER BY start_time DESC, id",
            (self.user_id,),
        ).fetchall()
        return [self.activity(r["id"]) for r in rows]

    def set_context(self, ident: str, context: ActivityContext):
        with self.db.transaction():
            self._lock()
            activity = self.activity(ident)
            values = {k: activity[k] for k in ("rpe", "modality") if k in activity}
            values.update(context.model_dump(exclude_none=True))
            context_id = uuid4().hex
            self.db.execute(
                "INSERT INTO cycling_activity_context (user_id,id,activity_id,context) VALUES (%s,%s,%s,%s::jsonb)",
                (self.user_id, context_id, ident, _json(values)),
            )
        return context_id

    def _register_file(self, key: str, kind: str, content: bytes) -> str:
        digest = hashlib.sha256(content).hexdigest()
        inserted = self.db.execute(
            "INSERT INTO cycling_files (user_id,object_key,kind,sha256,size) VALUES (%s,%s,%s,%s,%s) "
            "ON CONFLICT (user_id,object_key) DO NOTHING RETURNING object_key",
            (self.user_id, key, kind, digest, len(content)),
        ).fetchone()
        if inserted is None:
            existing = self.db.execute(
                "SELECT kind,sha256,size FROM cycling_files WHERE user_id=%s AND object_key=%s",
                (self.user_id, key),
            ).fetchone()
            if not existing or (
                existing["kind"],
                existing["sha256"],
                existing["size"],
            ) != (kind, digest, len(content)):
                raise ValueError(
                    "Immutable object key is already registered with different content"
                )
        return key

    def _file(self, content: bytes, kind: str, suffix: str) -> str:
        key = self.objects.put(content, kind, suffix)
        return self._register_file(key, kind, content)

    def write_activity(self, ident: str, metadata: dict, rows: list[dict]) -> str:
        with self.db.transaction():
            self._lock()
            ident = self._write_activity(ident, metadata, rows)
        self._sample_paths.pop(ident, None)
        return ident

    def import_activity(
        self,
        ident: str,
        metadata: dict,
        rows: list[dict],
        original_content: bytes | None = None,
    ) -> bool:
        """Idempotently import normalized legacy samples and optional original bytes."""
        digest = metadata.get("source_hash") or ident
        if not rows:
            raise ValueError("Cannot import an activity without normalized samples")
        if (
            original_content is not None
            and hashlib.sha256(original_content).hexdigest() != digest
        ):
            raise ValueError("Legacy original hash does not match the activity catalog")
        source_name = str(metadata.get("source_name", ""))
        suffix = next(
            (
                value
                for value in SUPPORTED_EXTENSIONS
                if source_name.lower().endswith(value)
            ),
            None,
        )
        with self.db.transaction():
            self._lock()
            existing = self.db.execute(
                "SELECT id,source_hash,original_path FROM cycling_activities "
                "WHERE user_id=%s AND (id=%s OR source_hash=%s)",
                (self.user_id, ident, digest),
            ).fetchone()
            if existing:
                if existing["source_hash"] != digest:
                    raise ValueError(
                        "Legacy activity ID conflicts with another source hash"
                    )
                if original_content is not None and existing["original_path"] is None:
                    if suffix is None:
                        raise ValueError("Cannot determine legacy original file type")
                    original_path = self._file(original_content, "originals", suffix)
                    self.db.execute(
                        "UPDATE cycling_activities SET original_path=%s WHERE user_id=%s AND id=%s",
                        (original_path, self.user_id, existing["id"]),
                    )
                return False
            original_path = None
            if original_content is not None:
                if suffix is None:
                    raise ValueError("Cannot determine legacy original file type")
                original_path = self._file(original_content, "originals", suffix)
            clean_metadata = {
                key: value
                for key, value in metadata.items()
                if key
                not in {
                    "id",
                    "user_id",
                    "sample_path",
                    "normalizer_version",
                    "ingested_at",
                    "context_id",
                    "rpe",
                }
            }
            self._write_activity(ident, clean_metadata, rows, original_path)
        self._sample_paths.pop(ident, None)
        return True

    def _write_activity(self, ident, metadata, rows, original_path=None):
        digest = metadata.get("source_hash") or ident
        existing = self.db.execute(
            "SELECT id,source_hash,original_path FROM cycling_activities WHERE user_id=%s AND (id=%s OR source_hash=%s)",
            (self.user_id, ident, digest),
        ).fetchall()
        if any(r["source_hash"] != digest for r in existing):
            raise ValueError("Activity ID already refers to a different source")
        if existing:
            ident = existing[0]["id"]
            original_path = original_path or existing[0]["original_path"]
        fields = [
            pa.field("timestamp", pa.timestamp("us", tz="UTC")),
            pa.field("elapsed_s", pa.int64()),
            pa.field("segment", pa.int64()),
            pa.field("active", pa.bool_()),
        ]
        fields += [pa.field(name, pa.float64()) for name in FIELDS]
        buffer = pa.BufferOutputStream()
        pq.write_table(
            pa.Table.from_pylist(rows, schema=pa.schema(fields)),
            buffer,
            compression="zstd",
        )
        sample_path = self._file(buffer.getvalue().to_pybytes(), "samples", ".parquet")
        self.db.execute(
            "INSERT INTO cycling_activities (user_id,id,source_hash,strava_id_hint,start_time,duration_s,"
            "modality,metadata,sample_path,original_path,normalizer_version) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s) "
            "ON CONFLICT (user_id,id) DO UPDATE SET strava_id_hint=EXCLUDED.strava_id_hint,"
            "start_time=EXCLUDED.start_time,duration_s=EXCLUDED.duration_s,modality=EXCLUDED.modality,"
            "metadata=EXCLUDED.metadata,sample_path=EXCLUDED.sample_path,original_path=EXCLUDED.original_path,"
            "normalizer_version=EXCLUDED.normalizer_version,ingested_at=clock_timestamp()",
            (
                self.user_id,
                ident,
                digest,
                metadata.get("strava_id_hint"),
                metadata["start_time"],
                metadata.get("elapsed_seconds", len(rows)),
                metadata.get("modality", "unknown"),
                _json(metadata),
                sample_path,
                original_path,
                NORMALIZER_VERSION,
            ),
        )
        self.db.execute(
            "DELETE FROM cycling_quality_flags WHERE user_id=%s AND activity_id=%s",
            (self.user_id, ident),
        )
        for flag in set(metadata.get("quality_flags", [])):
            self.db.execute(
                "INSERT INTO cycling_quality_flags VALUES (%s,%s,%s)",
                (self.user_id, ident, flag),
            )
        self.db.execute(
            "DELETE FROM cycling_activity_laps WHERE user_id=%s AND activity_id=%s",
            (self.user_id, ident),
        )
        for lap in metadata.get("laps", []):
            self.db.execute(
                "INSERT INTO cycling_activity_laps (user_id,activity_id,lap_index,lap) VALUES (%s,%s,%s,%s::jsonb)",
                (self.user_id, ident, lap["lap_index"], _json(lap)),
            )
        # Files are immutable. Retain previous revisions for cached result provenance.
        # Failed/ambiguous commits may leave orphans; never delete a possibly committed object.
        return ident

    def samples(self, ident: str, columns: Sequence[str] | None = None):
        # Pin samples to the last authorized activity read in this request. A
        # concurrent reingest must not mix new samples with an old cache key.
        key = self._sample_paths.get(ident)
        if key is None:
            key = self.activity(ident)["sample_path"]
        return pq.read_table(
            pa.BufferReader(self.objects.read(key)),
            columns=list(columns) if columns else None,
        ).to_pylist()

    def cached(self, ident: str, key: str):
        row = self.db.execute(
            "SELECT result FROM cycling_metrics WHERE user_id=%s AND activity_id=%s AND cache_key=%s "
            "ORDER BY computed_at DESC,id DESC LIMIT 1",
            (self.user_id, ident, key),
        ).fetchone()
        return row["result"] if row else None

    def save_metrics(self, ident: str, key: str, result: dict):
        activity = self.activity(ident)
        # Snapshot references come from the analyzed revision, not a concurrent reingest.
        snapshot = (result.get("data") or {}).get("activity") or activity
        sample_path = snapshot.get("sample_path", activity["sample_path"])
        self.objects._validate(sample_path)
        self.db.execute(
            "INSERT INTO cycling_metrics (user_id,id,activity_id,cache_key,parameter_id,context_id,sample_path,result) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb)",
            (
                self.user_id,
                uuid4().hex,
                ident,
                key,
                result.get("parameter_id"),
                snapshot.get("context_id"),
                sample_path,
                _json(result),
            ),
        )

    def record_run(self, status: str, summary: dict) -> str:
        ident = uuid4().hex
        self.db.execute(
            "INSERT INTO cycling_processing_runs (user_id,id,status,summary) VALUES (%s,%s,%s,%s::jsonb)",
            (self.user_id, ident, status, _json(summary)),
        )
        return ident

    def ingest_upload(
        self,
        filename: str,
        content: bytes,
        force: bool = False,
        original_path: str | None = None,
    ) -> dict:
        """Parse authenticated upload bytes; never fetch caller-provided paths/URLs.

        Caller must enforce upload/body limits. This synchronous path buffers files
        in memory; large direct-to-Blob upload/job orchestration is not implemented.
        """
        if (
            not isinstance(filename, str)
            or Path(filename).name != filename
            or "\\" in filename
        ):
            raise ValueError("Expected a filename, not a path or URL")
        suffix = next(
            (s for s in SUPPORTED_EXTENSIONS if filename.lower().endswith(s)), None
        )
        if not suffix or not isinstance(content, bytes) or not content:
            raise ValueError("Expected nonempty FIT/TCX upload bytes")
        if original_path is not None:
            self.objects._validate(original_path)
            if not original_path.startswith(self.objects.prefix + "originals/"):
                raise ValueError("Original object must belong to this user")
        digest = hashlib.sha256(content).hexdigest()
        started_at = self.db.execute(
            "SELECT clock_timestamp() AS started_at FROM cycling_athletes WHERE user_id=%s",
            (self.user_id,),
        ).fetchone()["started_at"]
        try:
            with self.db.transaction():
                self._lock()
                existing = self.db.execute(
                    "SELECT id,sample_path,original_path,normalizer_version FROM cycling_activities "
                    "WHERE user_id=%s AND source_hash=%s",
                    (self.user_id, digest),
                ).fetchone()
                ready = (
                    existing
                    and existing["normalizer_version"] == NORMALIZER_VERSION
                    and existing["original_path"]
                    and self.objects.exists(existing["sample_path"])
                    and self.objects.exists(existing["original_path"])
                )
                if ready and not force:
                    ident, ingested = existing["id"], False
                    if original_path and original_path != existing["original_path"]:
                        self.objects.delete(original_path)
                else:
                    with TemporaryDirectory(prefix="cycling-parse-") as temporary:
                        path = Path(temporary) / ("upload" + suffix)
                        path.write_bytes(content)
                        metadata, raw = parse_file(path)
                        metadata, rows = normalize(metadata, raw)
                    metadata.update(source_name=filename, source_hash=digest)
                    hint = re.search(r"(?:^|[^0-9])(\d{7,12})(?:[^0-9]|$)", filename)
                    if hint:
                        metadata["strava_id_hint"] = hint.group(1)
                    original = original_path or (
                        existing["original_path"] if existing else None
                    )
                    if not original or not self.objects.exists(original):
                        original = self._file(content, "originals", suffix)
                    elif original_path:
                        self._register_file(original, "originals", content)
                    ident = self._write_activity(digest, metadata, rows, original)
                    ingested = True
                self.source(filename, digest, len(content), "ready")
                summary = {"activity_id": ident, "ingested": ingested}
                run_id = self.record_run("completed", summary)
            self._sample_paths.pop(ident, None)
            return {**summary, "run_id": run_id}
        except Exception as exc:
            # Best-effort diagnostics must not replace the original failure.
            try:
                with self.db.transaction():
                    self._lock()
                    error = f"{type(exc).__name__}: {exc}"
                    current = self.db.execute(
                        "SELECT last_seen FROM cycling_sources WHERE user_id=%s AND path=%s",
                        (self.user_id, filename),
                    ).fetchone()
                    if current is None or current["last_seen"] <= started_at:
                        self.source(filename, digest, len(content), "error", error)
                    self.record_run(
                        "failed",
                        {
                            "source_name": filename,
                            "source_hash": digest,
                            "error": error,
                        },
                    )
            except Exception:
                pass
            raise

    def ingest_blob(self, filename: str, object_key: str, force: bool = False) -> dict:
        """Ingest a completed private Blob upload after validating its owner key."""
        self.objects._validate(object_key)
        if not object_key.startswith(self.objects.prefix + "originals/"):
            raise ValueError("Original object must belong to this user")
        return self.ingest_upload(
            filename,
            self.objects.read(object_key),
            force=force,
            original_path=object_key,
        )

    def status(self):
        sources = self.db.execute(
            "SELECT path,hash,size,status,error,last_seen FROM cycling_sources WHERE user_id=%s ORDER BY path",
            (self.user_id,),
        ).fetchall()
        by_hash = defaultdict(list)
        for source in sources:
            source["last_seen"] = source["last_seen"].isoformat()
            if source["hash"]:
                by_hash[source["hash"]].append(source["path"])
        activities = self.activities()
        count = self.db.execute(
            "SELECT count(*) AS n FROM cycling_metrics WHERE user_id=%s",
            (self.user_id,),
        ).fetchone()["n"]
        return {
            "activities": len(activities),
            "metric_snapshots": count,
            "source_counts": dict(Counter(s["status"] for s in sources)),
            "sources": sources,
            "missing_samples": [
                a["id"] for a in activities if not self.objects.exists(a["sample_path"])
            ],
            "duplicate_hashes": [
                {"hash": h, "paths": paths}
                for h, paths in by_hash.items()
                if len(paths) > 1
            ],
            "quality_counts": dict(
                Counter(f for a in activities for f in a["quality_flags"])
            ),
            "acquisition_audits": [],
        }
