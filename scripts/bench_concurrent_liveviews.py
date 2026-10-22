#!/usr/bin/env python3
"""Benchmark: many LiveView pages mounting and handling an event at once.

N WebSocket sessions connect to one in-process ``LiveViewConsumer``, then all
send ``mount`` at the same moment, then all send one event at the same moment.
The script reports each phase's latency (p50 / p95 / max) and wall time, next
to the latency of the same page alone, so the cost of sharing one process is
visible. ``--profile`` also counts the thread-sensitive ``sync_to_async`` hops
each phase makes and splits the time on the sync thread into queue wait and
run time.

The page is a list of ``--rows`` rows (default 300) rendered by djust's Rust
template engine; ``--query`` adds one database read to ``mount`` and to the
event handler, as an app's own data access would.

Run::

    python scripts/bench_concurrent_liveviews.py               # N=40
    python scripts/bench_concurrent_liveviews.py -n 80 --profile
    python scripts/bench_concurrent_liveviews.py --worker-threads 4
    python scripts/bench_concurrent_liveviews.py --cprofile 30

``--cprofile TOP`` runs the sync work inline on the loop thread (cProfile
follows one thread at a time) and prints the TOP functions by own time per
phase; ``BENCH_SORT=cumulative`` sorts by cumulative time instead.

Exit code is always 0: a measurement tool, not a gate (the regression tests
for what it found are in ``python/djust/tests/test_concurrent_liveview_cpu.py``).
"""

from __future__ import annotations

import argparse
import asyncio
import contextvars
import os
import statistics
import sys
import tempfile
import threading
import time
from collections import Counter
from typing import Any, Dict, List

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.join(ROOT, "python") not in sys.path:
    sys.path.insert(0, os.path.join(ROOT, "python"))

VIEW_MODULE = "djust_bench_concurrent_views"


def _configure(args: argparse.Namespace) -> None:
    import django
    from django.conf import settings

    db_path = os.path.join(tempfile.mkdtemp(prefix="djust-bench-"), "bench.sqlite3")
    liveview_config: Dict[str, Any] = {}
    if args.worker_threads:
        liveview_config["worker_threads"] = (
            int(args.worker_threads) if args.worker_threads.isdigit() else args.worker_threads
        )
    settings.configure(
        SECRET_KEY="bench-secret-key",
        DEBUG=False,
        ALLOWED_HOSTS=["*"],
        INSTALLED_APPS=[
            "django.contrib.contenttypes",
            "django.contrib.auth",
            "django.contrib.sessions",
            "channels",
            "djust",
        ],
        DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": db_path}},
        SESSION_ENGINE="django.contrib.sessions.backends.db",
        CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}},
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "DIRS": [],
                "APP_DIRS": True,
            }
        ],
        LIVEVIEW_ALLOWED_MODULES=[VIEW_MODULE],
        LIVEVIEW_CONFIG=liveview_config,
        USE_TZ=True,
        LOGGING_CONFIG=None,
    )
    django.setup()
    from django.core.management import call_command

    call_command("migrate", verbosity=0, run_syncdb=True)


def _install_views(rows: int, query: bool) -> None:
    """Define the benchmark view in an importable module (djust mounts by path)."""
    import types

    from djust import LiveView
    from djust.decorators import event_handler

    module = types.ModuleType(VIEW_MODULE)
    sys.modules[VIEW_MODULE] = module

    template = (
        f'<div dj-view="{VIEW_MODULE}.BenchView" dj-id="0">'
        "<header><h1>{{ title }}</h1><span>{{ count }} selected</span>"
        '<button dj-click="toggle_menu">{% if menu_open %}Close{% else %}Menu{% endif %}</button>'
        "</header>"
        '<ul class="rows">{% for row in rows %}'
        '<li class="row {% if row.active %}active{% endif %}" data-id="{{ row.id }}">'
        '<span class="name">{{ row.name|title }}</span>'
        '<span class="score">{{ row.score }}</span>'
        '<button dj-click="pick" data-id="{{ row.id }}">Pick</button>'
        "</li>{% endfor %}</ul></div>"
    )

    def _read_db() -> int:
        from django.contrib.sessions.models import Session

        return Session.objects.count()

    class BenchView(LiveView):
        def mount(self, request, **kwargs):
            self.title = "Bench"
            self.count = _read_db() if query else 0
            self.menu_open = False
            self.rows = [
                {"id": i, "name": f"row number {i}", "score": i * 7 % 101, "active": i % 3 == 0}
                for i in range(rows)
            ]

        @event_handler()
        def toggle_menu(self, **kwargs):
            if query:
                self.count = _read_db()
            self.menu_open = not self.menu_open

    BenchView.__module__ = VIEW_MODULE
    BenchView.template = template
    module.BenchView = BenchView


# --------------------------------------------------------------------------- #
# Hop profiler
# --------------------------------------------------------------------------- #


