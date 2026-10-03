# Local cycling analytics

Analyze original FIT/TCX files locally. No Strava API, browser or account is needed
after download. Python 3.13 and `uv` are required. Existing
[downloader usage](strava_fetcher/README.md) is unchanged.

## Multi-user web app

The replacement web app lives in `frontend/` (Next.js/Auth.js) and uses the existing
Python analytics through `cycling/cloud_api.py`. Vercel Services deploys the frontend
and FastAPI backend under one project. Vercel's team-only SSO protection is disabled;
production is gated by Google OAuth, the invite list, and owner checks in FastAPI.
Never bypass or weaken those app-level checks.

Local setup uses the same PostgreSQL major version as Neon:

```bash
docker compose up -d --wait postgres
export DATABASE_URL='postgresql://cycling:local-only@localhost:5432/cycling'
uv run python -m scripts.migrate_database
uv run python -m scripts.invite_user friend@example.com
```

Create `frontend/.env.local` from `.env.example`, remove its `CYCLING_API_URL` line
(Vercel Services injects the local binding), then set a Google OAuth client ID and
secret, a random `AUTH_SECRET`, and the local `DATABASE_URL`.
Google's authorized callback for production is:

```text
https://cycling-analytics-rose.vercel.app/api/auth/callback/google
```

For local Google sign-in, also register
`http://localhost:3000/api/auth/callback/google` as an authorized redirect URI.

Start the local Vercel Services router with local-only credentials:

```bash
export INTERNAL_API_SECRET="$(openssl rand -hex 32)"
export CYCLING_LOCAL_OBJECTS=1
vercel dev -L
```

`CYCLING_LOCAL_OBJECTS=1` is rejected on Vercel Preview/Production. Local uploads use
the ignored `.cycling/objects/` directory; deployed uploads use private Blob. Add an
invited address locally with `scripts/invite_user.py`; revoke/reactivate with
`scripts/revoke_user.py` and `scripts/activate_user.py`. Friend access requires both
Google OAuth Test User membership (while consent is in Testing) and a matching app invite.

Deploy through `python scripts/deploy_vercel.py` rather than raw `vercel deploy`:
the script materializes the shared `cycling` source inside the isolated backend
service for the upload, then restores the local symlink. `vercel build` checks the
service build without uploading. `.vercelignore` excludes private local downloads,
browser profiles, catalogs, and personal training notes.

To repeat the legacy import for another account, back up its catalog, sign in once, and
run a dry-run before applying. Raw FIT/TCX originals are not uploaded unless
`--include-originals` is explicitly supplied:

```bash
uv run python scripts/migrate_legacy_catalog.py --user-email owner@example.com
uv run python scripts/migrate_legacy_catalog.py --user-email owner@example.com --include-originals
uv run python scripts/migrate_legacy_catalog.py --user-email owner@example.com --apply --include-originals --precompute-metrics
```

## Quick start



```bash
uv sync --group local
google-chrome-stable   --remote-debugging-port=9222   --user-data-dir="$PWD/.strava-chrome"
uv run --group local python -m cycling ingest downloads/strava --new-only
uv run --group local python -m cycling activities list --limit 10 --modality all
uv run --group local python -m cycling dashboard
```

Dashboard: <http://127.0.0.1:8501>. Stop it before running another database writer.
Defaults: ignored `.cycling/catalog.duckdb` and immutable normalized Parquet under
`.cycling/samples/`. Set another location with global `--data-dir PATH`.
Original downloads, downloader state and browser profiles are never modified.

To run the complete download → ingest → dashboard flow:

```bash
./scripts/run_pipeline.sh
```

With no arguments, the script launches Google Chrome with a persistent
`.strava-chrome` profile and downloads up to 10 activities through CDP. Log in to
Strava in that Chrome window if needed. Downloader arguments are still forwarded
unchanged, for example `./scripts/run_pipeline.sh --limit 10`. To attach to a
Chrome you launched separately:

```bash
./scripts/run_pipeline.sh --cdp-url http://127.0.0.1:9222 --limit 10
```

Use `STRAVA_OUTPUT_DIR` or `CYCLING_DATA_DIR` to override the default directories.

## CLI examples

Choose an ID from `activities list`, or select the latest activity:

```bash
ID=$(uv run --group local python -m cycling activities list --limit 1 | uv run --group local python -c 'import json,sys; print(json.load(sys.stdin)["data"]["activities"][0]["id"])')
uv run --group local python -m cycling activity analyze "$ID"
uv run --group local python -m cycling activity analyze "$ID" --parameter-mode current
uv run --group local python -m cycling stream "$ID" --start-s 300 --end-s 900 --max-points 200
uv run --group local python -m cycling power-curve "$ID" --durations 5 30 60 300 1200
uv run --group local python -m cycling power-curve --period 90d --modality mtb
uv run --group local python -m cycling power-curves --period 90d --modality mtb
uv run --group local python -m cycling durability "$ID" --bucket-kj 0 500 1000 1500
uv run --group local python -m cycling drift "$ID"
uv run --group local python -m cycling thresholds "$ID"
uv run --group local python -m cycling load --start 2026-01-01 --end 2026-09-13 --modality unknown --basis time
uv run --group local python -m cycling reprocess --metric all --from 2026-01-01
uv run --group local python -m cycling status
```

`unknown` explicitly selects unclassified recordings, not a coherent cycling modality.
Correct context only when you know it; optionally record session RPE (0–10):

