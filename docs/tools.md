# Semantic Local Tools & API Interface

## Principles & Design

The analytical surface is exposed via a semantic Python service facade (`cycling.service.CyclingService`). Direct, raw SQL querying from external callers or presentation layers is prohibited.

- **Validated Contracts:** All tool invocations use strongly typed Pydantic request models (`cycling.models`) and return structured response envelopes (`ToolResult`) containing operation, algorithm version, parameter selection provenance, computation timestamp, and data.
- **Error Transparency:** Errors differentiate between missing resources (HTTP 404 `KeyError`), validation failures (HTTP 422 `ValueError`), missing sample files (HTTP 409 `FileNotFoundError`), and CLI execution errors (`{"error": "..."}` with exit codes 1 or 2).
- **Bound & Safety Enforcements:** Input parameters are strictly bounded (e.g., non-negative start/end seconds, maximum downsampling point caps, valid date ranges, local loopback IP binding).
- **Recorded Lap/Interval Evidence:** Devices recording manual or automatic lap markers have their structured lap summary metadata parsed into the `activity_laps` catalog table (`lap_index`, `start_time`, `end_time`, `duration_s`, `distance_m`, `avg_power_w`, `max_power_w`, `avg_hr_bpm`, `max_hr_bpm`, `avg_cadence_rpm`, `max_cadence_rpm`) for downstream evidence tracking.

---

## Service Facade API (`CyclingService`)

```python
class CyclingService:
    def list_activities(self) -> ToolResult: ...
    def get_activity(self, request: ActivityRequest) -> ToolResult: ...
    def analyze_activity(self, request: ActivityRequest, force: bool = False) -> ToolResult: ...
    def power_curve(self, request: CurveRequest) -> ToolResult: ...
    def period_power_curve(self, request: PeriodPowerCurveRequest) -> ToolResult: ...
    def durability(self, request: DurabilityRequest) -> ToolResult: ...
    def drift(self, request: ActivityRequest) -> ToolResult: ...
    def thresholds(self, request: ActivityRequest) -> ToolResult: ...
    def load(self, request: LoadRequest) -> ToolResult: ...
    def compare(self, request: ComparisonRequest) -> ToolResult: ...
    def stream(self, request: StreamRequest) -> ToolResult: ...
    def set_context(self, activity_id: str, context: ActivityContext) -> ToolResult: ...
    def add_parameters(self, parameters: AthleteParameters) -> ToolResult: ...
    def list_parameters(self) -> ToolResult: ...
    def status(self) -> ToolResult: ...
```

---

## Contract Schemas & Examples

### 1. Common Response Envelope Schema (`ToolResult`)
All successful API and service responses follow the uniform `ToolResult` envelope:

```json
{
  "operation": "analyze_activity",
  "algorithm_version": "mvp-1",
  "parameter_id": "7f8e9d0c1b2a3f4e5d6c7b8a9f0e1d2c",
  "parameter_mode": "historical",
  "computed_at": "2026-09-13T14:30:00.000000+00:00",
  "data": { ... }
}
```

### 2. Error Response Schemas
- **HTTP REST API Exceptions:**
  ```json
  {
    "detail": "Activity not found: 9f8e7d6c5b4a3f2e1d0c9b8a7f6e5d4c3b2a1f0e"
  }
  ```
- **CLI Exception Output (stderr):**
  ```json
  {
    "error": "end_s must not precede start_s"
  }
  ```

### 3. Analytics Request & Response Example (`analyze_activity`)

#### Request Schema (`ActivityRequest`):
```json
{
  "activity_id": "9f8e7d6c5b4a3f2e1d0c9b8a7f6e5d4c3b2a1f0e",
  "parameter_mode": "historical",
  "rpe": 7.5
}
```

