# Multi-user Vercel migration

Replace the local, single-athlete Streamlit/DuckDB dashboard with an invite-only
Next.js application backed by the existing Python analytics, PostgreSQL, and
private Vercel Blob storage. This file is the implementation tracker; update
status and the log as milestones change.

## Agreed decisions

| Area | Decision |
|---|---|
| Frontend | Next.js on Vercel; replace Streamlit views |
| Login | Google OAuth; invite-only; no app passwords |
| Data access | Each account sees only its own athlete data; no friend sharing in MVP |
| API and analysis | Keep Python/FastAPI and the existing deterministic analytics |
| Local database | PostgreSQL in Docker Compose |
| Production database | Neon through Vercel Marketplace |
| Files | Private Vercel Blob for original FIT/TCX and normalized Parquet |

## Milestones

### 0. Baseline and deployment feasibility — in progress

- [x] Create branch `feat/multiuser-vercel-platform`.
- [x] Confirm Vercel CLI authentication.
- [x] Record baseline tests and runtime versions.
- [x] Build the Next.js frontend locally.
- [x] Prove local Vercel Services routing for Next.js + FastAPI.
- [x] Pass local and remote Vercel builds with the isolated Python service root.
- [x] Deploy protected production and preview builds; smoke-test remote routing/auth denial.
- [ ] Measure representative FIT/TCX ingestion, package size, and function duration.
- [x] Create and link the Vercel project.
- [x] Provision free Neon development/preview database in `gru1` and private Blob in `gru1`.
- [x] Connect Neon to production and create a separate private production Blob store.
- [x] Apply migrations 001–003 to the production Neon branch using the direct connection.

**Gate:** Do not commit to synchronous serverless ingestion until workload fits
Vercel Function limits. If not, move ingestion to a durable worker.

### 1. Local app skeleton, identity, and tenancy — in progress

- [x] Add Next.js app and local PostgreSQL Compose service.
- [x] Add Google OAuth/Auth.js invite gate, persist the verified Google subject, and verify production owner login.
- [x] Define versioned PostgreSQL migrations and local migration/invitation commands.
- [x] Add an owner-scoped PostgreSQL adapter and signed Next.js-to-FastAPI identity validation.
- [x] Prove isolation using two users against local PostgreSQL integration tests.
- [x] Verify production Google Login with the invited owner account.

### 2. PostgreSQL and private object storage — in progress

- [x] Add a PostgreSQL adapter preserving `CyclingService` while keeping the legacy DuckDB CLI operational during migration.
- [x] Add local private-object storage and a Vercel Blob SDK adapter for original files and normalized Parquet.
- [x] Scope records, keys, deduplication, status and metric caches by authenticated owner.
- [x] Preserve FIT/TCX parsing, normalization, null sensors, quality flags and provenance in upload processing.
- [x] Verify live private Vercel Blob upload/read/delete in development and production using OIDC; anonymous reads are denied.
- [ ] Verify upload callback retries and orphan cleanup end-to-end.

### 3. Secure vertical slice — in progress

- [x] Add invite-gated Google login, athlete settings, FIT/TCX upload, ingestion, and owner-scoped activity analysis UI.
- [x] Add analysis and stream API routes and activity detail charts.
- [x] Configure Google OAuth variables and invite the owner account.
- [x] Verify real owner browser login and authorized dashboard access.
- [ ] Verify browser upload callback and authorization with a FIT/TCX file.
- [ ] Verify oversized, invalid, failed, retried, and cross-user access cases through Vercel.

### 4. Dashboard and API parity — in progress

- [x] Expose authenticated API operations for progress, weekly training and FTP mismatch.
- [x] Add initial Next.js Overview, Progress, FTP Calibration, Activity, Power Curve,
  HR Distribution, Durability, Load and Calendar views.
- [x] Expose Upload FIT/TCX from the dashboard navigation and Overview.
- [ ] Preserve existing metric semantics, data-quality warnings, filters, and parameter provenance.
- [ ] Complete side-by-side parity checks and exclude Streamlit from the Vercel runtime bundle; keep the legacy local dashboard temporarily.

### 5. Existing-data migration and release — in progress

