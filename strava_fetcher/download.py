"""Download original FIT files from the authenticated Strava activity list."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from playwright.sync_api import (
    Download,
    Error as PlaywrightError,
    Locator,
    Page,
    TimeoutError,
    sync_playwright,
)


BASE_URL = "https://www.strava.com"
TRAINING_URL = f"{BASE_URL}/athlete/training"
ACTIVITY_ID_RE = re.compile(r"/activities/(\d+)(?:[/?#]|$)")
DISPLAY_DATE_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
INVALID_FILENAME_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
LOGIN_RE = re.compile(r"/login(?:[/?#]|$)")
EXPORT_RE = re.compile(r"Export Original", re.IGNORECASE)
MENU_RE = re.compile(r"more|options|actions|overflow", re.IGNORECASE)


@dataclass(frozen=True)
class Activity:
    activity_id: str
    title: str
    activity_date: date
    sport_type: str


def parse_iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD") from exc


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("downloads/strava"),
        help="Directory for FIT files and progress state (default: downloads/strava)",
    )
    parser.add_argument(
        "--profile",
        type=Path,
        default=Path(".strava-chromium"),
        help="Persistent Chromium profile directory (default: .strava-chromium)",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=500,
        help="Maximum activity-list pages (default: 500)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Process at most this many discovered activities",
    )
    parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Skip this many activities before processing (default: 0)",
    )
    parser.add_argument(
        "--after",
        type=parse_iso_date,
        help="Include activities on or after YYYY-MM-DD",
    )
    parser.add_argument(
        "--before",
        type=parse_iso_date,
        help="Include activities on or before YYYY-MM-DD",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=2.0,
        help="Seconds between activity downloads (default: 2)",
    )
    parser.add_argument(
        "--page-delay",
        "--scroll-delay",
        dest="page_delay",
        type=float,
        default=1.5,
        help="Seconds to wait after each activity-list page (default: 1.5)",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run headless; use only after the persistent profile has been logged in",
    )
    parser.add_argument(
        "--cdp-url",
        help="Attach to an already-running regular Chrome, e.g. http://127.0.0.1:9222",
    )
    parser.add_argument(
        "--sport",
        dest="sports",
        action="append",
        help="Strava sport type value, e.g. Ride, MountainBikeRide, or GravelRide",
    )
    parser.add_argument(
        "--keyword",
        help="Filter activities by keyword using Strava's activity search",
    )
    parser.add_argument(
        "--include-tcx",
        action="store_true",
        help="Save original TCX files instead of skipping them",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Discover and print activities without downloading",
    )
    return parser.parse_args(argv)


def first_visible(locator: Locator) -> Locator | None:
    """Return the first visible match without assuming a stable DOM order."""
    for index in range(locator.count()):
        candidate = locator.nth(index)
        if candidate.is_visible():
            return candidate
    return None


def logged_out(page: Page) -> bool:
    return bool(LOGIN_RE.search(page.url))


def wait_for_activity_table(page: Page) -> None:
    page.locator("tr.training-activity-row").first.wait_for(
        state="visible", timeout=30_000
    )
    page.wait_for_timeout(1_000)


def parse_activity_date(value: str) -> date:
    match = DISPLAY_DATE_RE.search(value)
    if not match:
        raise ValueError(f"could not parse activity date: {value!r}")
    month, day, year = (int(part) for part in match.groups())
    return date(year, month, day)


def safe_title(title: str) -> str:
    title = INVALID_FILENAME_CHARS_RE.sub("_", title).strip()
    title = re.sub(r"\s+", "_", title)
    title = re.sub(r"_+", "_", title).strip(" ._")
    return (title or "untitled")[:150].rstrip(" ._")


def normalized_sport(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def activity_from_row(row: Locator) -> Activity:
    link = row.locator('a[href*="/activities/"]').first
    href = link.get_attribute("href") or ""
    match = ACTIVITY_ID_RE.search(href)
    if not match:
        raise ValueError(f"could not parse activity ID from {href!r}")
    return Activity(
        activity_id=match.group(1),
        title=link.inner_text().strip(),
        activity_date=parse_activity_date(row.locator("td.col-date").inner_text()),
        sport_type=row.locator("td.col-type").inner_text().strip(),
    )


def activity_path(output: Path, activity: Activity, extension: str = ".fit") -> Path:
    year = f"{activity.activity_date.year:04d}"
    month = f"{activity.activity_date.month:02d}"
    if not extension.startswith("."):
        extension = f".{extension}"
    filename = (
        f"{activity.activity_date.isoformat()}_"
        f"{safe_title(activity.title)}_{activity.activity_id}{extension}"
    )
    return output / year / month / filename


def open_training_page(page: Page) -> None:
    page.goto(TRAINING_URL, wait_until="domcontentloaded", timeout=60_000)
    if not logged_out(page):
        wait_for_activity_table(page)
        return

    print("Log in to Strava in the Chromium window, then press Enter here.")
    try:
        input()
    except EOFError as exc:
        raise RuntimeError("complete login in the visible Chromium window and rerun") from exc
    page.goto(TRAINING_URL, wait_until="domcontentloaded", timeout=60_000)
    if logged_out(page):
        raise RuntimeError("Strava login was not completed")
    wait_for_activity_table(page)


def apply_filters(page: Page, sport: str | None, keyword: str | None) -> None:
    if sport is None and keyword is None:
        return

    if sport is not None:
        page.locator('select[name="sport_type"]').first.select_option(sport)
    if keyword is not None:
        page.locator("#keywords").fill(keyword)

    page.locator('button[type="submit"]').first.click()
    wait_for_activity_table(page)


def activity_ids(
    page: Page,
    max_pages: int,
    page_delay: float,
    stop_after: int | None,
) -> list[Activity]:
    """Collect activities across the training table's paginated rows."""
    found: dict[str, Activity] = {}

    for _ in range(max_pages):
        rows = page.locator("tr.training-activity-row")
        for index in range(rows.count()):
            activity = activity_from_row(rows.nth(index))
            found[activity.activity_id] = activity

        if stop_after is not None and len(found) >= stop_after:
            break

        next_button = page.locator("button.next_page")
        if not next_button.count() or next_button.is_disabled():
            break

        first_href = None
        if rows.count():
            first_href = rows.first.locator('a[href*="/activities/"]').first.get_attribute(
                "href"
            )
        next_button.click()
        if first_href:
            page.wait_for_function(
                """
                (previous) => {
                    const link = document.querySelector(
                        'tr.training-activity-row a[href*="/activities/"]'
                    );
                    return link && link.href !== previous;
                }
                """,
                arg=first_href,
                timeout=20_000,
            )
        page.wait_for_timeout(round(page_delay * 1000))

    return list(found.values())


