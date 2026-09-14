"""Local cycling analysis CLI; JSON output is suitable for automation."""

import argparse
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

from cycling.ingestion import ingest
from cycling.models import (
    ActivityContext,
    ActivityRequest,
    AthleteParameters,
    ComparisonRequest,
    CurveRequest,
    DurabilityRequest,
    LoadRequest,
    PeriodPowerCurveRequest,
    StreamRequest,
)
from cycling.service import CyclingService
from cycling.storage import Store


def parser():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--data-dir", default=".cycling")
    commands = cli.add_subparsers(dest="command", required=True)
    ingestion = commands.add_parser("ingest")
    ingestion.add_argument("source", nargs="?", default="downloads/strava")
    ingestion.add_argument("--force", action="store_true")
    ingestion.add_argument(
        "--new-only",
        action="store_true",
        help="Ingest new files only (default behavior)",
    )
    activities_list = (
        commands.add_parser("activities")
        .add_subparsers(dest="action", required=True)
        .add_parser("list")
    )
    activities_list.add_argument(
        "--limit", type=int, default=None, help="Limit number of activities returned"
    )
    activities_list.add_argument(
        "--modality",
        choices=["indoor", "road", "mtb", "gravel", "unknown", "all"],
        default="all",
        help="Filter activities by modality",
    )
    activity = commands.add_parser("activity").add_subparsers(
        dest="action", required=True
    )
    for action in ("analyze", "get"):
        sub = activity.add_parser(action)
        sub.add_argument("activity_id")
        sub.add_argument(
            "--parameter-mode", choices=["historical", "current"], default="historical"
        )
        sub.add_argument("--rpe", type=float)
    context = activity.add_parser("set-context")
    context.add_argument("activity_id")
    context.add_argument("--rpe", type=float)
    context.add_argument(
        "--modality", choices=["road", "mtb", "gravel", "indoor", "unknown"]
    )
    activity.add_parser("delete").add_argument("activity_id")
    pc = commands.add_parser("power-curve")
    pc.add_argument("activity_id", nargs="?")
    pc.add_argument(
        "--parameter-mode", choices=["historical", "current"], default="historical"
    )
    pc.add_argument(
        "--durations",
        nargs="+",
        type=int,
        default=[
            5,
            30,
            60,
            300,
            600,
            900,
            1200,
            1800,
            2700,
            3600,
            4500,
            5400,
            6300,
            7200,
            9000,
            10800,
            12600,
            14400,
            16200,
            18000,
            19800,
            21600,
        ],
    )
    pc.add_argument(
        "--period",
        choices=["7d", "21d", "30d", "90d", "365d", "all", "custom"],
        default="all",
    )
    pc.add_argument(
        "--modality",
        choices=["indoor", "road", "mtb", "gravel", "unknown", "all"],
        default="all",
    )
    pc.add_argument("--start-date", help="Custom range start date (YYYY-MM-DD)")
    pc.add_argument(
        "--end-date", help="Reference or custom range end date (YYYY-MM-DD)"
    )
    for command in ("durability", "drift", "thresholds", "stream"):
        sub = commands.add_parser(command)
        sub.add_argument("activity_id")
        sub.add_argument(
            "--parameter-mode", choices=["historical", "current"], default="historical"
        )
        if command == "durability":
            sub.add_argument(
                "--durations",
                nargs="+",
                type=int,
                default=[300, 1200, 1800, 3600],
            )
            sub.add_argument(
                "--thresholds-kj",
                nargs="+",
                type=float,
                default=[1000.0, 1500.0, 1800.0, 2100.0, 2200.0],
            )
            sub.add_argument("--bucket-kj", nargs="+", type=float, default=None)
        if command == "stream":
            sub.add_argument("--start-s", type=int, default=0)
            sub.add_argument("--end-s", type=int)
            sub.add_argument("--max-points", type=int, default=2000)
    power_curves = commands.add_parser("power-curves")
    power_curves.add_argument(
        "--period",
        choices=["7d", "21d", "30d", "90d", "365d", "all", "custom"],
        default="all",
    )
    power_curves.add_argument(
        "--modality",
        choices=["indoor", "road", "mtb", "gravel", "unknown", "all"],
        default="all",
    )
    power_curves.add_argument(
        "--durations",
        nargs="+",
        type=int,
        default=[
            5,
            30,
            60,
            300,
            600,
            900,
            1200,
            1800,
            2700,
            3600,
            4500,
            5400,
            6300,
            7200,
            9000,
            10800,
            12600,
            14400,
            16200,
            18000,
            19800,
            21600,
        ],
    )
    power_curves.add_argument(
        "--start-date", help="Custom range start date (YYYY-MM-DD)"
    )
    power_curves.add_argument(
        "--end-date", help="Optional reference end date (YYYY-MM-DD)"
    )
    load = commands.add_parser("load")
    load.add_argument("--start", required=True)
    load.add_argument("--end", required=True)
    load.add_argument(
        "--modality",
        required=True,
        help="Exact modality; unknown is an explicitly unclassified group",
    )
    load.add_argument("--basis", choices=["time", "sessions", "load"], default="time")
    load.add_argument("--ctl-days", type=float)
    load.add_argument("--atl-days", type=float)
    load.add_argument(
        "--parameter-mode", choices=["historical", "current"], default="historical"
    )
    compare = commands.add_parser("compare")
    compare.add_argument("activity_ids", nargs="+")
    compare.add_argument("--allow-mixed", action="store_true")
    compare.add_argument(
        "--parameter-mode", choices=["historical", "current"], default="historical"
    )
    reprocess = commands.add_parser("reprocess")
    reprocess.add_argument(
        "--parameter-mode", choices=["historical", "current"], default="historical"
    )
    reprocess.add_argument(
        "--metric", default="all", help="Target metric name or 'all'"
    )
    reprocess.add_argument(
        "--all",
        action="store_true",
        help="Reprocess all activities (default behavior)",
    )
    reprocess.add_argument(
        "--from",
        dest="from_date",
        help="Filter activities starting on or after YYYY-MM-DD",
    )
    params = commands.add_parser("parameters").add_subparsers(
        dest="action", required=True
    )
    params.add_parser("list")
    params.add_parser("add").add_argument(
        "file", type=Path, help="JSON AthleteParameters; append-only"
    )
    commands.add_parser("status")
    commands.add_parser("dashboard").add_argument("--port", type=int, default=8501)
    commands.add_parser("api").add_argument("--port", type=int, default=8000)
    return cli


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "dashboard":
            return subprocess.call(
                [
                    sys.executable,
                    "-m",
                    "streamlit",
                    "run",
                    str(Path(__file__).with_name("dashboard.py")),
                    "--server.address=127.0.0.1",
                    f"--server.port={args.port}",
                    "--browser.gatherUsageStats=false",
                    "--",
                    "--data-dir",
                    str(Path(args.data_dir).resolve()),
                ]
            )
        if args.command == "api":
            import uvicorn

            from cycling.api import create_app

            uvicorn.run(create_app(args.data_dir), host="127.0.0.1", port=args.port)
            return 0
        with Store(args.data_dir) as store:
            service = CyclingService(store)
            command = args.command
            values = {
                k: v
                for k, v in vars(args).items()
                if k not in {"command", "action", "data_dir", "new_only", "all"}
            }
            if command == "ingest":
                result = ingest(store, args.source, args.force)
            elif command == "activities":
                result = service.list_activities()
                if args.action == "list":
                    acts = result.data.get("activities", [])
                    if args.modality and args.modality != "all":
                        acts = [a for a in acts if a.get("modality") == args.modality]
                    if args.limit is not None:
                        if args.limit < 0:
                            raise ValueError("Limit must be a non-negative integer")
                        acts = acts[: args.limit]
                    result.data["activities"] = acts
            elif command == "activity":
                if args.action == "delete":
                    result = store.delete_activity(args.activity_id)
                elif args.action == "set-context":
                    result = service.set_context(
                        args.activity_id,
                        ActivityContext(rpe=args.rpe, modality=args.modality),
                    )
                else:
                    request = ActivityRequest(**values)
                    result = (
                        service.analyze_activity(request)
                        if args.action == "analyze"
                        else service.get_activity(request)
                    )
            elif command == "power-curve":
                raw_args = sys.argv[1:] if argv is None else argv
                if args.activity_id:
                    if any(
                        opt in raw_args
                        for opt in (
                            "--period",
                            "--modality",
                            "--start-date",
                            "--end-date",
                        )
                    ):
                        raise ValueError(
                            "Ambiguous invocation: activity_id cannot be combined with period options (--period, --modality, --start-date, --end-date)"
                        )
                    result = service.power_curve(
                        CurveRequest(
                            activity_id=args.activity_id,
                            durations=args.durations,
                            parameter_mode=args.parameter_mode,
                        )
                    )
                else:
                    start_dt = None
                    if args.start_date:
                        start_dt = date.fromisoformat(args.start_date)
                    end_dt = None
                    if args.end_date:
                        end_dt = date.fromisoformat(args.end_date)
                    result = service.period_power_curve(
                        PeriodPowerCurveRequest(
                            period=args.period,
                            modality=args.modality,
                            durations=args.durations,
                            start_date=start_dt,
                            end_date=end_dt,
                        )
                    )
            elif command == "power-curves":
                start_dt = None
                if args.start_date:
                    start_dt = date.fromisoformat(args.start_date)
                end_dt = None
                if args.end_date:
                    end_dt = date.fromisoformat(args.end_date)
                result = service.period_power_curve(
                    PeriodPowerCurveRequest(
                        period=args.period,
                        modality=args.modality,
                        durations=args.durations,
                        start_date=start_dt,
                        end_date=end_dt,
                    )
                )
            elif command == "durability":
                clean_values = {k: v for k, v in values.items() if v is not None}
                result = service.durability(DurabilityRequest(**clean_values))
            elif command in {"drift", "thresholds"}:
                result = getattr(service, command)(ActivityRequest(**values))
            elif command == "stream":
                result = service.stream(StreamRequest(**values))
            elif command == "load":
                date.fromisoformat(args.start)
                date.fromisoformat(args.end)
                result = service.load(LoadRequest(**values))
            elif command == "compare":
                result = service.compare(ComparisonRequest(**values))
            elif command == "parameters":
                result = (
                    service.add_parameters(
                        AthleteParameters.model_validate_json(args.file.read_text())
                    )
                    if args.action == "add"
                    else service.list_parameters()
                )
            elif command == "reprocess":
                completed, errors = [], []
                activities = store.activities()
                if args.from_date:
                    date.fromisoformat(args.from_date)
                    activities = [
                        a
                        for a in activities
                        if a.get("start_time", "")[:10] >= args.from_date
                    ]
                for activity in activities:
                    try:
                        service.analyze_activity(
                            ActivityRequest(
                                activity_id=activity["id"],
                                parameter_mode=args.parameter_mode,
                            ),
                            force=True,
                        )
                        completed.append(activity["id"])
                    except Exception as exc:
                        errors.append(
                            {"activity_id": activity["id"], "error": str(exc)}
                        )
                result = {
                    "processed": len(completed),
                    "metric": args.metric,
                    "from_date": args.from_date,
                    "errors": errors,
                }
            else:
                result = service.status()
            output = (
                result.model_dump(mode="json")
                if hasattr(result, "model_dump")
                else result
            )
            print(json.dumps(output, indent=2, default=str, allow_nan=False))
            return 1 if output.get("errors") else 0
    except (ValueError, KeyError, OSError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
