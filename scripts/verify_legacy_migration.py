"""Compare a few owner activities between local DuckDB and Neon/Blob."""

import argparse
import os
from pathlib import Path

import psycopg

from cycling.models import ActivityRequest
from cycling.postgres_store import PostgresStore
from cycling.service import CyclingService
from cycling.storage import Store


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default=".cycling")
    parser.add_argument("--user-email", required=True)
    args = parser.parse_args()
    database_url = os.environ.get("DATABASE_URL_UNPOOLED") or os.environ.get(
        "DATABASE_URL"
    )
    if not database_url:
        raise SystemExit("Set DATABASE_URL_UNPOOLED or DATABASE_URL")
    with psycopg.connect(database_url, autocommit=True) as connection:
        user = connection.execute(
            "SELECT user_id FROM cycling_users WHERE email=%s AND is_active=true",
            (args.user_email.strip().lower(),),
        ).fetchone()
    if user is None:
        raise SystemExit("No active account found")

    compared = 0
    with (
        Store(Path(args.data_dir)) as local,
        PostgresStore(database_url, user[0]) as remote,
    ):
        local_activities = local.activities()
        remote_activities = {
            activity["id"]: activity for activity in remote.activities()
        }
        if {activity["id"] for activity in local_activities} != set(remote_activities):
            raise SystemExit("Local and remote activity ID sets differ")

        latest = max(local_activities, key=lambda activity: activity["start_time"])
        longest = max(
            local_activities, key=lambda activity: activity.get("duration_s") or 0
        )
        missing_power = next(
            (
                activity
                for activity in local_activities
                if "missing_power_w" in activity.get("quality_flags", [])
            ),
            None,
        )
        selected = {
            activity["id"]: activity
            for activity in (latest, longest, missing_power)
            if activity
        }
        local_service, remote_service = CyclingService(local), CyclingService(remote)
        for activity_id in selected:
            local_activity = local.activity(activity_id)
            remote_activity = remote.activity(activity_id)
            for field in (
                "source_hash",
                "start_time",
                "duration_s",
                "modality",
                "quality_flags",
            ):
                local_value = local_activity.get(field)
                remote_value = remote_activity.get(field)
                if field == "quality_flags":
                    local_value, remote_value = (
                        set(local_value or []),
                        set(remote_value or []),
                    )
                if local_value != remote_value:
                    raise SystemExit(f"Activity metadata mismatch in {field}")
            request = ActivityRequest(
                activity_id=activity_id, parameter_mode="historical"
            )
            local_samples = local.samples(activity_id)
            remote_samples = remote.samples(activity_id)
            if local_samples != remote_samples:
                raise SystemExit("Normalized sample mismatch")
            local_metrics = local_service.analyze_activity(request, force=True).data[
                "metrics"
            ]
            remote_metrics = remote_service.analyze_activity(request, force=True).data[
                "metrics"
            ]
            if local_metrics != remote_metrics:
                raise SystemExit("Deterministic metric payload mismatch")
            compared += 1

    print(
        f"Migration parity passed: {len(remote_activities)} activities, {compared} representative metric checks"
    )


if __name__ == "__main__":
    main()