#### Response Payload (`ToolResult`):
```json
{
  "operation": "analyze_activity",
  "algorithm_version": "mvp-1",
  "parameter_id": "7f8e9d0c1b2a3f4e5d6c7b8a9f0e1d2c",
  "parameter_mode": "historical",
  "computed_at": "2026-09-13T15:00:00.000000+00:00",
  "data": {
    "activity": {
      "id": "9f8e7d6c5b4a3f2e1d0c9b8a7f6e5d4c3b2a1f0e",
      "source_hash": "9f8e7d6c5b4a3f2e1d0c9b8a7f6e5d4c3b2a1f0e",
      "strava_id_hint": "1234567890",
      "start_time": "2026-04-12T09:00:00Z",
      "duration_s": 5400,
      "modality": "mtb",
      "quality_flags": [],
      "rpe": 7.5,
      "sample_path": "samples/2026/04/9f8e7d6c5b4a3f2e1d0c9b8a7f6e5d4c3b2a1f0e.parquet",
      "normalizer_version": "1",
      "ingested_at": "2026-09-13T12:00:00.000000+00:00"
    },
    "declared_parameters": {
      "effective_date": "2026-01-01",
      "ftp_w": 285.0,
      "lthr_bpm": 157.0,
      "max_hr_bpm": 181.0,
      "weight_kg": 75.0,
      "power_zone_fractions": [0.55, 0.75, 0.9, 1.05, 1.2],
      "hr_zone_bounds": [127.0, 141.0, 147.0, 158.0],
      "power_three_zone_fractions": [0.8, 1.0],
      "hr_three_zone_fractions": [0.87, 1.0],
      "ctl_days": 42.0,
      "atl_days": 7.0,
      "notes": "Profile defaults"
    },
    "metrics": {
      "elapsed_seconds": 5400,
      "active_seconds": 5120,
      "quality": {
        "flags": [],
        "power_coverage_pct": 98.5,
        "hr_coverage_pct": 99.2
      },
      "work_kj": 1205.2,
      "average_power_w": 235.4,
      "normalized_power_w": 262.1,
      "intensity_factor": 0.9196,
      "load": {
        "value": 120.4,
        "source": "power"
      },
      "zones": {
        "three_zone": {
          "basis": "power",
          "seconds": [3600, 1200, 320],
          "unknown_seconds": 0
        }
      }
    }
  }
}
```

---

## Input Validation & Rate/Bound Limits

| Parameter | Type / Constraint | Limits / Bounds | Validation Error |
|---|---|---|---|
| `activity_id` | String | $1 \le \text{length} \le 128$ | `ValueError` / HTTP 422 |
| `parameter_mode` | Literal String | `"historical"` or `"current"` (default `"historical"`) | `ValueError` / HTTP 422 |
| `max_points` | Integer | $2 \le N \le 20,000$ (default 2000) | `ValueError` / HTTP 422 |
| `start_s`, `end_s` | Integer | $\ge 0$; `end_s >= start_s` | `ValueError` / HTTP 422 |
| `durations` | Array of Integers | $1 \le \text{length} \le 30$; $1 \le d \le 86,400\,\text{s}$ | `ValueError` / HTTP 422 |
| `period` | Literal String | `"30d"`, `"90d"`, `"365d"`, or `"all"` (default `"all"`) | `ValueError` / HTTP 422 |
| `modality` | Literal String | `"indoor"`, `"road"`, `"mtb"`, `"gravel"`, `"unknown"`, `"all"` | `ValueError` / HTTP 422 |
| `bucket_kj` | Array of Floats | $1 \le \text{length} \le 20$; starts at 0, strictly increasing | `ValueError` / HTTP 422 |
| `rpe` | Float | $0.0 \le \text{RPE} \le 10.0$ | `ValueError` / HTTP 422 |
| `start`, `end` | Date | `start <= end`, max range 3660 days (~10 years) | `ValueError` / HTTP 422 |
| `ftp_w` | Float | $0.0 < \text{FTP} \le 1000.0\,\text{W}$ | `ValueError` / HTTP 422 |
| `lthr_bpm`, `max_hr_bpm` | Float | $0.0 < \text{LTHR} \le \text{Max HR} \le 250.0\,\text{BPM}$ | `ValueError` / HTTP 422 |

---

## Adapter Interfaces

### 1. Command Line Interface (Standard `argparse`)
Executed via `python -m cycling <command>` (or `uv run --group local python -m cycling <command>`):

