"""
Per-handler timing samples + aggregation.

Populated by LiveViewConsumer at every handler invocation (sync or
async, view-level or component-level). The MCP reads aggregated
percentiles via /_djust/observability/handler_timings/.
"""

from __future__ import annotations

import statistics
import threading
from collections import defaultdict, deque
from typing import Any, Dict, List, Optional, Tuple

_MAX_SAMPLES_PER_HANDLER = 100

# Map (view_class, handler_name) → deque of (timestamp_ms, duration_ms).
_samples: "defaultdict[Tuple[str, str], deque[Tuple[int, float]]]" = defaultdict(
    lambda: deque(maxlen=_MAX_SAMPLES_PER_HANDLER)
)
_lock = threading.Lock()


def record_handler_timing(view_class: str, handler_name: str, duration_ms: float) -> None:
    """Push a single handler execution sample onto the rolling window."""
    import time

    ts = int(time.time() * 1000)
    with _lock:
        _samples[(view_class, handler_name)].append((ts, float(duration_ms)))


def _percentile(values: List[float], pct: float) -> float:
    """Return the approximate pct (0..100) percentile. Uses linear
    interpolation between sorted samples — matches numpy's 'linear'
    default. Empty list → 0.0.
    """
    if not values:
        return 0.0
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * (pct / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    frac = k - lo
    return s[lo] + (s[hi] - s[lo]) * frac


def get_timing_stats(
    handler_name: Optional[str] = None,
    since_ms: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Aggregate samples into per-handler stats.

    Args:
        handler_name: If set, only return stats for handlers with this
            exact name. Otherwise return one row per (view_class,
            handler_name) pair seen.
        since_ms: If set, only include samples with timestamp > since_ms.
    """
    with _lock:
        snapshot = {k: list(v) for k, v in _samples.items()}

    rows = []
    for (view_class, h_name), sample_list in snapshot.items():
        if handler_name and h_name != handler_name:
            continue
        durations = [d for (ts, d) in sample_list if since_ms is None or ts > since_ms]
        if not durations:
            continue
        rows.append(
            {
                "view_class": view_class,
                "handler_name": h_name,
                "count": len(durations),
                "min_ms": +round(min(durations), 3),
                "max_ms": +round(max(durations), 3),
                "avg_ms": +round(statistics.fmean(durations), 3),
                "p50_ms": +round(_percentile(durations, 50), 3),
                "p90_ms": +round(_percentile(durations, 90), 3),
                "p99_ms": +round(_percentile(durations, 99), 3),
            }
        )
    # Slowest p90 first — surfaces the most-suspicious handlers.
    rows.sort(key=lambda r: r["p90_ms"], reverse=True)
    return rows


def get_sample_total() -> int:
    """Total samples across all handlers. Diagnostic helper."""
    with _lock:
        return sum(len(v) for v in _samples.values())


def _clear_timings() -> None:
    """Test-only reset."""
    with _lock:
        _samples.clear()
