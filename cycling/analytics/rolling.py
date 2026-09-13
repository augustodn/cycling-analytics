"""Contiguous rolling window calculations and active sample filtering.

Formulas & Principles:
----------------------
1. Active Filter:
   Only samples with `active=True` are processed for physical movement analytics.
   Samples are ordered by ascending `elapsed_s`.

2. Contiguous Runs:
   A run is a continuous sequence of 1-second step samples where:
   - sample[i].elapsed_s == sample[i-1].elapsed_s + 1
   - sample[i].segment == sample[i-1].segment
   - sample[key] is neither None nor NaN.

3. Complete Contiguous Rolling Window:
   For a given window duration d (seconds):
   - Sliding window moves over contiguous runs without crossing timestamp gaps, segment breaks, or missing values.
   - For run values [v_0, v_1, ..., v_{m-1}], the rolling mean at end position i (where i >= d - 1) is:
     W_d(i) = (1 / d) * sum_{j = i - d + 1}^{i} v_j
   - Incomplete windows (< d seconds) are strictly omitted.

4. Coverage:
   - Covered seconds = count of active samples with valid (non-None, non-NaN) key value.
   - Coverage fraction = Covered seconds / total active seconds.
"""

import math
from typing import Any, Dict, Generator, List, Optional, Sequence, Tuple

from cycling.analytics.types import Coverage


def _is_valid_value(val: Any) -> bool:
    """Check if value is present, non-None, and not NaN."""
    if val is None:
        return False
    if isinstance(val, (int, float)):
        return not math.isnan(val)
    return True


def filter_active(samples: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Filter and sort active samples by elapsed_s."""
    return sorted(
        (s for s in samples if s.get("active", False)),
        key=lambda s: s["elapsed_s"],
    )


def extract_runs(
    samples: Sequence[Dict[str, Any]], key: str
) -> Generator[List[Dict[str, Any]], None, None]:
    """Yield contiguous, gap-free sequences of active samples containing valid key values."""
    run: List[Dict[str, Any]] = []
    previous: Optional[Dict[str, Any]] = None

    for sample in filter_active(samples):
        val = sample.get(key)
        valid = _is_valid_value(val)
        contiguous = (
            previous is not None
            and sample["elapsed_s"] == previous["elapsed_s"] + 1
            and sample.get("segment") == previous.get("segment")
        )

        if not valid or not contiguous:
            if run:
                yield run
            run = []

        if valid:
            run.append(sample)
        previous = sample

    if run:
        yield run


def rolling_windows(
    samples: Sequence[Dict[str, Any]], key: str, duration: int
) -> Generator[Tuple[Dict[str, Any], float], None, None]:
    """Yield (end_sample, rolling_average) for complete contiguous duration-second windows in O(n)."""
    if duration < 1:
        raise ValueError("duration must be a positive integer >= 1")

    for run in extract_runs(samples, key):
        if len(run) < duration:
            continue
        values = [float(s[key]) for s in run]
        window_sum = sum(values[:duration])
        yield run[duration - 1], window_sum / duration

        for i in range(duration, len(values)):
            window_sum += values[i] - values[i - duration]
            yield run[i], window_sum / duration


def calculate_coverage(
    samples: Sequence[Dict[str, Any]], key: str, active_seconds: int
) -> Coverage:
    """Calculate duration and fraction of active samples with valid data for a given key."""
    covered = sum(1 for s in filter_active(samples) if _is_valid_value(s.get(key)))
    fraction = covered / active_seconds if active_seconds > 0 else 0.0
    return Coverage(seconds=covered, fraction=fraction)
