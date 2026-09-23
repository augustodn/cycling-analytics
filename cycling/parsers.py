"""Small, loss-aware FIT and TCX decoders."""

import gzip
import io
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from pathlib import Path

FIELDS = (
    "power_w",
    "hr_bpm",
    "cadence_rpm",
    "speed_mps",
    "distance_m",
    "altitude_m",
    "latitude",
    "longitude",
)


def _utc(value):
    if isinstance(value, datetime):
        return (value if value.tzinfo else value.replace(tzinfo=UTC)).astimezone(UTC)
    text = str(value).replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)).astimezone(UTC)


def _iso(value):
    return _utc(value).isoformat().replace("+00:00", "Z")


def _number(value):
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return value  # Normalization retains a quality flag for malformed sensors.
    return value


def _text(value):
    return (
        str(getattr(value, "value", value)).rsplit(".", 1)[-1].lower().replace(" ", "_")
    )


def _is_virtual_indoor_platform(*values):
    text = " ".join(str(value) for value in values if value is not None).casefold()
    return "zwift" in text or "mywhoosh" in text


def _modality(*values):
    if _is_virtual_indoor_platform(*values):
        return "indoor"
    text = " ".join(_text(v) for v in values if v is not None)
    for name in ("mtb", "mountain", "gravel", "road", "indoor", "trainer"):
        if name in text:
            return (
                "mtb"
                if name == "mountain"
                else ("indoor" if name == "trainer" else name)
            )
    return "unknown"


def _row(timestamp, values):
    result = {"timestamp": _utc(timestamp)}
    result.update({field: values.get(field) for field in FIELDS})
    return result


def _fit_coordinate(value):
    return value * 180 / 2**31 if isinstance(value, (int, float)) else value


def _read_bytes(path: Path) -> bytes:
    if path.suffix.lower() == ".gz":
        with gzip.open(path, "rb") as handle:
            return handle.read()
    return path.read_bytes()


