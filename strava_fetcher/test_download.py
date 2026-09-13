from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from strava_fetcher.download import (
    ACTIVITY_ID_RE,
    Activity,
    activity_path,
    load_state,
    parse_activity_date,
    safe_title,
    write_state,
)


def test_activity_id_pattern() -> None:
    assert ACTIVITY_ID_RE.search("https://www.strava.com/activities/12345")
    assert ACTIVITY_ID_RE.search("/activities/12345/photos")
    assert not ACTIVITY_ID_RE.search("/athlete/training")


def test_state_round_trip() -> None:
    with TemporaryDirectory() as directory:
        path = Path(directory) / ".state.json"
        state = {"activities": {"12345": {"status": "downloaded"}}}
        write_state(path, state)
        assert load_state(path) == state


def test_activity_filename() -> None:
    activity = Activity("42", "Morning Ride/Ride", date(2026, 9, 12), "Ride")
    assert parse_activity_date("Sat, 9/12/2026") == date(2026, 9, 12)
    assert safe_title("Morning Ride/Ride") == "Morning_Ride_Ride"
    assert activity_path(Path("downloads/strava"), activity) == Path(
        "downloads/strava/2026/09/2026-09-12_Morning_Ride_Ride_42.fit"
    )
    assert activity_path(Path("downloads/strava"), activity, ".tcx").suffix == ".tcx"


if __name__ == "__main__":
    test_activity_id_pattern()
    test_state_round_trip()
    test_activity_filename()
    print("self-check passed")
