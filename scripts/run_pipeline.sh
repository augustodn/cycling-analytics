#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_DIR="${CYCLING_DATA_DIR:-.cycling}"
DOWNLOAD_DIR="${STRAVA_OUTPUT_DIR:-downloads/strava}"

cd "$ROOT_DIR"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    cat <<'EOF'
Usage: ./scripts/run_pipeline.sh [downloader options]

Runs the Strava downloader, ingests local FIT/TCX files, then starts the dashboard.
With no options, starts Chrome with CDP and downloads up to 10 activities.
Downloader options are forwarded to strava_fetcher/download.py as before.
EOF
    exit 0
fi

if [[ "$#" -eq 0 ]]; then
    if ! command -v google-chrome-stable >/dev/null 2>&1; then
        printf '%s\n' 'google-chrome-stable was not found in PATH.' >&2
        exit 1
    fi

    printf '%s\n' 'Starting Google Chrome with remote debugging...'
    google-chrome-stable \
        --remote-debugging-port=9222 \
        --user-data-dir="$PWD/.strava-chrome" &

    for _ in {1..30}; do
        if (exec 3<>/dev/tcp/127.0.0.1/9222) 2>/dev/null; then
            break
        fi
        sleep 1
    done
    if ! (exec 3<>/dev/tcp/127.0.0.1/9222) 2>/dev/null; then
        printf '%s\n' 'Chrome did not start its remote debugging server on port 9222.' >&2
        exit 1
    fi

    set -- --cdp-url http://127.0.0.1:9222 --limit 10
fi

printf '%s\n' '1/3 Downloading original activity files...'
uv run --group local python strava_fetcher/download.py --output "$DOWNLOAD_DIR" "$@"

printf '%s\n' '2/3 Ingesting FIT/TCX files...'
uv run --group local python -m cycling --data-dir "$DATA_DIR" ingest "$DOWNLOAD_DIR" --new-only

printf '%s\n' '3/3 Starting dashboard...'
exec uv run --group local python -m cycling --data-dir "$DATA_DIR" dashboard