def export_control(page: Page) -> Locator | None:
    menu_candidates = [
        page.get_by_role("button", name=MENU_RE),
        page.locator('[aria-label*="More"], [aria-label*="more"]'),
        page.locator('[data-testid*="more"], [data-testid*="More"]'),
    ]
    for _ in range(20):
        direct = first_visible(page.get_by_text(EXPORT_RE))
        if direct:
            return direct

        for menu_locator in menu_candidates:
            menu = first_visible(menu_locator)
            if not menu:
                continue
            try:
                menu.click(timeout=5_000)
            except TimeoutError:
                continue
            page.wait_for_timeout(300)
            direct = first_visible(page.get_by_text(EXPORT_RE))
            if direct:
                return direct

        page.wait_for_timeout(500)

    return None


def save_original_download(
    download: Download, output: Path, activity: Activity, include_tcx: bool
) -> str:
    failure = download.failure()
    if failure:
        raise RuntimeError(f"download failed: {failure}")

    filename = download.suggested_filename
    extension = Path(filename).suffix.lower()
    if extension not in {".fit", ".tcx"} or (extension == ".tcx" and not include_tcx):
        return f"skipped ({extension or 'unknown'} original)"

    target = activity_path(output, activity, extension)
    target.parent.mkdir(parents=True, exist_ok=True)
    download.save_as(target)
    return str(target)


def state_path(output: Path) -> Path:
    return output / ".state.json"


def installed_chrome_channel() -> str | None:
    """Prefer a branded browser for providers that reject bundled Chromium."""
    if any(
        shutil.which(command)
        for command in ("google-chrome", "google-chrome-stable", "chrome")
    ):
        return "chrome"
    return None


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"activities": {}}
    try:
        state = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid state file: {path}") from exc
    if not isinstance(state, dict) or not isinstance(state.get("activities", {}), dict):
        raise RuntimeError(f"invalid state file: {path}")
    return state


def write_state(path: Path, state: dict[str, Any]) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def download_activity(
    page: Page, activity: Activity, output: Path, include_tcx: bool
) -> str:
    page.goto(
        f"{BASE_URL}/activities/{activity.activity_id}",
        wait_until="domcontentloaded",
        timeout=60_000,
    )
    if logged_out(page):
        raise RuntimeError("Strava session expired")

    export = export_control(page)
    if not export:
        raise RuntimeError("Export Original control not found")

    with page.expect_download(timeout=20_000) as download_info:
        export.click()
    return save_original_download(download_info.value, output, activity, include_tcx)