- [x] Add a dry-run-first migration tool for profiles, activities, contexts, laps, and optional originals.
- [x] Back up the local `.cycling` catalog/samples; raw source files remain untouched locally.
- [x] Add and integration-test a dry-run-first legacy catalog importer with optional original FIT/TCX upload.
- [x] Dry-run and reconcile 179 activities, 3 parameter sets, 83 context revisions and all normalized samples.
- [x] Recompute derived metric snapshots from normalized Parquet; legacy caches are not trusted across storage revisions.
- [x] Verify migration rerun idempotency and exact sample/metric parity on 3 representative rides.
- [x] Deploy a protected Vercel preview against non-production Neon/Blob resources.
- [x] Deploy production and preview, then disable Vercel team-only SSO after owner login and owner-scoped checks passed.
- [ ] Verify browser upload, friend invitations, workload and recovery before release completion.

## Security and deployment gates

- Production and preview must use distinct Neon branches and Blob stores; previews must
  not inherit private production activity data.
- Browser uploads go directly to Blob only after a server-side authenticated,
  owner-scoped upload token is issued.
- Disabled accounts are checked again before Blob upload-token issuance and every API request.
- FIT/TCX and gzip-expanded inputs are bounded before parsing.
- Next production builds use Webpack because local Next 16 Turbopack builds repeatedly
  canceled server-module tracing; local and Vercel Webpack builds pass.
- Filesystem writes in Vercel Functions are temporary only; no catalog or Parquet path
  may depend on persistent local disk.
- FastAPI validates a short-lived signed application identity from the Next.js server
  boundary; CORS and hidden UI controls are not authorization.
- Public health checks return liveness only; tenant status requires authentication.
- Apply migrations with Neon’s direct connection; use its pooled connection for
  serverless runtime requests.
- Legacy import defaults to dry-run; upload original raw files only with `--include-originals`.
- Never commit OAuth secrets, Neon credentials, Blob tokens, or Vercel environment files.
- Vercel source upload must exclude local `.cycling`, raw downloads, browser profiles,
  training/nutrition notes, and agent instruction files; verify with `vercel deploy --dry --json`.
- Vercel Deployment Protection is disabled so invited friends can reach Google Login;
  every data/API route still requires an active invited app account.

## External setup still needed

- Add friend emails to `cycling_invites` and to Google OAuth Test Users if the consent app remains in Testing.
- Exercise one browser FIT/TCX upload and verify its activity is visible only to the owner.

## Performance follow-up — local only, rollout awaiting review

**Selected slice:** persist per-activity MMP watts in the existing owner-scoped
`cycling_metrics` table/index from migration 002. Single and period power curves
share these snapshots. No new migration, dependency, frontend response cache,
deployment, or production database operation.

Production timings after an Overview refresh identified period power curves
(`32,706 ms`) and weekly HR training (`22,588 ms`) as the dominant paths, both
with sequential private sample downloads. Load (`1,615–2,617 ms`) already uses
analysis snapshots; warm activities (`83–85 ms`) are not the target. These are
baseline observations, not predicted post-change timings.

### Correctness and invalidation

- Cache identity: verified store owner + activity ID + `activity_power_curve:v1`
  + analytics algorithm version + catalog normalizer version + immutable sample
  path + ingestion revision timestamp + exact sorted validated duration list.
  Duplicates remain part of identity; response order remains the request order.
- PostgreSQL reads the sample revision pinned by the authorized catalog read.
  The cached snapshot records that analyzed revision even if reingest publishes
  a newer revision during calculation. DuckDB needs the ingestion timestamp
  because it overwrites stable paths; its existing single-process assumption stays.
- Date/modality selection runs before lookup, including the newest selected
  activity anchor for `90d`, inclusive boundaries, explicit end dates, and ties.
  Catalog/context and response metadata are read fresh. MMP itself does not
  depend on FTP/HR settings, RPE or parameter mode.
- Null and zero watts are reusable results. Missing/failed sample reads are not
  persisted; local file presence is checked before accepting a hit. Authentication
  and active-account checks still precede every lookup;
  neither bearer tokens nor emails identify cache entries.

### Proposed rollout (not executed)

1. Review this local diff. No migration 004 is needed: verify migrations 001–003
   and the migration-002 metrics lookup index are present using read-only checks.
   If an environment lacks those prerequisites, review/apply its existing ordered
   migrations separately with a direct connection before deploying code.
2. Test an isolated preview with non-production Neon/Blob resources. Compare
   exact cold/warm outputs, tenant isolation and revocation; measure endpoint
   timings and sample-read counts across new requests/function instances.
3. Only after approval, deploy code. Existing users warm lazily on their first
   selected activity/duration request; no backfill job or upload change. First
   misses still download samples sequentially and incur cache read/write overhead.
   New sample/normalizer/algorithm revisions or duration lists create new misses.
