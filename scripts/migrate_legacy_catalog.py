"""Dry-run or migrate the local DuckDB catalog into one authenticated account."""

import argparse
import hashlib
import json
import os
from datetime import date, datetime
from pathlib import Path

import psycopg

from cycling.models import ActivityRequest
from cycling.parsers import MAX_SOURCE_BYTES
from cycling.postgres_store import PostgresStore
from cycling.service import CyclingService
from cycling.storage import Store


def _recorded_at(value: str | datetime) -> datetime:
    return (
        value
        if isinstance(value, datetime)
        else datetime.fromisoformat(value.replace("Z", "+00:00"))
    )


def _original_bytes(store: Store, digest: str) -> bytes | None:
    rows = store.db.execute(
        "SELECT path FROM sources WHERE hash=? AND status='ready' ORDER BY last_seen DESC",
        [digest],
    ).fetchall()
    for (source,) in rows:
        path = Path(source)
        try:
            if path.stat().st_size > MAX_SOURCE_BYTES:
                continue
            content = path.read_bytes()
        except OSError:
            continue
        if hashlib.sha256(content).hexdigest() == digest:
            return content
    return None


def migrate(
    data_dir: Path,
    database_url: str,
    email: str,
    apply: bool,
    include_originals: bool,
    object_root: Path | None = None,
    precompute_metrics: bool = False,
) -> dict:
    with psycopg.connect(database_url, autocommit=True) as connection:
        user = connection.execute(
            "SELECT user_id FROM cycling_users WHERE email=%s AND is_active=true AND email_verified=true",
            (email.strip().lower(),),
        ).fetchone()
    if user is None:
        raise ValueError(
            "No active Google account exists for that email; invite and sign in first"
        )
    user_id = user[0]

    summary = {
        "parameters": 0,
        "activities_imported": 0,
        "activities_skipped": 0,
        "activities_without_samples": 0,
        "originals_available": 0,
        "originals_retained": 0,
        "originals_missing": 0,
        "originals_not_requested": 0,
        "contexts_imported": 0,
        "metric_snapshots_recomputed_on_demand": True,
        "metric_snapshots_precomputed": 0,
        "metric_precompute_failures": 0,
        "applied": apply,
    }

    with Store(data_dir) as legacy:
        activities = legacy.activities()
        summary["parameters"] = len(legacy.parameter_history())
        summary["activities_discovered"] = len(activities)
        contexts = legacy.db.execute(
            "SELECT id, activity_id, recorded_at, context FROM activity_context ORDER BY recorded_at, id"
        ).fetchall()
        summary["context_revisions"] = len(contexts)

        if not apply:
            for activity in activities:
                sample_path = activity.get("sample_path")
                if not sample_path or not (legacy.root / sample_path).is_file():
                    summary["activities_without_samples"] += 1
                elif include_originals:
                    original = _original_bytes(
                        legacy, activity.get("source_hash") or activity["id"]
                    )
                    if original is None:
                        summary["originals_missing"] += 1
                    else:
                        summary["originals_available"] += 1
                else:
                    summary["originals_not_requested"] += 1
            return summary

        with PostgresStore(database_url, user_id, object_root=object_root) as target:
            for item in legacy.parameter_history():
                target.import_parameters(
                    item["id"],
                    date.fromisoformat(item["effective_date"]),
                    _recorded_at(item["recorded_at"]),
                    item["settings"],
                )

            eligible_ids = set()
            for activity in activities:
                rows = legacy.samples(activity["id"])
                if not rows:
                    summary["activities_without_samples"] += 1
                    continue
                eligible_ids.add(activity["id"])
                original = None
                if include_originals:
                    original = _original_bytes(
                        legacy, activity.get("source_hash") or activity["id"]
                    )
                    if original is None:
                        summary["originals_missing"] += 1
                    else:
                        summary["originals_available"] += 1
                else:
                    summary["originals_not_requested"] += 1
                imported = target.import_activity(
                    activity["id"], activity, rows, original_content=original
                )
                if imported:
                    summary["activities_imported"] += 1
                else:
                    summary["activities_skipped"] += 1
                migrated_activity = target.activity(activity["id"])
                original_path = migrated_activity.get("original_path")
                if original is not None and original_path:
                    summary["originals_retained"] += 1
                digest = activity.get("source_hash") or activity["id"]
                target.source(
                    f"legacy/{activity['id']}/{activity.get('source_name') or 'activity'}",
                    digest,
                    len(original) if original is not None else None,
                    "ready" if original_path else "missing",
                    None if original_path else "Original file was not migrated",
                )

            for context_id, activity_id, recorded_at, context in contexts:
                if activity_id not in eligible_ids:
                    continue
                imported = target.import_context(
                    activity_id,
                    context_id,
                    _recorded_at(recorded_at),
                    json.loads(context) if isinstance(context, str) else context,
                )
                summary["contexts_imported"] += int(imported)

            if precompute_metrics:
                service = CyclingService(target)
                for activity in target.activities():
                    try:
                        service.analyze_activity(
                            ActivityRequest(activity_id=activity["id"])
                        )
                        summary["metric_snapshots_precomputed"] += 1
                    except Exception:
                        summary["metric_precompute_failures"] += 1

    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default=".cycling")
    parser.add_argument("--user-email", required=True)
    parser.add_argument(
        "--apply", action="store_true", help="Write to Neon/Blob; default is dry-run"
    )
    parser.add_argument(
        "--include-originals",
        action="store_true",
        help="Upload raw FIT/TCX files if their local sources still exist",
    )
    parser.add_argument(
        "--precompute-metrics",
        action="store_true",
        help="Precompute historical activity metrics to warm dashboard caches",
    )
    args = parser.parse_args()
    database_url = os.environ.get("DATABASE_URL_UNPOOLED") or os.environ.get(
        "DATABASE_URL"
    )
    if not database_url:
        raise SystemExit("Set DATABASE_URL_UNPOOLED or DATABASE_URL")
    if args.precompute_metrics and not args.apply:
        raise SystemExit("--precompute-metrics requires --apply")
    result = migrate(
        Path(args.data_dir),
        database_url,
        args.user_email,
        args.apply,
        args.include_originals,
        precompute_metrics=args.precompute_metrics,
    )
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