def _tcx(path: Path):
    data = _read_bytes(path)
    if re.search(rb"<!DOCTYPE|<!ENTITY", data.replace(b"\x00", b""), re.IGNORECASE):
        raise ValueError("TCX DTD/entity declarations are not allowed")
    root = ET.fromstring(data)
    activities = [
        node for node in root.iter() if node.tag.rsplit("}", 1)[-1] == "Activity"
    ]
    if len(activities) != 1:
        raise ValueError("TCX must contain exactly one activity")
    activity = activities[0]
    sport = activity.attrib.get("Sport", "unknown").lower()
    sport = "cycling" if sport == "biking" else sport
    creator = next(
        (
            node.text
            for parent in activity.iter()
            if parent.tag.rsplit("}", 1)[-1] == "Creator"
            for node in parent.iter()
            if node.tag.rsplit("}", 1)[-1] == "Name"
        ),
        None,
    )
    rows, starts = [], []
    assumed_utc = False
    laps = []
    lap_nodes = [
        node for node in activity.iter() if node.tag.rsplit("}", 1)[-1] == "Lap"
    ]
    for lap_idx, lap_node in enumerate(lap_nodes):
        start_attr = lap_node.attrib.get("StartTime")
        start_iso = _iso(_utc(start_attr)) if start_attr else None
        duration_s, distance_m = None, None
        avg_hr_bpm, max_hr_bpm = None, None
        avg_cadence_rpm, max_cadence_rpm = None, None
        avg_power_w, max_power_w = None, None

        for child in lap_node:
            tag = child.tag.rsplit("}", 1)[-1]
            if tag == "Track":
                continue
            nodes = list(child.iter()) if tag == "Extensions" else [child]
            for node in nodes:
                t = node.tag.rsplit("}", 1)[-1]
                if t == "TotalTimeSeconds" and node.text:
                    duration_s = _number(node.text)
                elif t == "DistanceMeters" and node.text:
                    distance_m = _number(node.text)
                elif t in {"Cadence", "RunCadence"} and node.text:
                    avg_cadence_rpm = _number(node.text)
                elif t == "MaxCadence" and node.text:
                    max_cadence_rpm = _number(node.text)
                elif t in {"Watts", "AvgWatts"} and node.text:
                    avg_power_w = _number(node.text)
                elif t == "MaxWatts" and node.text:
                    max_power_w = _number(node.text)
                elif t == "AverageHeartRateBpm":
                    avg_hr_bpm = next(
                        (
                            _number(c.text)
                            for c in node
                            if c.tag.rsplit("}", 1)[-1] == "Value"
                        ),
                        None,
                    )
                elif t == "MaximumHeartRateBpm":
                    max_hr_bpm = next(
                        (
                            _number(c.text)
                            for c in node
                            if c.tag.rsplit("}", 1)[-1] == "Value"
                        ),
                        None,
                    )

        end_iso = None
        if start_iso and duration_s is not None:
            try:
                dt_start = _utc(start_iso)
                dt_end = dt_start + timedelta(seconds=float(duration_s))
                end_iso = _iso(dt_end)
            except Exception:
                end_iso = None

        laps.append(
            {
                "lap_index": lap_idx,
                "start_time": start_iso,
                "end_time": end_iso,
                "duration_s": duration_s,
                "distance_m": distance_m,
                "avg_power_w": avg_power_w,
                "max_power_w": max_power_w,
                "avg_hr_bpm": avg_hr_bpm,
                "max_hr_bpm": max_hr_bpm,
                "avg_cadence_rpm": avg_cadence_rpm,
                "max_cadence_rpm": max_cadence_rpm,
            }
        )

    for point in (
        node for node in activity.iter() if node.tag.rsplit("}", 1)[-1] == "Trackpoint"
    ):
        values = {}
        timestamp = None
        for node in point.iter():
            name = node.tag.rsplit("}", 1)[-1]
            if not node.text:
                continue
            if name == "Time":
                assumed_utc |= not bool(re.search(r"(?:Z|[+-]\d\d:\d\d)$", node.text))
                timestamp = _utc(node.text)
            elif name in {"Watts", "Speed", "DistanceMeters", "AltitudeMeters"}:
                values[
                    {
                        "Watts": "power_w",
                        "Speed": "speed_mps",
                        "DistanceMeters": "distance_m",
                        "AltitudeMeters": "altitude_m",
                    }[name]
                ] = _number(node.text)
            elif name in {"Cadence", "RunCadence"}:
                values["cadence_rpm"] = _number(node.text)
            elif name in {"LatitudeDegrees", "LongitudeDegrees"}:
                values["latitude" if name.startswith("Latitude") else "longitude"] = (
                    _number(node.text)
                )
        if timestamp is None:
            continue
        for parent in point.iter():
            if parent.tag.rsplit("}", 1)[-1] == "HeartRateBpm":
                values["hr_bpm"] = next(
                    (
                        _number(child.text)
                        for child in parent
                        if child.tag.rsplit("}", 1)[-1] == "Value"
                    ),
                    None,
                )
        rows.append(_row(timestamp, values))
        starts.append(timestamp)
    if not rows:
        raise ValueError("TCX contains no timed trackpoints")
    flags = ["timer_state_unavailable"] + (["assumed_utc"] if assumed_utc else [])
    ret = {
        "start_time": _iso(min(starts)),
        "sport": sport,
        "subsport": "unknown",
        "device": creator or "unknown",
        "modality": _modality(sport, creator),
        "quality_flags": flags,
        "format": "tcx",
    }
    if laps:
        ret["laps"] = laps
    return ret, rows


