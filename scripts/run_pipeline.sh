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
Downloader options are forwarded to strava_fetcher/download.py.
EOF
    exit 0
fi

printf '%s\n' '1/3 Downloading original activity files...'
uv run python strava_fetcher/download.py --output "$DOWNLOAD_DIR" "$@"

printf '%s\n' '2/3 Ingesting FIT/TCX files...'
uv run python -m cycling --data-dir "$DATA_DIR" ingest "$DOWNLOAD_DIR" --new-only

printf '%s\n' '3/3 Starting dashboard...'
exec uv run python -m cycling --data-dir "$DATA_DIR" dashboard
