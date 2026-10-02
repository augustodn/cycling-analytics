"""Shared serialization helpers that do not load a storage engine."""

import json
from datetime import datetime, timezone


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def encode(value) -> str:
    return json.dumps(value, default=str, allow_nan=False)
