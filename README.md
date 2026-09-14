# Local cycling analytics

Analyze original FIT/TCX files locally. No Strava API, browser or account is needed
after download. Python 3.13 and `uv` are required. Existing
[downloader usage](strava_fetcher/README.md) is unchanged.

## Quick start

```bash
uv sync
uv run python -m cycling ingest downloads/strava --new-only
uv run python -m cycling activities list --limit 10 --modality all
uv run python -m cycling dashboard
```

Dashboard: <http://127.0.0.1:8501>. Stop it before running another database writer.
Defaults: ignored `.cycling/catalog.duckdb` and immutable normalized Parquet under
`.cycling/samples/`. Set another location with global `--data-dir PATH`.
Original downloads, downloader state and browser profiles are never modified.

To run the complete download → ingest → dashboard flow:

```bash
./scripts/run_pipeline.sh
```

Pass any downloader option, for example `./scripts/run_pipeline.sh --limit 10`.
By default the downloader opens its persistent Chromium profile and asks for a
manual Strava login when needed. For an already authenticated regular Chrome:

```bash
./scripts/run_pipeline.sh --cdp-url http://127.0.0.1:9222
```

Use `STRAVA_OUTPUT_DIR` or `CYCLING_DATA_DIR` to override the default directories.

## CLI examples

Choose an ID from `activities list`, or select the latest activity:

```bash
ID=$(uv run python -m cycling activities list --limit 1 | uv run python -c 'import json,sys; print(json.load(sys.stdin)["data"]["activities"][0]["id"])')
uv run python -m cycling activity analyze "$ID"
uv run python -m cycling activity analyze "$ID" --parameter-mode current
uv run python -m cycling stream "$ID" --start-s 300 --end-s 900 --max-points 200
uv run python -m cycling power-curve "$ID" --durations 5 30 60 300 1200
uv run python -m cycling power-curve --period 90d --modality mtb
uv run python -m cycling power-curves --period 90d --modality mtb
uv run python -m cycling durability "$ID" --bucket-kj 0 500 1000 1500
uv run python -m cycling drift "$ID"
uv run python -m cycling thresholds "$ID"
uv run python -m cycling load --start 2026-01-01 --end 2026-09-13 --modality unknown --basis time
uv run python -m cycling reprocess --metric all --from 2026-01-01
uv run python -m cycling status
```

`unknown` explicitly selects unclassified recordings, not a coherent cycling modality.
Correct context only when you know it; optionally record session RPE (0–10):

```bash
uv run python -m cycling activity set-context "$ID" --modality mtb --rpe 6
uv run python -m cycling load --start 2026-01-01 --end 2026-09-13 --modality mtb --basis sessions
```

Context revisions are append-only. RPE never adds to a power/HR load for the same
activity. `activity analyze --rpe 6` is a temporary what-if override instead.
Exit codes: 0 success, 1 per-file/reprocess failures, 2 invalid request.

### Refresh or remove catalog data

`reprocess` recomputes metric snapshots from normalized Parquet. It does not
reparse FIT/TCX files or remove activities from the catalog. When the original
files are available, force normalization first:

```bash
uv run python -m cycling --data-dir .cycling ingest downloads/strava --force
uv run python -m cycling --data-dir .cycling reprocess --metric all --from 2026-08-01
```

To remove one stale activity, find its catalog ID by source name and delete it:

```bash
ID=$(uv run python -m cycling --data-dir .cycling activities list |
  uv run python -c 'import json,sys; activities=json.load(sys.stdin)["data"]["activities"]; print(next(a["id"] for a in activities if "20146933314" in a.get("source_name", "")))')
uv run python -m cycling --data-dir .cycling activity delete "$ID"
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
uv run python -m cycling parameters add settings.json
uv run python -m cycling parameters list
uv run python -m cycling reprocess --parameter-mode historical
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
uv run python -m cycling api
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
uv run python -m unittest discover -s tests -v
uv run python -m strava_fetcher.test_download
```

Tests generate synthetic FIT/TCX; private downloads and browsers are not required.
Current dependency resolution emits a harmless Starlette warning about its httpx
test adapter; request tests still run normally.

Design docs: [architecture](docs/architecture.md), [data model](docs/data-model.md),
[analytics](docs/analytics.md), [tools](docs/tools.md), [dashboard](docs/dashboard.md),
[implementation plan](docs/implementation-plan.md).

MVP limits: one local writer process, UTC day grouping, no multisession/multisport
file merging, no HR durability proxy, no automatic threshold fitting, no cloud sync.