def run(args: argparse.Namespace) -> int:
    if (
        args.max_pages < 1
        or args.delay < 0
        or args.page_delay < 0
        or args.offset < 0
        or (args.limit is not None and args.limit < 1)
        or (
            args.after is not None
            and args.before is not None
            and args.after > args.before
        )
    ):
        raise ValueError(
            "max-pages must be positive; offset, limit, and dates must be valid"
        )

    args.output.mkdir(parents=True, exist_ok=True)
    progress_file = state_path(args.output)
    state = load_state(progress_file)

    with sync_playwright() as playwright:
        browser = None
        if args.cdp_url:
            try:
                browser = playwright.chromium.connect_over_cdp(args.cdp_url)
            except PlaywrightError as exc:
                if "ECONNREFUSED" in str(exc):
                    raise RuntimeError(
                        f"cannot connect to {args.cdp_url}; close Chrome and launch "
                        "a separate profile with --remote-debugging-port=9222"
                    ) from exc
                raise
            if not browser.contexts:
                raise RuntimeError("connected Chrome has no browser context")
            context = browser.contexts[0]
        else:
            channel = installed_chrome_channel()
            if channel is None:
                print(
                    "warning: Google Chrome was not found; using bundled Chromium. "
                    "Google sign-in may reject it.",
                    file=sys.stderr,
                )
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(args.profile),
                channel=channel,
                headless=args.headless,
                accept_downloads=True,
            )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            open_training_page(page)
            server_sport = args.sports[0] if len(args.sports or []) == 1 else None
            apply_filters(page, server_sport, args.keyword)
            stop_after = (
                args.offset + args.limit
                if args.limit is not None
                and len(args.sports or []) <= 1
                and args.after is None
                and args.before is None
                else None
            )
            discovered = activity_ids(page, args.max_pages, args.page_delay, stop_after)
            if args.sports:
                allowed_sports = {normalized_sport(sport) for sport in args.sports}
                discovered = [
                    activity
                    for activity in discovered
                    if normalized_sport(activity.sport_type) in allowed_sports
                ]
            if args.after is not None:
                discovered = [
                    activity
                    for activity in discovered
                    if activity.activity_date >= args.after
                ]
            if args.before is not None:
                discovered = [
                    activity
                    for activity in discovered
                    if activity.activity_date <= args.before
                ]
            activities = discovered[args.offset :]
            if args.limit is not None:
                activities = activities[: args.limit]
            print(f"Discovered {len(activities)} activities")

            if args.dry_run:
                for activity in activities:
                    print(
                        f"{activity.activity_date.isoformat()} "
                        f"{activity.title} [{activity.activity_id}]"
                    )
                return 0

            records = state.setdefault("activities", {})
            for position, activity in enumerate(activities, start=1):
                record = records.get(activity.activity_id, {})
                fit_target = activity_path(args.output, activity)
                tcx_target = activity_path(args.output, activity, ".tcx")
                already_handled = (
                    fit_target.exists()
                    or tcx_target.exists()
                    or (record.get("status") == "skipped" and not args.include_tcx)
                )
                if already_handled:
                    print(
                        f"[{position}/{len(activities)}] {activity.activity_id}: "
                        "already handled"
                    )
                    continue

                try:
                    result = download_activity(
                        page, activity, args.output, args.include_tcx
                    )
                    status = (
                        "downloaded"
                        if result.endswith((".fit", ".tcx"))
                        else "skipped"
                    )
                    records[activity.activity_id] = {
                        "status": status,
                        "result": result,
                        "date": activity.activity_date.isoformat(),
                        "title": activity.title,
                    }
                    print(
                        f"[{position}/{len(activities)}] {activity.activity_id}: "
                        f"{result}"
                    )
                except (TimeoutError, RuntimeError, OSError) as exc:
                    records[activity.activity_id] = {
                        "status": "failed",
                        "error": str(exc),
                        "date": activity.activity_date.isoformat(),
                        "title": activity.title,
                    }
                    print(
                        f"[{position}/{len(activities)}] {activity.activity_id}: "
                        f"failed: {exc}",
                        file=sys.stderr,
                    )
                finally:
                    write_state(progress_file, state)
                    page.wait_for_timeout(round(args.delay * 1000))
        finally:
            if browser:
                browser.close()
            else:
                context.close()

    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        return run(parse_args(argv))
    except (PlaywrightError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
