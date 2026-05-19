#!/usr/bin/env python3
"""Benchmark: ``asgiref.sync.sync_to_async`` round-trip overhead — issue #1434.

djust's async paths (``websocket.py``, ``sse.py``, ``runtime.py``,
``streaming.py``) bridge into sync framework code with ``sync_to_async``.
Issue #1434 proposed replacing ``sync_to_async(Model.objects.X)`` call sites
with the native Django async ORM and *estimated* the per-call cost at
~50-200 µs. This script measures that cost empirically so
``docs/audits/async-orm-2026-05.md`` can cite a real number, and applies it
to the per-event crossing budget the audit established.

No Django setup is required: ``sync_to_async`` overhead is a property of
asgiref's threadpool dispatch, independent of any Django model.

Run::

    python scripts/bench_sync_to_async_overhead.py

Exit code is always 0 — this is a measurement tool, not a gate.
"""

from __future__ import annotations

import asyncio
import statistics
import sys
import time

from asgiref.sync import sync_to_async

ITERS = 2000
WARMUP = 200

# Per-event sync_to_async crossing budget for a representative LiveView event
# (a counter-increment ``dj-click``), taken from the call-site classification
# in docs/audits/async-orm-2026-05.md. The per-event hot path crosses
# sync_to_async once for the user @event_handler (CALLBACK) and a small
# number of times for Rust-extension calls (_sync_state_to_rust,
# render_with_diff, and HTML strip/extract). It crosses zero times for ORM or
# cache work — the three ORM-category sites (check_view_auth, _ensure_tenant,
# check_object_permission) all fire once per *connection* at mount, never
# per event.
EVENT_CROSSINGS = {"CALLBACK": 1, "RUST": 4, "ORM": 0, "CACHE": 0}


def _noop() -> None:
    return None


def _percentile(samples: list[float], pct: float) -> float:
    ordered = sorted(samples)
    idx = int(round((pct / 100.0) * (len(ordered) - 1)))
    return ordered[idx]


def _measure_direct() -> list[float]:
    for _ in range(WARMUP):
        _noop()
    samples = []
    for _ in range(ITERS):
        start = time.perf_counter()
        _noop()
        samples.append((time.perf_counter() - start) * 1e6)
    return samples


async def _measure_sync_to_async(*, thread_sensitive: bool) -> list[float]:
    wrapped = sync_to_async(_noop, thread_sensitive=thread_sensitive)
    for _ in range(WARMUP):
        await wrapped()
    samples = []
    for _ in range(ITERS):
        start = time.perf_counter()
        await wrapped()
        samples.append((time.perf_counter() - start) * 1e6)
    return samples


def _report(label: str, samples: list[float]) -> float:
    median = statistics.median(samples)
    p99 = _percentile(samples, 99.0)
    mean = statistics.fmean(samples)
    print(f"  {label:<40s}  median={median:8.2f} us   p99={p99:9.2f} us   mean={mean:8.2f} us")
    return median


async def main() -> int:
    gil_probe = getattr(sys, "_is_gil_enabled", None)
    ft = "free-threaded" if gil_probe is not None and not gil_probe() else "GIL"
    print(f"sync_to_async round-trip overhead  ({ITERS} iters, {WARMUP} warmup)")
    print(f"Python {sys.version.split()[0]} [{ft}], asgiref measurement, no Django\n")

    direct = _measure_direct()
    ts_true = await _measure_sync_to_async(thread_sensitive=True)
    ts_false = await _measure_sync_to_async(thread_sensitive=False)

    print("Raw measurements:")
    direct_med = _report("direct sync call (baseline noop)", direct)
    true_med = _report("sync_to_async(thread_sensitive=True)", ts_true)
    false_med = _report("sync_to_async(thread_sensitive=False)", ts_false)

    per_crossing = true_med - direct_med
    print("\nPer-crossing overhead (median, minus direct-call baseline):")
    print(
        f"  thread_sensitive=True    {per_crossing:8.2f} us"
        "   <- djust default / database_sync_to_async"
    )
    print(f"  thread_sensitive=False   {false_med - direct_med:8.2f} us")

    total = sum(EVENT_CROSSINGS.values()) * per_crossing
    migratable = (EVENT_CROSSINGS["ORM"] + EVENT_CROSSINGS["CACHE"]) * per_crossing
    pct = 0.0 if total == 0 else 100.0 * migratable / total
    print("\nRepresentative LiveView event (counter increment) — crossing budget:")
    print(f"  crossings/event:         {EVENT_CROSSINGS}")
    print(f"  total threadpool cost:   {total:8.2f} us/event")
    print(f"  ORM/cache-migratable:    {migratable:8.2f} us/event  ({pct:.1f}%)")

    print(
        "\nConclusion: 0 of the per-event sync_to_async crossings are ORM or "
        "cache.\nThe native-async-ORM migration #1434 envisioned has no "
        "hot-path surface;\nthe 3 ORM-category sites are mount/connection-time "
        "auth + tenant helpers.\n"
        f"SUMMARY per_crossing_us={per_crossing:.1f} "
        f"event_migratable_us={migratable:.1f} event_migratable_pct={pct:.1f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