```bash
uv run --group local python -m cycling activity set-context "$ID" --modality mtb --rpe 6
uv run --group local python -m cycling load --start 2026-01-01 --end 2026-09-13 --modality mtb --basis sessions
```

Context revisions are append-only. RPE never adds to a power/HR load for the same
activity. `activity analyze --rpe 6` is a temporary what-if override instead.
Exit codes: 0 success, 1 per-file/reprocess failures, 2 invalid request.

### Refresh or remove catalog data

`reprocess` recomputes metric snapshots from normalized Parquet. It does not
reparse FIT/TCX files or remove activities from the catalog. When the original
files are available, force normalization first:

```bash
uv run --group local python -m cycling --data-dir .cycling ingest downloads/strava --force
uv run --group local python -m cycling --data-dir .cycling reprocess --metric all --from 2026-08-01
```

To remove one stale activity, find its catalog ID by source name and delete it:

```bash
ID=$(uv run --group local python -m cycling --data-dir .cycling activities list |
  uv run --group local python -c 'import json,sys; activities=json.load(sys.stdin)["data"]["activities"]; print(next(a["id"] for a in activities if "20146933314" in a.get("source_name", "")))')
uv run --group local python -m cycling --data-dir .cycling activity delete "$ID"
```

Deletion removes the catalog row, derived metrics, context and normalized
Parquet. It never deletes original downloads. HR distribution is computed live;
`Unknown HR time` means the normalized activity contains missing or invalid HR,
and can only be repaired by re-ingesting an original file that contains HR.

## Declared athlete settings

Initial profile defaults: FTP 285 W, LTHR 155 bpm, max HR 181 bpm, weight 75 kg,
effective **2026-01-01**. These are declared assumptions, not historical measurements.
Historical mode selects settings effective on the activity's UTC date. Current mode
uses settings effective today. Earlier recordings have no threshold-based metrics
until you declare suitable settings. Estimates never change settings automatically.

Create a JSON file, for example `settings.json`:

```json
{"effective_date": "2026-09-01", "ftp_w": 295, "lthr_bpm": 155,
  "hr_load_factor": 0.692, "max_hr_bpm": 181, "weight_kg": 75,
  "notes": "Declared FTP update"}
```

```bash
uv run --group local python -m cycling parameters add settings.json
uv run --group local python -m cycling parameters list
uv run --group local python -m cycling reprocess --parameter-mode historical
```

Partial JSON uses profile defaults for omitted fields, **not** inheritance from the
previous version. Keep personal settings files outside Git. Zone boundaries and
42/7-day load time constants are configurable; `load --ctl-days 42 --atl-days 7`
explicitly controls aggregate recursion. Metric snapshots include settings ID,
algorithm version, selection mode and computation time.
Without CLI overrides, load uses the time constants declared at the query end date
(historical mode) or today (current mode), held fixed across the computed timeline.

## Optional HTTP tools

```bash
uv run --group local python -m cycling api
```

Open <http://127.0.0.1:8000/docs> for semantic request/response contracts. No arbitrary
SQL endpoint. API and dashboard have **no authentication**: keep them on loopback.
They expose private activity/GPS data and are not designed for public deployment.

## Quality and recovery

- Missing power stays null. MTB without power has HR/RPE fallback load, but **no
  fabricated watts, kJ buckets, or power durability**.
- NP uses complete contiguous 30-second rolling means only; short/gapped windows
  are excluded. Power curves are best observed efforts, not proven maximal ability.
- Durability compares observed powers after recorded work, with strict complete
  energy coverage; it is not a controlled test of physiological fatigue resistance.
- HR load is an approximation, not TRIMP. When `hr_load_factor` is declared, it
  scales the raw HR load against paired power-reference sessions. Mixing HR, power
  and RPE scales lowers
  longitudinal comparability. CTL/ATL initialize at zero; missing days are not proven rest.
- Strict drift detection often returns unavailable outdoors. Read coverage and flags.
- Idempotent ingestion deduplicates identical bytes, not independently encoded copies
  of the same ride. Different content hashes remain separate and can double-count load.
- Source catalog is separate from downloader progress. Read-only state audits expose
  download failures and missing originals; they do not repair or retry downloads.
- `ingest` retries errors and missing Parquet; `ingest --force` reparses all originals.
  `reprocess` recomputes metric snapshots without reparsing. Old Parquet publications
  remain retained; automatic garbage collection is intentionally absent.
- Back up originals and the entire `.cycling/` directory with all writers stopped.
  Atomic publication protects against partial files, not disk loss or absent backups.

## Verification and design

```bash
uv run ruff check cycling tests
uv run ruff format --check cycling tests
uv run --group local python -m unittest discover -s tests -v
uv run --group local python -m strava_fetcher.test_download
```

Tests generate synthetic FIT/TCX; private downloads and browsers are not required.
Current dependency resolution emits a harmless Starlette warning about its httpx
test adapter; request tests still run normally.

Design docs: [architecture](docs/architecture.md), [data model](docs/data-model.md),
[analytics](docs/analytics.md), [tools](docs/tools.md), [dashboard](docs/dashboard.md),
[implementation plan](docs/implementation-plan.md),
[multi-user Vercel migration](docs/multiuser-vercel-plan.md).

MVP limits: one local writer process, UTC day grouping, no multisession/multisport
file merging, no HR durability proxy, no automatic threshold fitting, no cloud sync.
