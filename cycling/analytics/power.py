"""Power analytics: Rolling power, Normalized Power (NP), Intensity Factor (IF), PDC/MMP with timestamps, and FTP estimate.

Formulas:
---------
1. Complete Contiguous 30-second Window Rolling Average:
   For every valid 30-second contiguous sub-sequence [P_0, ..., P_29]:
   P_30s = (1 / 30) * sum_{i=0}^{29} P_i

2. Normalized Power (NP):
   NP = ( (1 / N) * sum_{k=1}^{N} (P_30s_k)^4 )^(1/4)
   where N is the number of complete contiguous 30-second rolling windows.
   If N == 0 (e.g. activity shorter than 30s or missing power), NP is None.

3. Intensity Factor (IF):
   IF = NP / FTP
   where FTP is the athlete's functional threshold power in watts.
   If NP is None, or FTP is None or <= 0, IF is None.

4. Power Curve / Mean Maximal Power (MMP) with Timestamps:
   For a requested duration d (seconds):
   MMP(d) = max_{complete contiguous windows of size d} ( (1 / d) * sum_{i=0}^{d-1} P_i )
   Timestamps:
   - end_s: elapsed_s of the final sample in the winning window
   - start_s: elapsed_s of the first sample in the winning window (end_s - d + 1)
   If no complete contiguous window of size d exists, MMP(d) is None.

5. Threshold Estimate (20-minute Heuristic):
   FTP_estimate = 0.95 * MMP(1200s)
"""

from typing import Any, Dict, Optional, Sequence, Tuple

from cycling.analytics.rolling import rolling_windows
from cycling.analytics.types import MMPPoint


def _validate_durations(durations: Sequence[int]) -> None:
    if any(not isinstance(d, int) or isinstance(d, bool) or d < 1 for d in durations):
        raise ValueError("durations must be positive integer seconds")


def calculate_np(samples: Sequence[Dict[str, Any]]) -> Tuple[Optional[float], int]:
    """Calculate Normalized Power (NP) and the count of complete eligible 30s windows.

    Returns:
        (np_w, eligible_window_count)
    """
    np_values = [avg for _, avg in rolling_windows(samples, "power_w", 30)]
    if not np_values:
        return None, 0
    fourth_power_avg = sum(v**4 for v in np_values) / len(np_values)
    return fourth_power_avg**0.25, len(np_values)


def calculate_if(np_w: Optional[float], ftp_w: Optional[float]) -> Optional[float]:
    """Calculate Intensity Factor (IF = NP / FTP)."""
    if np_w is None or ftp_w is None or ftp_w <= 0:
        return None
    return np_w / ftp_w


def power_curve_detailed(
    samples: Sequence[Dict[str, Any]], durations: Sequence[int]
) -> Dict[int, MMPPoint]:
    """Calculate Mean Maximal Power (MMP) curve with exact start and end timestamps."""
    _validate_durations(durations)
    result: Dict[int, MMPPoint] = {}

    for duration in durations:
        best_w: Optional[float] = None
        best_end_sample: Optional[Dict[str, Any]] = None

        for end_sample, avg in rolling_windows(samples, "power_w", duration):
            if best_w is None or avg > best_w:
                best_w = avg
                best_end_sample = end_sample

        if best_w is not None and best_end_sample is not None:
            end_s = best_end_sample["elapsed_s"]
            start_s = end_s - duration + 1
            result[duration] = MMPPoint(
                duration_s=duration, max_w=best_w, start_s=start_s, end_s=end_s
            )
        else:
            result[duration] = MMPPoint(
                duration_s=duration, max_w=None, start_s=None, end_s=None
            )

    return result


def power_curve(
    samples: Sequence[Dict[str, Any]], durations: Sequence[int]
) -> Dict[int, Optional[float]]:
    """Calculate power curve mapping duration -> max_w for API compatibility."""
    detailed = power_curve_detailed(samples, durations)
    return {d: pt.max_w for d, pt in detailed.items()}


def threshold_estimate(samples: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Estimate threshold power from 95% of 20-minute max power."""
    curve = power_curve(samples, [1200])
    best_20m = curve[1200]
    return {
        "available": best_20m is not None,
        "observed_20m_w": best_20m,
        "watts": 0.95 * best_20m if best_20m is not None else None,
        "reason": "95% of best observed 20-minute power; low-confidence estimate",
    }
