"""One-second zero-order hold, never inventing sensors or bridging unknown gaps."""

import math
from datetime import UTC, datetime, timedelta

from .parsers import FIELDS


def _utc(value):
    parsed = (
        value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    )
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)).astimezone(UTC)


def normalize(metadata: dict, rows: list[dict]) -> tuple[dict, list[dict]]:
    flags = set(metadata.get("quality_flags", []))
    seen = {}
    previous = None
    for raw in rows:
        if raw.get("timestamp") is None:
            flags.add("missing_timestamp")
            continue
        timestamp = _utc(raw["timestamp"])
        if timestamp.microsecond:
            flags.add("subsecond_timestamps_floored")
        timestamp = timestamp.replace(microsecond=0)
        if previous is not None and timestamp < previous:
            flags.add("reordered_records")
        previous = timestamp
        clean = {}
        for field in FIELDS:
            value = raw.get(field)
            try:
                number = float(value) if value is not None else None
                bad = number is not None and (
                    not math.isfinite(number)
                    or (
                        number < 0
                        and field not in {"altitude_m", "latitude", "longitude"}
                    )
                    or (field == "latitude" and not -90 <= number <= 90)
                    or (field == "longitude" and not -180 <= number <= 180)
                    or (field == "hr_bpm" and number == 0)
                )
            except (TypeError, ValueError):
                number, bad = None, True
            clean[field] = None if bad else number
            if bad:
                flags.add(f"invalid_{field}")
        if timestamp in seen:
            flags.add("duplicate_timestamps")
            for field in FIELDS:
                if seen[timestamp][field] is None:
                    seen[timestamp][field] = clean[field]
                elif (
                    clean[field] is not None and clean[field] != seen[timestamp][field]
                ):
                    flags.add("conflicting_duplicate_values_first_wins")
        else:
            seen[timestamp] = clean
    if not seen:
        raise ValueError("No timestamped samples")
    times = sorted(seen)
    start, finish = times[0], times[-1] + timedelta(seconds=1)
    if (finish - start).total_seconds() > 7 * 86400:
        raise ValueError(
            "Activity span exceeds seven days; possible corrupted timestamps"
        )
    pauses, stopped = [], None
    for event in sorted(
        metadata.get("timer_events", []), key=lambda e: _utc(e["timestamp"])
    ):
        timestamp = _utc(event["timestamp"]).replace(microsecond=0)
        kind = str(event.get("event_type", event.get("event", ""))).lower()
        if "stop" in kind and stopped is None:
            stopped = timestamp
        elif "start" in kind and stopped is not None:
            if timestamp > stopped:
                pauses.append((max(start, stopped), min(finish, timestamp)))
            stopped = None
    if stopped is not None:
        pauses.append((max(start, stopped), finish))
    pauses = [(a, b) for a, b in pauses if b > a]
    if pauses:
        flags.add("timer_pauses")
    cells = {}
    for index, timestamp in enumerate(times):
        next_time = times[index + 1] if index + 1 < len(times) else finish
        end = min(next_time, timestamp + timedelta(seconds=5))
        if next_time - timestamp > timedelta(seconds=5):
            flags.add("gaps")
        for offset in range(int((end - timestamp).total_seconds())):
            cell = timestamp + timedelta(seconds=offset)
            # Never carry a pre-pause value through a timer restart.
            if any(timestamp < begin <= cell for begin, _ in pauses):
                break
            cells[cell] = {**seen[timestamp], "active": True}
    for begin, end in pauses:
        for offset in range(int((end - begin).total_seconds())):
            cells[begin + timedelta(seconds=offset)] = {
                **dict.fromkeys(FIELDS),
                "active": False,
            }
    result, segment, previous = [], 0, None
    for timestamp, cell in sorted(cells.items()):
        if previous is not None and (
            timestamp - previous["timestamp"] != timedelta(seconds=1)
            or cell["active"] != previous["active"]
        ):
            segment += 1
        row = {
            **cell,
            "timestamp": timestamp,
            "elapsed_s": int((timestamp - start).total_seconds()),
            "segment": segment,
        }
        result.append(row)
        previous = row
    for field in FIELDS:
        if not any(row[field] is not None for row in result if row["active"]):
            flags.add(f"missing_{field}")
    if metadata.get("modality", "unknown") == "unknown":
        flags.add("unknown_modality")
    flags.add("last_record_one_second")
    metadata = {
        **metadata,
        "start_time": start.isoformat().replace("+00:00", "Z"),
        "quality_flags": sorted(flags),
        "elapsed_seconds": int((finish - start).total_seconds()),
        "uncovered_seconds": int((finish - start).total_seconds()) - len(result),
    }
    return metadata, result
