"""Durability analytics based strictly on prior cumulative work (kJ) exposure.

Formulas & Principles:
----------------------
1. Cumulative Work Accumulation:
   For 1-second active power samples [P_0, P_1, ..., P_{t-1}]:
   Work_kJ(t) = (1 / 1000) * sum_{i=0}^{t-1} P_i

2. Requirements & Availability:
   - Requires valid power data (power coverage >= 95%).
   - All active samples must have non-null, non-NaN power values.
   - Requires continuous 1-second step samples without unexplained timestamp gaps.
   - For no-power activities, durability is strictly UNAVAILABLE (no fake power/kJ).

3. Work Buckets:
   Configurable bucket boundaries [W_0, W_1, ..., W_m] (default: [0, 500, 1000, 1500] kJ).
   Must start at 0 and strictly increase.
   Each bucket represents work range [W_k, W_{k+1}).

4. Per-Bucket MMP & Decay:
   For each requested duration d (seconds):
   - Computes best average power for complete contiguous d-second windows whose work range
     [Work(start_s), Work(end_s)] falls within [W_k, W_{k+1}).
   - Baseline performance = best_w in first bucket [W_0, W_1).
   - Relative performance change:
     change_percent = 100 * (best_w_bucket - best_w_baseline) / best_w_baseline
"""

from itertools import pairwise
from typing import Any, Dict, List, Optional, Sequence

from cycling.analytics.power import _validate_durations
from cycling.analytics.rolling import (
    _is_valid_value,
    calculate_coverage,
    filter_active,
    rolling_windows,
)
from cycling.analytics.types import DurabilityBucket, DurabilityResult


def durability(
    samples: Sequence[Dict[str, Any]],
    durations: Optional[Sequence[int]] = None,
    bucket_kj: Optional[Sequence[float]] = None,
) -> Dict[str, Any]:
    """Calculate durability decay of Mean Maximal Power across prior work buckets."""
    dur_seq = (
        (
            5,
            30,
            60,
            300,
            600,
            900,
            1200,
            1800,
            2700,
            3600,
            4500,
            5400,
            6300,
            7200,
            9000,
            10800,
            12600,
            14400,
            16200,
            18000,
            19800,
            21600,
        )
        if durations is None
        else tuple(durations)
    )
    bkt_seq = (0, 500, 1000, 1500) if bucket_kj is None else tuple(bucket_kj)

    _validate_durations(dur_seq)

    if not bkt_seq or bkt_seq[0] != 0 or any(a >= b for a, b in pairwise(bkt_seq)):
        raise ValueError("bucket_kj must start at zero and strictly increase")

    active = filter_active(samples)
    cov = calculate_coverage(samples, "power_w", len(active))

    if not active or cov.fraction < 0.95:
        return DurabilityResult(
            available=False,
            reason="power durability requires at least 95% power coverage; all active power is required to place work safely",
            buckets=[],
        ).to_dict()

    all_rows = sorted(samples, key=lambda s: s["elapsed_s"])
    for previous, current in pairwise(all_rows):
        if current["elapsed_s"] > previous["elapsed_s"] + 1:
            return DurabilityResult(
                available=False,
                reason="unexplained elapsed gap hides unknown energy; supply inactive pause rows",
                buckets=[],
            ).to_dict()

    work = 0.0
    by_elapsed: Dict[int, float] = {}

    for s in active:
        p = s.get("power_w")
        if not _is_valid_value(p):
            return DurabilityResult(
                available=False,
                reason="missing power makes work bucket placement unreliable",
                buckets=[],
            ).to_dict()

        by_elapsed[s["elapsed_s"]] = work / 1000.0
        work += float(p)

    total_work_kj = work / 1000.0
    buckets: List[Dict[str, Any]] = []

    high_bounds = [float(b) for b in bkt_seq[1:]] + [float("inf")]

    for low, high in zip(bkt_seq, high_bounds, strict=True):
        low_f = float(low)
        high_f = float(high)

        exp_kj = max(0.0, min(total_work_kj, high_f) - low_f)
        exp_sec = sum(1 for val in by_elapsed.values() if low_f <= val < high_f)

        best_w_map: Dict[int, Optional[float]] = {}
        for d in dur_seq:
            best_avg: Optional[float] = None
            for end_sample, avg in rolling_windows(samples, "power_w", d):
                end_s = end_sample["elapsed_s"]
                start_s = end_s - d + 1

                start_work = by_elapsed.get(start_s)
                end_work = by_elapsed.get(end_s)

                if (
                    start_work is not None
                    and end_work is not None
                    and low_f <= start_work < high_f
                    and end_work < high_f
                ):
                    best_avg = max(best_avg or avg, avg)

            best_w_map[d] = best_avg

        row = DurabilityBucket(
            start_kj=low_f,
            end_kj=None if high_f == float("inf") else high_f,
            exposure_kj=exp_kj,
            exposure_seconds=exp_sec,
            best_w=best_w_map,
            change_percent={},
        ).to_dict()
        buckets.append(row)

    first_bucket = buckets[0]
    for row in buckets:
        chg_map: Dict[int, Optional[float]] = {}
        for d, val in row["best_w"].items():
            first_w = first_bucket["best_w"].get(d)
            if val is not None and first_w is not None and first_w > 0:
                chg_map[d] = 100.0 * (val - first_w) / first_w
            else:
                chg_map[d] = None
        row["change_percent"] = chg_map

    return DurabilityResult(
        available=True,
        confidence="low",
        buckets=buckets,
        total_work_kj=total_work_kj,
        exposure_seconds=len(active),
        coverage=cov.to_dict(),
        reason="observed effort selection and route are uncontrolled",
    ).to_dict()
