# Strava FIT downloader

Downloads original activity files through Strava's visible website UI. It
does not automate passwords, extract cookies, or call private Strava endpoints.

## Quick start

Run these commands from the repository root:

```bash
uv python install 3.13
uv sync
uv run --group local playwright install chromium
```

If `uv` is not installed, follow the [official installation instructions](https://docs.astral.sh/uv/getting-started/installation/).

The downloader prefers an installed Google Chrome because some identity
providers reject Playwright's bundled Chromium. Install Google Chrome if it is
not already available. The bundled browser remains a fallback for non-Google
login flows.

## First run

After completing one of the login setups below, start with a small dry run.
This discovers activity IDs but downloads nothing.

```bash
uv run --group local python strava_fetcher/download.py \
  --cdp-url http://127.0.0.1:9222 \
  --dry-run --limit 10
```

A browser window opens visibly when using the default mode. Log in to Strava
manually, complete 2FA if requested, then press **Enter** in the terminal. The
activity dates, titles, and IDs will be printed. With `--cdp-url`, the browser
must already be open and authenticated.

For **Sign in with Google**, use the regular Chrome connection method below.
Google can reject browser automation even when Playwright launches installed
Chrome.

## Google login: attach to regular Chrome

This avoids automating Google's login page. Close all Chrome windows first,
then launch a separate local Chrome profile with remote debugging:

```bash
google-chrome-stable \
  --remote-debugging-port=9222 \
  --user-data-dir="$PWD/.strava-chrome"
```

Log in to Strava normally in that Chrome window. Keep it open, then run the
downloader from another terminal:

```bash
uv run --group local python strava_fetcher/download.py \
  --cdp-url http://127.0.0.1:9222 \
  --dry-run --limit 10
```

After the dry run succeeds, remove `--dry-run` to download files. The scraper
attaches to the already-authenticated browser and never receives your Google
password.

An ordinary Chrome window is not enough: it was not started with a CDP port.
Chrome 136 and newer also refuse remote debugging on the default personal
profile. `.strava-chrome` is only a separate local browser profile; use your
normal Strava account in it.

## Download files

Download a small batch first:

```bash
uv run --group local python strava_fetcher/download.py \
  --cdp-url http://127.0.0.1:9222 \
  --limit 10
```

When that works, download all discovered activities:

```bash
uv run --group local python strava_fetcher/download.py \
  --cdp-url http://127.0.0.1:9222
```

Only original `.fit` files are saved in year/month folders:

```text
downloads/strava/YYYY/MM/YYYY-MM-DD_<strava-title>_<strava-id>.fit
```

Example:

```text
downloads/strava/2026/09/2026-09-12_MyWhoosh_-_Long_Ride_20146933314.fit
```

Activities whose original file is GPX or TCX are reported and skipped.
Whitespace and filesystem-invalid characters in titles are replaced with `_`.

To keep original TCX files too, add `--include-tcx`:

```bash
uv run --group local python strava_fetcher/download.py \
  --cdp-url http://127.0.0.1:9222 \
  --include-tcx
```

TCX files use the same year/month folders and filename format, with a `.tcx`
extension.

## Resume an interrupted run

Run the same command again. Existing FIT files are skipped, and failed
activities are retried. Progress is stored in:

```text
downloads/strava/.state.json
```

## Useful options

| Option | Purpose |
| --- | --- |
| `--dry-run` | Discover and print dates, titles, and IDs without downloading |
| `--limit 10` | Process only the first 10 activities |
| `--offset 90` | Skip the first 90 activities |
| `--after 2026-01-01` | Include activities from this date onward |
| `--before 2026-09-13` | Include activities through this date |
| `--delay 3` | Wait 3 seconds between downloads |
| `--max-pages 1000` | Allow a longer activity list to load |
| `--page-delay 2` | Wait 2 seconds between activity-list pages |
| `--sport Ride` | Keep one Strava sport type; repeat for multiple types |
| `--keyword Morning` | Filter by Strava activity keyword |
| `--include-tcx` | Save original TCX files instead of skipping them |
| `--output path` | Change the FIT output directory |
| `--profile path` | Change the persistent Chromium profile |
| `--headless` | Run without a window after login has been saved |
| `--cdp-url URL` | Attach to an already-running regular Chrome |

Example:

```bash
uv run --group local python strava_fetcher/download.py \
  --cdp-url http://127.0.0.1:9222 \
  --limit 25 \
  --sport Ride \
  --delay 3 \
  --output downloads/strava
```

To download regular and virtual rides from January through the current
activity list:

```bash
uv run --group local python strava_fetcher/download.py \
  --cdp-url http://127.0.0.1:9222 \
  --sport Ride \
  --sport VirtualRide \
  --after 2026-01-01
```

Repeat `--sport` to select multiple types. Date bounds are inclusive. Omit
`--before` to include everything currently visible in Strava.

The activity table has 20 rows per page. The downloader clicks its **Next**
button automatically, so `--limit 90` now discovers the first 90 activities
across five pages instead of stopping at 20.

To download the next 90 older activities, use an offset:

```bash
uv run --group local python strava_fetcher/download.py \
  --cdp-url http://127.0.0.1:9222 \
  --offset 90 --limit 90
```

Or omit `--limit` to process every page. Existing FIT files are still skipped.

## Troubleshooting

### Login prompt appears every run

Keep using the same `.strava-chromium` profile. Do not delete it between runs.
If the session expires, log in again when prompted.

### Google says “This browser or app may not be secure”

The scraper prefers installed Google Chrome, but Google may still reject a
Playwright-controlled login. Use the regular Chrome connection method instead:

```bash
google-chrome-stable \
  --remote-debugging-port=9222 \
  --user-data-dir="$PWD/.strava-chrome"

uv run --group local python strava_fetcher/download.py \
  --cdp-url http://127.0.0.1:9222 \
  --dry-run --limit 10
```

Do not add stealth flags or disable browser security checks.

### `Export Original control not found`

Strava may have changed its activity-page UI. Save the error output and update
the selectors in `strava_fetcher/download.py`.

### Activity is skipped

The activity's original upload is not a FIT file. Strava only returns the
format that was originally uploaded.

## Private data

`.strava-chromium/`, `.strava-chrome/`, `downloads/strava/`, and `.venv/` are
ignored by Git.
Keep the Chromium profile private because it contains your local authenticated
browser session.