def _fit(path: Path):
    try:
        import fitdecode
    except ImportError as exc:
        raise ImportError("fitdecode package is required to parse FIT files") from exc

    rows, sessions, events, device, laps = [], [], [], [], []
    source_input = path
    if path.suffix.lower() == ".gz":
        source_input = io.BytesIO(_read_bytes(path))

    with fitdecode.FitReader(
        source_input, check_crc=fitdecode.CrcCheck.RAISE
    ) as reader:
        for frame in reader:
            if frame.frame_type != fitdecode.FIT_FRAME_DATA:
                continue
            name = frame.name.lower()
            if name not in {"record", "session", "event", "device_info", "lap"}:
                continue

            def get(field, default=None, frame=frame):
                return frame.get_value(field, fallback=default)

            if name == "record":
                timestamp = get("timestamp")
                if timestamp is not None:
                    rows.append(
                        _row(
                            timestamp,
                            {
                                "power_w": get("power"),
                                "hr_bpm": get("heart_rate"),
                                "cadence_rpm": get("cadence"),
                                "speed_mps": get("enhanced_speed", get("speed")),
                                "distance_m": get("distance"),
                                "altitude_m": get("enhanced_altitude", get("altitude")),
                                "latitude": _fit_coordinate(get("position_lat")),
                                "longitude": _fit_coordinate(get("position_long")),
                            },
                        )
                    )
            elif name == "lap":
                start_t = get("start_time")
                end_t = get("timestamp")
                elapsed_t = get("total_elapsed_time", get("total_timer_time"))
                start_iso = _iso(start_t) if start_t is not None else None
                end_iso = _iso(end_t) if end_t is not None else None
                duration_s = _number(elapsed_t)
                if start_iso is None and end_t is not None and duration_s is not None:
                    try:
                        start_iso = _iso(
                            _utc(end_t) - timedelta(seconds=float(duration_s))
                        )
                    except Exception:
                        pass
                if end_iso is None and start_t is not None and duration_s is not None:
                    try:
                        end_iso = _iso(
                            _utc(start_t) + timedelta(seconds=float(duration_s))
                        )
                    except Exception:
                        pass
                if duration_s is None and start_t is not None and end_t is not None:
                    try:
                        duration_s = (_utc(end_t) - _utc(start_t)).total_seconds()
                    except Exception:
                        pass
                laps.append(
                    {
                        "lap_index": len(laps),
                        "start_time": start_iso,
                        "end_time": end_iso,
                        "duration_s": duration_s,
                        "distance_m": _number(get("total_distance")),
                        "avg_power_w": _number(get("avg_power")),
                        "max_power_w": _number(get("max_power")),
                        "avg_hr_bpm": _number(get("avg_heart_rate")),
                        "max_hr_bpm": _number(get("max_heart_rate")),
                        "avg_cadence_rpm": _number(get("avg_cadence")),
                        "max_cadence_rpm": _number(get("max_cadence")),
                    }
                )
            elif name == "session":
                sessions.append(
                    {
                        "sport": get("sport", "unknown"),
                        "subsport": get("sub_sport", "unknown"),
                        "start_time": get("start_time"),
                    }
                )
            elif (
                name == "event"
                and _text(get("event")) == "timer"
                and get("timestamp") is not None
            ):
                events.append(
                    {
                        "timestamp": _iso(get("timestamp")),
                        "event": _text(get("event_type")),
                    }
                )
            elif name == "device_info":
                device.extend(
                    v for v in (get("manufacturer"), get("product")) if v is not None
                )
    if len(sessions) > 1:
        raise ValueError("FIT multisession files are not supported")
    if not rows:
        raise ValueError("FIT contains no timed records")
    session = sessions[0] if sessions else {}
    start = session.get("start_time") or rows[0]["timestamp"]
    sport, subsport = (
        session.get("sport", "unknown"),
        session.get("subsport", "unknown"),
    )
    metadata = {
        "start_time": _iso(start),
        "sport": _text(sport),
        "subsport": _text(subsport),
        "device": " ".join(map(str, device)) or "unknown",
        "modality": _modality(sport, subsport, *device),
        "quality_flags": [],
        "format": "fit",
    }
    if events:
        metadata["timer_events"] = events
    else:
        metadata["quality_flags"].append("timer_state_unavailable")
    if laps:
        metadata["laps"] = laps
    return metadata, rows


def parse_file(path: Path) -> tuple[dict, list[dict]]:
    """Parse one original activity file into metadata and raw canonical rows."""
    name = path.name.lower()
    if name.endswith(".tcx") or name.endswith(".tcx.gz"):
        metadata, rows = _tcx(path)
    elif name.endswith(".fit") or name.endswith(".fit.gz"):
        metadata, rows = _fit(path)
    else:
        raise ValueError(f"unsupported activity format: {path.suffix}")
    if _is_virtual_indoor_platform(path.name):
        metadata["modality"] = "indoor"
    return metadata, rows
