"""Recursive content-addressed ingestion, independent of downloader state."""

import hashlib
import json
import re
from pathlib import Path

from cycling.config import (
    DEFAULT_DOWNLOADS_DIR,
    NORMALIZER_VERSION,
    SUPPORTED_EXTENSIONS,
)
from cycling.normalization import normalize
from cycling.parsers import parse_file
from cycling.storage import Store, encode, now


def sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def extract_strava_hint(path: Path) -> str | None:
    match = re.search(r"(?:^|[^0-9])(\d{7,12})(?:[^0-9]|$)", path.name)
    return match.group(1) if match else None


def discover_sources(root: Path | str = DEFAULT_DOWNLOADS_DIR) -> list[Path]:
    dir_path = Path(root).resolve()
    if not dir_path.exists():
        return []
    if dir_path.is_file():
        return (
            [dir_path]
            if any(dir_path.name.lower().endswith(ext) for ext in SUPPORTED_EXTENSIONS)
            else []
        )

    found = []
    for path in dir_path.rglob("*"):
        if path.is_file():
            name_lower = path.name.lower()
            if any(name_lower.endswith(ext) for ext in SUPPORTED_EXTENSIONS):
                found.append(path)
    return sorted(found)


def audit_download_state(root: Path) -> dict:
    """Read acquisition evidence separately; never treat its dates as canonical."""
    state = root / ".state.json"
    if not state.is_file():
        return {"available": False}
    try:
        records = json.loads(state.read_text())["activities"]
        missing, failed = [], []
        for ident, record in records.items():
            if record.get("status") != "downloaded":
                failed.append(
                    {
                        "strava_id": ident,
                        "status": record.get("status"),
                        "result": record.get("result"),
                    }
                )
                continue
            reference = Path(record.get("result", ""))
            if not any(
                reference.name.lower().endswith(ext) for ext in SUPPORTED_EXTENSIONS
            ):
                continue
            if reference.is_absolute():
                path = reference.resolve()
            elif root.name in reference.parts:
                path = root.joinpath(
                    *reference.parts[reference.parts.index(root.name) + 1 :]
                ).resolve()
            else:
                path = (root / reference).resolve()
            if root in path.parents and not path.is_file():
                missing.append({"strava_id": ident, "path": str(path)})
        return {
            "available": True,
            "records": len(records),
            "failed_records": failed,
            "missing_downloads": missing,
        }
    except (ValueError, KeyError, TypeError, AttributeError, OSError) as exc:
        return {"available": False, "error": f"Cannot audit downloader state: {exc}"}


def ingest(
    store: Store, source: str | Path = DEFAULT_DOWNLOADS_DIR, force: bool = False
) -> dict:
    root = Path(source).resolve()
    if not root.exists():
        raise FileNotFoundError(f"Source does not exist: {root}")
    files = discover_sources(root)
    result = {"discovered": len(files), "ingested": 0, "skipped": 0, "errors": []}
    seen = set()

    for path in files:
        path = path.resolve()
        seen.add(str(path))
        digest, size = None, None
        try:
            size = path.stat().st_size
            digest = sha256(path)
            strava_hint = extract_strava_hint(path)

            try:
                existing = store.activity(digest)
            except KeyError:
                existing = None

            if (
                existing
                and not force
                and existing.get("normalizer_version") == NORMALIZER_VERSION
                and existing.get("sample_path")
                and (store.root / existing["sample_path"]).is_file()
                and existing.get("source_hash")
                and existing.get("start_time")
                and existing.get("duration_s") is not None
                and existing.get("modality")
            ):
                result["skipped"] += 1
                store.source(path, digest, size, "ready")
            else:
                metadata, raw = parse_file(path)
                metadata, samples = normalize(metadata, raw)
                if not samples:
                    raise ValueError("No timestamped samples found")
                if sha256(path) != digest:
                    raise ValueError(
                        "Source changed during ingestion; retry after download completes"
                    )
                metadata["source_name"] = path.name
                metadata["source_hash"] = digest
                if strava_hint:
                    metadata["strava_id_hint"] = strava_hint

                store.write_activity(digest, metadata, samples)
                store.source(path, digest, size, "ready")
                result["ingested"] += 1
        except Exception as exc:
            store.source(path, digest, size, "error", f"{type(exc).__name__}: {exc}")
            result["errors"].append({"path": str(path), "error": str(exc)})

    for item in store.status()["sources"]:
        path_item = Path(item["path"])
        if (
            (path_item == root or root in path_item.parents)
            and str(path_item) not in seen
            and not path_item.exists()
        ):
            store.source(
                path_item, item["hash"], item["size"], "missing", "Source file missing"
            )

    if root.is_dir():
        audit = audit_download_state(root)
        for item in audit.get("missing_downloads", []):
            path_item = Path(item["path"])
            existing = store.db.execute(
                "SELECT hash, size FROM sources WHERE path=?", [str(path_item)]
            ).fetchone()
            store.source(
                path_item,
                existing[0] if existing else None,
                existing[1] if existing else None,
                "missing",
                "Downloader state references a missing original",
            )
        store.db.execute(
            "INSERT OR REPLACE INTO acquisition_audits VALUES (?, ?, ?)",
            [str(root), now(), encode(audit)],
        )
        result["acquisition_state"] = audit

    status_str = "completed" if not result["errors"] else "completed_with_errors"
    store.record_run(status_str, result)
    return result
