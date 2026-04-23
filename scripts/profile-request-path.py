#!/usr/bin/env python
"""
Profile the djust request path with cProfile.

Runs a representative workload against the four request-path segments
profiled in ``tests/benchmarks/test_request_path.py``:

1. Mount                 — LiveView HTTP render
2. Event dispatch        — event -> handler -> render via RustLiveView
3. VDOM diff + patch     — render_with_diff cycle
4. List reorder          — 50-item keyed list shuffle + diff

Timings are printed per segment and the full cProfile pstats output is
written to ``artifacts/profile-<timestamp>.txt`` (and an accompanying
``.pstats`` binary for interactive analysis with
``python -m pstats artifacts/profile-<timestamp>.pstats``).

Usage:
    .venv/bin/python scripts/profile-request-path.py                 # default: 1000 events
    .venv/bin/python scripts/profile-request-path.py --events 5000   # custom load
    .venv/bin/python scripts/profile-request-path.py --pyspy         # emit py-spy cmd

This is a dev-only tool. print() is fine here (ClaudeMd: "Profiling
script is dev-only, so print() is fine there").
"""

from __future__ import annotations

import argparse
import cProfile
import io
import os
import pstats
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
ARTIFACTS = ROOT / "artifacts"
DJANGO_SETTINGS_MODULE = "demo_project.settings"


def _bootstrap_django() -> None:
    """Configure sys.path and Django settings before importing djust."""
    # examples/demo_project is on sys.path for pytest; mirror that for scripts.
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "examples" / "demo_project"))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", DJANGO_SETTINGS_MODULE)

    import django

    django.setup()


COUNTER_TEMPLATE = """
<div id="counter" dj-id="0">
    <h1 dj-id="1">Counter: {{ count }}</h1>
    <button dj-id="2" dj-click="increment">+</button>
    <button dj-id="3" dj-click="decrement">-</button>
</div>
"""

LIST_TEMPLATE = """
<ul id="items" dj-id="0">
    {% for item in items %}
    <li id="item-{{ item.id }}" dj-id="li-{{ item.id }}">
        <span dj-id="txt-{{ item.id }}">{{ item.text }}</span>
    </li>
    {% endfor %}
</ul>
"""


def segment_mount(n_iterations: int) -> float:
    """Profile LiveView HTTP render (mount + render) cycle.

    Returns the mean time in seconds.
    """
    from djust import LiveView

    class _MountCounter(LiveView):
        template = COUNTER_TEMPLATE

        def mount(self, request, **kwargs):
            self.count = 0

    total = 0.0
    for _ in range(n_iterations):
        view = _MountCounter()
        view._initialize_temporary_assigns()
        t0 = time.perf_counter()
        view.mount(request=None)
        view.render()
        total += time.perf_counter() - t0
    return total / max(n_iterations, 1)


def segment_event_dispatch(n_iterations: int) -> float:
    """Profile event -> handler -> render_with_diff via RustLiveView."""
    from djust._rust import RustLiveView

    view = RustLiveView(COUNTER_TEMPLATE, [])
    view.update_state({"count": 0})
    view.render_with_diff()  # warm baseline

    total = 0.0
    count = 0
    for _ in range(n_iterations):
        count += 1
        t0 = time.perf_counter()
        view.update_state({"count": count})
        view.render_with_diff()
        total += time.perf_counter() - t0
    return total / max(n_iterations, 1)


def segment_vdom_patch(n_iterations: int) -> float:
    """Profile VDOM diff + patch generation for single-node updates."""
    from djust._rust import RustLiveView

    view = RustLiveView(COUNTER_TEMPLATE, [])
    view.update_state({"count": 0})
    view.render_with_diff()

    total = 0.0
    for i in range(n_iterations):
        view.update_state({"count": i + 1})
        t0 = time.perf_counter()
        view.render_with_diff()
        total += time.perf_counter() - t0
    return total / max(n_iterations, 1)


def segment_list_reorder(n_iterations: int, list_size: int = 50) -> float:
    """Profile keyed list reorder diff (50-item default)."""
    from djust._rust import RustLiveView

    view = RustLiveView(LIST_TEMPLATE, [])
    items = [{"id": i, "text": f"Item {i}"} for i in range(list_size)]
    view.update_state({"items": items})
    view.render_with_diff()

    rng = random.Random(0xD1057)
    total = 0.0
    for _ in range(n_iterations):
        rng.shuffle(items)
        t0 = time.perf_counter()
        view.update_state({"items": items})
        view.render_with_diff()
        total += time.perf_counter() - t0
    return total / max(n_iterations, 1)