class HopProfile:
    """Counts thread-sensitive sync_to_async hops and their queue/run time."""

    def __init__(self, cprofile: bool = False) -> None:
        self.lock = threading.Lock()
        self.cprofile = cprofile
        self.reset()

    def reset(self) -> None:
        with self.lock:
            self.hops = 0
            self.queue_s = 0.0
            self.run_s = 0.0
            self.cpu_s = 0.0
            self.by_func: Counter = Counter()
            self.run_by_func: Counter = Counter()
            self.loop_profile: Any = None

    def install(self) -> None:
        from asgiref.sync import SyncToAsync

        profile = self
        original_call = SyncToAsync.__call__
        original_handler = SyncToAsync.thread_handler

        submitted_var: contextvars.ContextVar = contextvars.ContextVar("bench_submitted")

        async def __call__(self, *args, **kwargs):  # noqa: N807
            # asgiref copies the context at call time and runs the function
            # through context.run, so the submit time travels with it.
            token = submitted_var.set(time.perf_counter() if self._thread_sensitive else None)
            try:
                return await original_call(self, *args, **kwargs)
            finally:
                submitted_var.reset(token)

        def thread_handler(self, loop, exc_info, task_context, func, *args, **kwargs):
            ctx = getattr(func, "__self__", None)
            submitted = ctx.get(submitted_var, None) if ctx is not None else None
            if submitted is None:
                return original_handler(self, loop, exc_info, task_context, func, *args, **kwargs)
            start = time.perf_counter()
            cpu0 = time.thread_time()
            try:
                return original_handler(self, loop, exc_info, task_context, func, *args, **kwargs)
            finally:
                end = time.perf_counter()
                name = getattr(self.func, "__qualname__", None) or repr(self.func)
                with profile.lock:
                    profile.hops += 1
                    profile.queue_s += start - submitted
                    profile.run_s += end - start
                    profile.cpu_s += time.thread_time() - cpu0
                    profile.by_func[name] += 1
                    profile.run_by_func[name] += end - start

        if self.cprofile:
            # cProfile (sys.monitoring on 3.12+) profiles one thread at a time,
            # so run sync work inline on the loop thread. Under the GIL the CPU
            # cost per function is the same; the latencies are not, so the
            # cProfile run is for attribution only. Django refuses ORM calls
            # on a thread with a running loop unless told otherwise.
            os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"

            async def inline_call(self, *args, **kwargs):  # noqa: N807
                with profile.lock:
                    profile.hops += 1
                    name = getattr(self.func, "__qualname__", None) or repr(self.func)
                    profile.by_func[name] += 1
                return self.func(*args, **kwargs)

            SyncToAsync.__call__ = inline_call
            return
        SyncToAsync.__call__ = __call__
        SyncToAsync.thread_handler = thread_handler

    def start_loop_profile(self) -> None:
        if self.cprofile:
            import cProfile

            self.loop_profile = cProfile.Profile()
            self.loop_profile.enable()

    def print_cprofile(self, label: str, top: int) -> None:
        if not self.cprofile:
            return
        import pstats

        self.loop_profile.disable()
        print(f"  --- cProfile {label} (sync work inlined on the loop; by tottime) ---")
        pstats.Stats(self.loop_profile, stream=sys.stdout).sort_stats(
            os.environ.get("BENCH_SORT", "tottime")
        ).print_stats(top)

    def report(self, label: str, sessions: int) -> None:
        print(
            f"  {label}: {self.hops} hops ({self.hops / max(sessions, 1):.1f}/session), "
            f"run {self.run_s * 1000:.0f} ms (cpu {self.cpu_s * 1000:.0f} ms), "
            f"queue wait {self.queue_s * 1000:.0f} ms summed"
        )
        for name, count in self.by_func.most_common():
            print(
                f"      {count / max(sessions, 1):5.1f}/session  "
                f"{self.run_by_func[name] * 1000 / count:7.2f} ms each  {name}"
            )


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #


async def _receive_type(comm, wanted: str, timeout: float = 120.0) -> dict:
    while True:
        frame = await comm.receive_json_from(timeout=timeout)
        if frame.get("type") == wanted:
            return frame
        if frame.get("type") == "error":
            raise RuntimeError(f"server error frame: {frame!r}")


async def _connect(n: int) -> List[Any]:
    from asgiref.sync import sync_to_async
    from channels.testing import WebsocketCommunicator
    from django.contrib.sessions.backends.db import SessionStore

    from djust.websocket import LiveViewConsumer

    def _create_session() -> str:
        store = SessionStore()
        store.create()
        return store.session_key

    class _ScopeSession:
        def __init__(self, key: str) -> None:
            self.session_key = key

    app = LiveViewConsumer.as_asgi()
    comms = []
    for _ in range(n):
        key = await sync_to_async(_create_session)()
        comm = WebsocketCommunicator(
            app, "/ws/live/", headers=[(b"host", b"localhost"), (b"origin", b"http://localhost")]
        )
        comm.scope["session"] = _ScopeSession(key)
        connected, _ = await comm.connect()
        assert connected, "WebSocket did not connect"
        await comm.receive_json_from(timeout=10)  # connect frame
        comms.append(comm)
    return comms