```bash
# Global option
python -m cycling --data-dir .cycling <command>

# Ingest new files or force full reparse
python -m cycling ingest [source] [--force] [--new-only]

# Activities list with modality and limit filtering
python -m cycling activities list [--limit LIMIT] [--modality indoor|road|mtb|gravel|unknown|all]

# Activity retrieval and analysis
python -m cycling activity analyze <activity_id> [--parameter-mode historical|current] [--rpe RPE]
python -m cycling activity get <activity_id> [--parameter-mode historical|current] [--rpe RPE]
python -m cycling activity set-context <activity_id> [--rpe RPE] [--modality road|mtb|gravel|indoor|unknown]
python -m cycling stream <activity_id> [--parameter-mode historical|current] [--start-s S] [--end-s E] [--max-points N]

# Power curves (supports single activity or period mode; activity_id and period options are mutually exclusive)
python -m cycling power-curve [<activity_id>] [--parameter-mode historical|current] [--durations D ...] [--period 30d|90d|365d|all] [--modality indoor|road|mtb|gravel|unknown|all] [--end-date YYYY-MM-DD]
python -m cycling power-curves [--period 30d|90d|365d|all] [--modality indoor|road|mtb|gravel|unknown|all] [--durations D ...] [--end-date YYYY-MM-DD]

# Analytics & Training Load
python -m cycling durability <activity_id> [--parameter-mode historical|current] [--durations D ...] [--bucket-kj B ...]
python -m cycling drift <activity_id> [--parameter-mode historical|current]
python -m cycling thresholds <activity_id> [--parameter-mode historical|current]
python -m cycling load --start YYYY-MM-DD --end YYYY-MM-DD --modality MODALITY [--basis time|sessions|load] [--ctl-days FLOAT] [--atl-days FLOAT] [--parameter-mode historical|current]
python -m cycling compare <activity_id1> <activity_id2> ... [--allow-mixed] [--parameter-mode historical|current]

# Maintenance & Parameters
python -m cycling reprocess [--parameter-mode historical|current] [--metric METRIC] [--all] [--from YYYY-MM-DD]
python -m cycling parameters list
python -m cycling parameters add <file.json>
python -m cycling status

# Web Adapters
python -m cycling dashboard [--port 8501]
python -m cycling api [--port 8000]
```

### 2. HTTP REST API (`api.py`)
FastAPI application bound strictly to loopback `127.0.0.1`:

- `GET /activities` - Returns all activities in catalog wrapped in `ToolResult`.
- `GET /status` - System status and audit metrics.
- `GET /health` - Health probe returning system status.
- `POST /activity` - Fetch metadata and declared parameters for activity (`ActivityRequest`).
- `POST /activity/analyze` - Calculate and cache activity metrics snapshot (`ActivityRequest`).
- `POST /stream` - Retrieve downsampled 1 Hz time-series stream (`StreamRequest`).
- `POST /power-curve` - Calculate best observed power curve for a single activity (`CurveRequest`).
- `POST /power-curves` - Calculate period power curve across activities (`PeriodPowerCurveRequest`).
- `POST /durability` - Calculate work-bucketed durability metrics (`DurabilityRequest`).
- `POST /drift` - Calculate steady-state aerobic drift (`ActivityRequest`).
- `POST /thresholds` - Calculate 20-min peak observed threshold estimate (`ActivityRequest`).
- `POST /load` - Compute multi-activity CTL/ATL/TSB and zone distributions (`LoadRequest`).
- `POST /compare` - Compare metrics across multiple activities (`ComparisonRequest`).
- `POST /activity/context` - Append user-declared context (`SetContextRequest`).
- `GET /parameters` - List athlete parameter revision history.
- `POST /parameters` - Append new athlete parameters entry (`AthleteParameters`).

### 3. Interactive Dashboard (`dashboard.py`)
Streamlit local user interface providing visualization views (all underlying calculations originate strictly from `CyclingService`):

- **Overview:** Displays system status envelope, recent 10 activities, and load summary metrics (CTL/TSB) per modality over the past 90 days.
- **Activity:** Deep dive for selected activity including metric snapshot JSON, interactive multi-metric stream chart (power, HR, cadence), and 20-minute threshold estimate.
- **Power curve:** Dual-mode power curve visualization:
  - *Period mode:* Aggregates best observed Mean Maximal Power across activities for selected period (`30d`, `90d`, `365d`, `all`) and modality (`all` or specific) plotted on a log duration scale.
  - *Single Activity mode:* Plotted power duration curve for individual activity.
  - *Power Skills:* Overlays all-time best and selected-scope watts for Strava's 12
    Sprinting, Attacking, and Climbing intervals. Historical maxima use the selected
    modality; personalized milestone levels are not reproduced because thresholds are
    not published.
- **Durability:** Work-bucketed MMP decay metrics across progressive kJ expenditure ranges, displaying clear warning banners when power sensor coverage is below 95% or missing (no-power rides).
- **Load:** Single-modality CTL/ATL/TSB trend chart over custom date ranges with 3-zone polarization distribution (time, session count, or load basis).
- **Calendar:** Modality-filterable table of recorded activities.