def run_workload(n_events: int) -> dict:
    """Execute the full workload and return per-segment mean timings."""
    # 1 mount per N events is realistic; scale down to keep total runtime reasonable.
    n_mount = max(10, n_events // 100)
    n_list = max(20, n_events // 50)

    print(f"[profile] mount          x{n_mount}")
    mount_mean = segment_mount(n_mount)

    print(f"[profile] event dispatch x{n_events}")
    event_mean = segment_event_dispatch(n_events)

    print(f"[profile] vdom patch     x{n_events}")
    patch_mean = segment_vdom_patch(n_events)

    print(f"[profile] list reorder   x{n_list}")
    list_mean = segment_list_reorder(n_list)

    return {
        "mount": mount_mean,
        "event_dispatch": event_mean,
        "vdom_patch": patch_mean,
        "list_reorder": list_mean,
    }


def write_report(report_path: Path, pstats_path: Path, timings: dict, n_events: int) -> None:
    """Write a human-readable text report with segment timings + pstats."""
    ARTIFACTS.mkdir(parents=True, exist_ok=True)

    buf = io.StringIO()
    buf.write("djust v0.6.0 request-path profile\n")
    buf.write(f"Generated: {datetime.now(timezone.utc).isoformat()}\n")
    buf.write(f"Workload: {n_events} events + derived mount/list counts\n")
    buf.write(f"Python: {sys.version.split()[0]}\n")
    buf.write(f"Platform: {sys.platform}\n")
    buf.write("\n")
    buf.write("Segment timings (mean per operation):\n")
    buf.write(f"  Mount (HTTP render)     : {timings['mount'] * 1000:.3f} ms\n")
    buf.write(f"  Event dispatch          : {timings['event_dispatch'] * 1000:.3f} ms\n")
    buf.write(f"  VDOM patch              : {timings['vdom_patch'] * 1000:.3f} ms\n")
    buf.write(f"  List reorder (50 items) : {timings['list_reorder'] * 1000:.3f} ms\n")
    buf.write("\n")
    buf.write("Targets (v0.6.0):\n")
    buf.write("  per-event    < 2 ms  (mount / event / patch)\n")
    buf.write("  list-update  < 5 ms  (list operations)\n")
    buf.write("\n")

    # Attach top cProfile lines by cumulative time.
    buf.write("cProfile top 30 by cumulative time:\n")
    buf.write("-" * 78 + "\n")
    stats = pstats.Stats(str(pstats_path))
    stats.strip_dirs()
    stats.sort_stats("cumulative")
    stats.stream = buf
    stats.print_stats(30)

    report_path.write_text(buf.getvalue(), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--events",
        type=int,
        default=1000,
        help="Number of events per segment (default: 1000)",
    )
    parser.add_argument(
        "--pyspy",
        action="store_true",
        help="Print the py-spy command to run this script under a sampling profiler",
    )
    return parser.parse_args()


def emit_pyspy_hint() -> None:
    """Print a one-liner the user can run to capture a py-spy flamegraph."""
    print("To capture a flamegraph with py-spy (install via `pip install py-spy`):")
    print(
        "  py-spy record -o artifacts/profile.svg -r 200 -- "
        ".venv/bin/python scripts/profile-request-path.py"
    )
    print("py-spy is optional; this script works standalone via cProfile.")


def main() -> int:
    args = parse_args()
    if args.pyspy:
        emit_pyspy_hint()
        return 0

    _bootstrap_django()

    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    pstats_path = ARTIFACTS / f"profile-{ts}.pstats"
    report_path = ARTIFACTS / f"profile-{ts}.txt"

    profiler = cProfile.Profile()
    profiler.enable()
    timings = run_workload(args.events)
    profiler.disable()
    profiler.dump_stats(str(pstats_path))

    write_report(report_path, pstats_path, timings, args.events)

    print()
    print("=" * 60)
    print("Request-path profile")
    print("=" * 60)
    print(f"Mount (HTTP render)     : {timings['mount'] * 1000:.3f} ms")
    print(f"Event dispatch          : {timings['event_dispatch'] * 1000:.3f} ms")
    print(f"VDOM patch              : {timings['vdom_patch'] * 1000:.3f} ms")
    print(f"List reorder (50 items) : {timings['list_reorder'] * 1000:.3f} ms")
    print()
    print(f"Report : {report_path}")
    print(f"pstats : {pstats_path}")

    # Hot-spot exit code — non-zero if any segment exceeded its target so
    # CI invocations can fail-fast when a regression lands.
    per_event_target = 0.002
    list_target = 0.005
    hot = []
    if timings["mount"] > per_event_target:
        hot.append("mount")
    if timings["event_dispatch"] > per_event_target:
        hot.append("event_dispatch")
    if timings["vdom_patch"] > per_event_target:
        hot.append("vdom_patch")
    if timings["list_reorder"] > list_target:
        hot.append("list_reorder")
    if hot:
        print(f"HOT SPOTS: {', '.join(hot)} exceeded target(s)")
        return 1
    print("OK: all segments within target bounds")
    return 0


if __name__ == "__main__":
    sys.exit(main())