async def _mount(comm) -> float:
    start = time.perf_counter()
    await comm.send_json_to({"type": "mount", "view": f"{VIEW_MODULE}.BenchView", "url": "/b/"})
    await _receive_type(comm, "mount")
    return time.perf_counter() - start


async def _event(comm, ref: int) -> float:
    start = time.perf_counter()
    await comm.send_json_to({"type": "event", "event": "toggle_menu", "params": {}, "ref": ref})
    await _receive_type(comm, "patch")
    return time.perf_counter() - start


def _summary(samples: List[float]) -> str:
    ordered = sorted(samples)

    def pct(p: float) -> float:
        return ordered[min(len(ordered) - 1, int(round(p / 100 * (len(ordered) - 1))))] * 1000

    return f"p50 {pct(50):7.1f} ms  p95 {pct(95):7.1f} ms  max {ordered[-1] * 1000:7.1f} ms"


async def _run(args: argparse.Namespace, profile: HopProfile | None) -> Dict[str, Any]:
    results: Dict[str, Any] = {}

    # Warm up (template compile, imports, first DB connection).
    warm = await _connect(2)
    for c in warm:
        await _mount(c)
        await _event(c, 1)
    for c in warm:
        await c.disconnect()

    # One page alone.
    solo_mount, solo_event = [], []
    for _ in range(args.solo):
        (c,) = await _connect(1)
        solo_mount.append(await _mount(c))
        solo_event.append(await _event(c, 1))
        await c.disconnect()
    results["solo_mount"] = statistics.median(solo_mount)
    results["solo_event"] = statistics.median(solo_event)
    print(f"one page alone (median of {args.solo}):")
    print(
        f"  mount {results['solo_mount'] * 1000:.1f} ms   event {results['solo_event'] * 1000:.1f} ms"
    )

    comms = await _connect(args.n)

    if profile:
        profile.reset()
        profile.start_loop_profile()
    cpu0 = time.process_time()
    wall0 = time.perf_counter()
    mounts = await asyncio.gather(*(_mount(c) for c in comms))
    mount_wall = time.perf_counter() - wall0
    mount_cpu = time.process_time() - cpu0
    print(f"{args.n} pages at once:")
    print(
        f"  mount  {_summary(mounts)}  wall {mount_wall * 1000:7.1f} ms  cpu {mount_cpu * 1000:.0f} ms"
    )
    if profile:
        profile.report("mount", args.n)
        profile.print_cprofile("mount", args.cprofile_top)
        profile.reset()
        profile.start_loop_profile()

    cpu0 = time.process_time()
    wall0 = time.perf_counter()
    events = await asyncio.gather(*(_event(c, 2) for c in comms))
    event_wall = time.perf_counter() - wall0
    event_cpu = time.process_time() - cpu0
    print(
        f"  event  {_summary(events)}  wall {event_wall * 1000:7.1f} ms  cpu {event_cpu * 1000:.0f} ms"
    )
    if profile:
        profile.report("event", args.n)
        profile.print_cprofile("event", args.cprofile_top)

    for c in comms:
        await c.disconnect()
    results.update(mounts=mounts, events=events, mount_wall=mount_wall, event_wall=event_wall)
    return results


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("-n", type=int, default=40, help="concurrent pages (default 40)")
    parser.add_argument("--rows", type=int, default=300, help="rows rendered per page")
    parser.add_argument("--query", action="store_true", help="one DB read per mount/event")
    parser.add_argument("--solo", type=int, default=5, help="single-page samples")
    parser.add_argument("--profile", action="store_true", help="count sync_to_async hops")
    parser.add_argument(
        "--worker-threads", default="", help="LIVEVIEW_CONFIG['worker_threads'] (e.g. 4, auto)"
    )
    parser.add_argument(
        "--cprofile",
        type=int,
        default=0,
        metavar="TOP",
        help="with --profile: print the TOP functions by own time, per thread",
    )
    args = parser.parse_args(argv)
    args.cprofile_top = args.cprofile
    if args.cprofile:
        args.profile = True

    _configure(args)
    _install_views(args.rows, args.query)
    profile = None
    if args.profile:
        profile = HopProfile(cprofile=bool(args.cprofile))
        profile.install()
    from djust import __file__ as djust_file

    print(f"djust from {os.path.dirname(djust_file)}")
    print(
        f"python {sys.version.split()[0]}  n={args.n}  rows={args.rows}  query={args.query}  "
        f"worker_threads={args.worker_threads or 'off'}"
    )
    asyncio.run(_run(args, profile))
    return 0


if __name__ == "__main__":
    sys.exit(main())