4. Measure repeated Overview refreshes; do not claim production speedup from
   local tests. Monitor warm SQL latency and append-only snapshot growth/concurrent
   duplicate misses. Rollback is code-only; older code ignores namespaced snapshots.

**Remaining bottleneck:** weekly HR aggregation is unchanged. Cold curves and
one SQL lookup per selected activity also remain. Revisit per-activity HR summaries
or batched cache reads only with the next measurements; no Redis or instance-local
cache introduced.

**Local verification:** all 177 backend tests passed with PostgreSQL from Docker
Compose (each integration test uses a temporary schema). Tests prove zero warm
sample reads across new stores, exact watts/null/order parity, owner isolation,
account revocation, version invalidation and concurrent reingest provenance.
Ruff passes for `cycling tests scripts`, changed Python files are formatted, and
`git diff --check` passes. Repo-wide Ruff still reports pre-existing issues in
`strava_fetcher/download.py` (imports/format) and `docs/tools.md` (format), untouched.

## Progress log

| Date | Update |
|---|---|
| 2026-10-01 | Created `feat/multiuser-vercel-platform`; baseline 74 unittest tests, Ruff, format and downloader checks passed. Vercel CLI required uv ≥0.9.25, so local uv was upgraded to 0.12.21. |
| 2026-10-01 | Implemented Next.js/Auth.js, owner-scoped PostgresStore and FastAPI, private Blob uploads, local Docker Postgres, migrations/invite tools, and first-cut versions of all nine dashboard views. Local Vercel Services routing and builds passed. |
| 2026-10-01 | Created Vercel project `la-grupeta/cycling-analytics`; provisioned separate Neon Free resources and private Blob stores for dev/preview and production in `gru1`. Migrations 001–003 applied to both databases. |
| 2026-10-01 | `.vercelignore` plus `scripts/deploy_vercel.py` now exclude personal catalogs, browser profiles, raw downloads, training/nutrition notes and agent files. Final deployment dry-run is ~1.26 MB with zero private paths. |
| 2026-10-02 | Invited `adenevreze@gmail.com`; owner completed Google Login and reached the dashboard. Disabled Vercel team-only SSO after app-level auth and tenant isolation were checked. Production and Preview use `https://cycling-analytics-rose.vercel.app` and `https://cycling-analytics-jr0on88n9-la-grupeta.vercel.app`. |
| 2026-10-02 | Backed up local `.cycling` to `/tmp/opencode/strava-analysis-cycling-legacy-20261002.tar.gz`; migrated 179 activities, 3 parameter sets, 83 context revisions, normalized Parquet and 179 cached metrics to production Neon/private Blob. Raw FIT/TCX originals intentionally remain local. |
| 2026-10-02 | Exact sample/metric parity passed for 3 representative rides. Private Blob OIDC upload/read/delete passed in dev and production; anonymous reads denied. Parser input/decompression limit is 64 MiB; inactive users cannot obtain upload tokens or API access. |
| 2026-10-02 | Final validation: 75 legacy unittests, 29 PostgreSQL/API/analytics integration tests, Ruff, Next build/lint, local and remote Vercel builds. Remote health returns 200; unauthenticated tenant API returns 401. Browser upload callback, friends' invites, workload benchmark and full visual parity remain. |
| 2026-10-02 | Changed the Next build script to `next build --webpack` after repeatable local Turbopack trace cancellations; local Next build, lint and Vercel build now pass. |
| 2026-10-02 | Added visible Upload FIT/TCX navigation and Overview links after owner couldn't find the upload page; production redeployed. |
| 2026-10-02 | Added FIT MIME types `application/fits` and `application/vnd.ant.fit` after the browser upload returned a Vercel Blob content-type mismatch. |
| 2026-10-02 | Added an accessible dashboard loading status and CSS skeleton; page transitions previously had no `loading.tsx` fallback while server-rendered API requests completed. |
| 2026-10-02 | Added per-endpoint timing logs, reduced activity catalog hydration from 1+N SQL statements to one owner-scoped query, and streamed Overview panels independently. Persistent caching remains deferred until production timings identify the hot path. |
| 2026-10-02 | Production timings identified repeated sample reads in period curves and weekly HR training. Implemented revision/duration-aware per-activity MMP snapshots using the existing metrics table, with local correctness, isolation, revocation and reingest tests. No migration or production operation; rollout awaits review. Weekly caching remains deferred. |
