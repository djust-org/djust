#!/usr/bin/env python3
"""Benchmark: complete event turns over WebSocket, SSE and the HTTP fallback (#3657).

The baseline later performance work measures against. Each turn goes through
the real stack, as a browser's client drives it: a page GET through the
project's middleware and URLconf (``djust.testing.open_live_page``), then the
event over one transport, and the timer stops when the frame (or response)
that answers it is back:

``ws``    ``LiveViewConsumer`` over a ``WebsocketCommunicator`` (no ASGI
          middleware: the consumer gets the page's session and user).
``sse``   the SSE stream GET mounts the view; each event is a POST to the
          message URL and the answer arrives on the stream.
``http``  the page-POST fallback: one JSON POST per event, answered with
          patches (the render digest is sent, as the client sends it).

Five workloads (``--shapes``), all explicit exposure with strict parameters:

``form``       six validated fields; each turn edits one and re-validates.
``table``      ``--rows`` rows of dicts (default 1,000); one cell changes.
``dashboard``  24 scalars, a list of 20 records, a dict and two components;
               one scalar changes.
``push``       an async handler that sends an intermediate render with
               ``push_state()`` and then finishes (two renders per turn; no
               HTTP row, since the fallback has no live channel).
``editor``     a ~4 KB text area; each turn sends the whole text, as an
               ``dj-input`` does, and a derived word count re-renders.

Two save modes (``--modes``): ``save`` is the shipped default (the view's
state is saved in the server-side session after each turn); ``nosave`` is
``automatic_state = False`` (no save; the HTTP fallback then runs ``mount()``
on every POST, as documented).

Simulated round-trip time (``--rtt``, ms): each frame the client sends is
held for RTT/2 before it reaches the server, and each answer for RTT/2 after
the server sends it, on a kept-alive connection. Nothing else is modelled
(no TLS or TCP handshakes, no bandwidth limit).

Per cell: p50 / p95 turn latency, CPU per turn (``process_time``: client and
server share this process, so it includes the in-process client's own work),
event-loop lag (a 2 ms ticker on the loop; p95 and max overshoot), and wire
bytes per turn (the event's body up, the answer's JSON down; no headers). A
separate pass (``--memory``) measures with ``tracemalloc`` the memory one open
page retains after a few turns (view, session, transport state).

The app does no database or API work of its own: what is measured is the
framework (and the session save, which is the framework's). Browser-side
patch and input time is not measured here.

Run::

    make benchmark-transports                       # full baseline (a few minutes)
    python scripts/bench_turn_transports.py --rounds 40 --json out.json
    python scripts/bench_turn_transports.py --compare docs/performance/turn-transports-baseline.json
    python scripts/bench_turn_transports.py --quick  # seconds; what the test runs

A RELEASE ``_rust`` build is required for meaningful numbers. Rounds rotate
over the transports and modes (one turn each per round), so drift on a busy
machine lands on every cell alike. Exit code is always 0 unless a turn fails:
a measurement tool, not a gate.
"""

from __future__ import annotations

import argparse
import asyncio
import gc
import json
import os
import platform
import shutil
import statistics
import sys
import tempfile
import time
import tracemalloc
from typing import Any, Dict, List, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.join(ROOT, "python") not in sys.path:
    sys.path.insert(0, os.path.join(ROOT, "python"))

VIEW_MODULE = "djust_bench_turn_views"
URL_MODULE = "djust_bench_turn_urls"
SHAPES = ("form", "table", "dashboard", "push", "editor")
TRANSPORTS = ("ws", "sse", "http")
MODES = ("save", "nosave")
WARMUP = 3
#: Transports a shape does not run on, and why (printed in the table).
NOT_APPLICABLE = {("push", "http"): "the HTTP fallback has no live channel for push_state()"}


# --------------------------------------------------------------------------- #
# Django setup
# --------------------------------------------------------------------------- #


def _configure() -> str:
    """Configure Django on a fresh SQLite file; returns its directory (``main``
    removes it at the end of the run)."""
    import django
    from django.conf import settings

    tmp_dir = tempfile.mkdtemp(prefix="djust-bench-turn-")
    db_path = os.path.join(tmp_dir, "bench.sqlite3")
    settings.configure(
        SECRET_KEY="bench-secret-key-not-for-production-0123456789",
        DEBUG=False,
        ALLOWED_HOSTS=["*"],
        INSTALLED_APPS=[
            "django.contrib.contenttypes",
            "django.contrib.auth",
            "django.contrib.sessions",
            "channels",
            "djust",
        ],
        MIDDLEWARE=[
            "django.contrib.sessions.middleware.SessionMiddleware",
            "django.middleware.csrf.CsrfViewMiddleware",
            "django.contrib.auth.middleware.AuthenticationMiddleware",
        ],
        ROOT_URLCONF=URL_MODULE,
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
        # A benchmark sends events back to back; lift the per-socket rate limit.
        LIVEVIEW_CONFIG={"rate_limit": {"rate": 10**6, "burst": 10**6, "max_warnings": 10**6}},
        USE_TZ=True,
        LOGGING_CONFIG=None,
    )
    django.setup()
    from django.core.management import call_command

    call_command("migrate", verbosity=0, run_syncdb=True)
    return tmp_dir


# --------------------------------------------------------------------------- #
# The views
# --------------------------------------------------------------------------- #

FORM_FIELDS = ("name", "email", "age", "city", "phone", "notes")
SCALARS = ["s%02d" % i for i in range(24)]


def _scalar_default(i: int) -> Any:
    return ("value %d" % i, i, i * 1.5, i % 2 == 0)[i % 4]


def _validate(field: str, value: str) -> str:
    if not value:
        return "required"
    if field == "email" and "@" not in value:
        return "not an email address"
    if field == "age" and not value.isdigit():
        return "not a number"
    return ""


def _install_views(rows: int, table_body: str = "flat") -> Dict[Tuple[str, str], type]:
    """Define the views in an importable module (djust mounts by dotted path).

    ``table_body="nested"`` gives each table row a ``tags`` list rendered by a
    nested ``{% for %}`` (#4325), so the row body exercises the per-item
    store's nested-loop path; the ``table`` cell keeps its name and event."""
    import types

    from django.urls import include, path

    from djust import LiveView, state
    from djust.components.descriptors.base import LiveComponent, TypedState
    from djust.decorators import event_handler
    from djust.sse import sse_urlpatterns

    module = types.ModuleType(VIEW_MODULE)
    sys.modules[VIEW_MODULE] = module

    class FormBody(LiveView):
        template = (
            '<div dj-root><form dj-submit="save">'
            + "".join(
                '<label>%s<input name="%s" value="{{ values.%s }}" dj-input="validate">'
                '<span class="err">{{ errors.%s }}</span></label>' % (f, f, f, f)
                for f in FORM_FIELDS
            )
            + "<button {% if invalid %}disabled{% endif %}>Save</button></form></div>"
        )
        values = state(default_factory=dict)
        errors = state(default_factory=dict)

        def mount(self, request, **kwargs):
            self.values = {f: "" for f in FORM_FIELDS}
            self.errors = {}

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["invalid"] = any(self.errors.values()) or not all(self.values.values())
            return context

        @event_handler()
        def validate(self, field: str = "", value: str = ""):
            if field in self.values:
                self.values[field] = value
                self.errors[field] = _validate(field, value)

    nested_cell = (
        "<td>{% for t in row.tags %}<i>{{ t }}</i>{% endfor %}</td>"
        if table_body == "nested"
        else ""
    )

    class TableBody(LiveView):
        template = (
            "<div dj-root><h1>{{ title }}</h1><table>"
            '{% for row in items %}<tr id="row-{{ row.id }}"><td>{{ row.sku }}</td>'
            "<td>{{ row.name }}</td><td>{{ row.price }}</td><td>{{ row.qty }}</td>"
            "<td>{% if row.active %}on{% else %}off{% endif %}</td>"
            + nested_cell
            + "</tr>{% endfor %}</table></div>"
        )
        title = state("")
        items = state(default_factory=list)

        def mount(self, request, **kwargs):
            self.title = "Inventory"
            self.items = [
                {
                    "id": i,
                    "sku": "SKU-%05d" % i,
                    "name": "Catalogue item number %d" % i,
                    "price": round(i * 3.75, 2),
                    "qty": i % 7,
                    "active": i % 3 != 0,
                    **({"tags": ["alpha", "t%d" % (i % 5)]} if nested_cell else {}),
                }
                for i in range(rows)
            ]

        @event_handler()
        def bump(self, **kwargs):
            self.items[len(self.items) // 2]["qty"] += 1

    class Tabs(LiveComponent):
        class State(TypedState):
            active: str = "overview"

        template = "<nav>{{ active }}</nav>"

    class Badge(LiveComponent):
        class State(TypedState):
            label: str = "new"
            n: int = 0

        template = "<em>{{ label }} {{ n }}</em>"

    class DashboardBody(LiveView):
        template = (
            "<div dj-root>"
            + "".join("<b>{{ %s }}</b>" % name for name in SCALARS)
            + "{{ tabs }}{{ badge }}"
            "<ul>{% for p in products %}<li>{{ p.name }} {{ p.price }} {{ p.stock }}</li>"
            "{% endfor %}</ul><i>{{ filters.category }} {{ filters.page }}</i></div>"
        )
        products = state(default_factory=list)
        filters = state(default_factory=dict)
        tabs = Tabs()
        badge = Badge()
        locals().update({name: state(_scalar_default(i)) for i, name in enumerate(SCALARS)})

        def mount(self, request, **kwargs):
            self.products = [
                {"name": "Product %d" % i, "price": "%d.99" % i, "stock": i * 3} for i in range(20)
            ]
            self.filters = {"category": "tools", "page": 1, "sort": "name"}

        @event_handler()
        def tick(self, **kwargs):
            self.s01 += 1

    class PushBody(LiveView):
        template = '<div dj-root><p id="status">{{ status }}</p><p>{{ n }}</p></div>'
        status = state("idle")
        n = state(0)

        @event_handler()
        async def work(self, **kwargs):
            self.status = "working"
            await self.push_state()
            self.n += 1
            self.status = "done"

    class EditorBody(LiveView):
        template = (
            '<div dj-root><textarea dj-input="edit">{{ text }}</textarea>'
            "<p>{{ words }} words, {{ text|length }} characters</p>"
            "<pre>{{ text|truncatechars:400 }}</pre></div>"
        )
        text = state("")

        def mount(self, request, **kwargs):
            self.text = EDITOR_TEXT

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["words"] = len(self.text.split())
            return context

        @event_handler()
        def edit(self, value: str = ""):
            self.text = value

    bodies = {
        "form": FormBody,
        "table": TableBody,
        "dashboard": DashboardBody,
        "push": PushBody,
        "editor": EditorBody,
    }
    views: Dict[Tuple[str, str], type] = {}
    patterns = []
    for shape, body in bodies.items():
        for mode in MODES:
            name = "%s_%s" % (shape.capitalize(), mode)
            attrs = {"__module__": VIEW_MODULE, "automatic_state": mode == "save"}
            cls = type(name, (body,), attrs)
            setattr(module, name, cls)
            views[(shape, mode)] = cls
            patterns.append(path("%s-%s/" % (shape, mode), cls.as_view()))
    for helper in (FormBody, TableBody, Tabs, Badge, DashboardBody, PushBody, EditorBody):
        helper.__module__ = VIEW_MODULE
        setattr(module, helper.__name__, helper)

    urls = types.ModuleType(URL_MODULE)
    urls.urlpatterns = patterns + [path("djust/", include(sse_urlpatterns))]
    sys.modules[URL_MODULE] = urls
    return views


EDITOR_TEXT = (
    "The quick brown fox jumps over the lazy dog while the editor keeps typing. " * 55
)  # ~4 KB


def _event_for(shape: str, n: int) -> Tuple[str, Dict[str, Any]]:
    """The n-th turn's event and parameters (every turn changes the page)."""
    if shape == "form":
        field = FORM_FIELDS[n % len(FORM_FIELDS)]
        value = {"email": "user%d@example.com", "age": "%d"}.get(field, "value %d") % n
        if n % 5 == 0:
            value = ""  # an error appears, then clears on a later turn
        return "validate", {"field": field, "value": value}
    if shape == "table":
        return "bump", {}
    if shape == "dashboard":
        return "tick", {}
    if shape == "push":
        return "work", {}
    return "edit", {"value": EDITOR_TEXT + ("x" * (n % 50))}


# --------------------------------------------------------------------------- #
# Connections
# --------------------------------------------------------------------------- #


def _compact(frame: Any) -> int:
    return len(json.dumps(frame, separators=(",", ":"), default=str).encode("utf-8"))


class Conn:
    """One open page on one transport: ``turn()`` sends an event and returns
    (answer type, bytes up, bytes down)."""

    def __init__(self, shape: str, mode: str, transport: str, rtt: float) -> None:
        self.shape, self.mode, self.transport = shape, mode, transport
        self.half = rtt / 2.0
        self.page: Any = None
        self.live: Any = None
        self.n = 0

    async def open(self) -> None:
        from asgiref.sync import sync_to_async

        from djust.testing import open_live_page

        self.page = await sync_to_async(open_live_page)("/%s-%s/" % (self.shape, self.mode))
        if self.transport == "ws":
            self.live = await self.page.connect(timeout=60)
        elif self.transport == "sse":
            self.live = await self.page.connect_sse(timeout=60)
        if self.live is not None and self.live.mount.get("type") != "mount":
            raise RuntimeError("mount failed: %r" % (self.live.mount,))

    async def turn(self) -> Tuple[str, int, int]:
        from asgiref.sync import sync_to_async

        self.n += 1
        event, params = _event_for(self.shape, self.n)
        up = _compact(params)
        if self.half:
            await asyncio.sleep(self.half)
        if self.live is None:
            response = await sync_to_async(self.page.post_event)(event, params)
            if response.status_code != 200:
                raise RuntimeError("HTTP fallback answered %s" % response.status_code)
            answer = json.loads(response.content)
            kind = "html" if isinstance(answer.get("html"), str) else "patch"
            down = len(response.content)
        else:
            seen = len(self.live.frames)
            answer = await self.live.send_event(event, params)
            kind = answer.get("type", "?")
            if kind not in ("patch", "html_update"):
                raise RuntimeError("turn answered %r" % (answer,))
            if self.shape == "push":
                await self._await_renders(seen, 2)
            # Every frame that arrived for this turn (a push sends two renders).
            down = sum(_compact(f) for f in list(self.live.frames)[seen:])
            await self._drain()
        if self.half:
            await asyncio.sleep(self.half)
        return kind, up, down

    def _renders_since(self, seen: int) -> int:
        frames = list(self.live.frames)[seen:]
        return sum(1 for f in frames if f.get("type") in ("patch", "html_update"))

    async def _await_renders(self, seen: int, wanted: int) -> None:
        """Wait until ``wanted`` render frames have arrived since ``seen``.

        The push frame has arrived before the answer in every run so far (it
        is sent first, on one ordered connection), but the bytes of the turn
        must not depend on that: if it has not, wait for it.
        """
        while self._renders_since(seen) < wanted:
            await self.live.receive(("patch", "html_update"), timeout=30)

    async def _drain(self) -> None:
        """Read the frames ``send_event`` skipped (the push render), so they do
        not pile up unread across turns."""
        while True:
            try:
                await self.live.receive(timeout=0)
            except asyncio.TimeoutError:
                return

    async def close(self) -> None:
        if self.live is not None:
            await self.live.close()


# --------------------------------------------------------------------------- #
# Measurement
# --------------------------------------------------------------------------- #


class LoopLag:
    """Samples how late a 2 ms ticker on the event loop wakes."""

    INTERVAL = 0.002

    def __init__(self) -> None:
        self.samples: List[float] = []
        self._task: Optional[asyncio.Task] = None

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            due = loop.time() + self.INTERVAL
            await asyncio.sleep(self.INTERVAL)
            self.samples.append(max(0.0, loop.time() - due))

    def start(self) -> None:
        self.samples = []
        self._task = asyncio.ensure_future(self._run())

    async def stop(self) -> List[float]:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass  # the sampler was cancelled on purpose: stop() ends it
        return self.samples


def pct(samples: List[float], p: float) -> float:
    if not samples:
        return 0.0
    ordered = sorted(samples)
    return ordered[min(len(ordered) - 1, int(round(p / 100 * (len(ordered) - 1))))]


async def measure_cells(
    shape: str, combos: List[Tuple[str, str]], rtt_ms: float, rounds: int
) -> Dict[str, Dict[str, Any]]:
    """Run ``rounds`` turns on each (transport, mode), interleaved."""
    conns = {}
    for transport, mode in combos:
        conn = Conn(shape, mode, transport, rtt_ms / 1000.0)
        await conn.open()
        conns[(transport, mode)] = conn
    lat: Dict[Any, List[float]] = {k: [] for k in conns}
    cpu: Dict[Any, float] = {k: 0.0 for k in conns}
    lag: Dict[Any, List[float]] = {k: [] for k in conns}
    up: Dict[Any, List[int]] = {k: [] for k in conns}
    down: Dict[Any, List[int]] = {k: [] for k in conns}
    kinds: Dict[Any, Dict[str, int]] = {k: {} for k in conns}
    ticker = LoopLag()
    keys = list(conns)
    try:
        for n in range(WARMUP + rounds):
            order = keys[n % len(keys) :] + keys[: n % len(keys)]
            for key in order:
                ticker.start()
                cpu0 = time.process_time()
                start = time.perf_counter()
                kind, sent, received = await conns[key].turn()
                elapsed = time.perf_counter() - start
                used = time.process_time() - cpu0
                samples = await ticker.stop()
                if n < WARMUP:
                    continue
                lat[key].append(elapsed)
                cpu[key] += used
                lag[key].extend(samples)
                up[key].append(sent)
                down[key].append(received)
                kinds[key][kind] = kinds[key].get(kind, 0) + 1
    finally:
        for conn in conns.values():
            await conn.close()
    out: Dict[str, Dict[str, Any]] = {}
    for key in keys:
        transport, mode = key
        out["%s/%s/%s/%g" % (shape, transport, mode, rtt_ms)] = {
            "shape": shape,
            "transport": transport,
            "mode": mode,
            "rtt_ms": rtt_ms,
            "turns": len(lat[key]),
            "p50_ms": pct(lat[key], 50) * 1000,
            "p95_ms": pct(lat[key], 95) * 1000,
            "cpu_ms": cpu[key] / max(1, len(lat[key])) * 1000,
            "lag_p95_ms": pct(lag[key], 95) * 1000,
            "lag_max_ms": (max(lag[key]) if lag[key] else 0.0) * 1000,
            "bytes_up": statistics.median(up[key]) if up[key] else 0,
            "bytes_down": statistics.median(down[key]) if down[key] else 0,
            "answers": kinds[key],
        }
    return out


async def measure_memory(shape: str, transport: str, mode: str, pages: int) -> float:
    """KB each of ``pages`` open pages retains after three turns (``tracemalloc``).

    One page is opened and closed first, so caches (compiled templates, the
    view class's contract) are not charged to the pages measured.
    """
    warm = Conn(shape, mode, transport, 0.0)
    await warm.open()
    await warm.turn()
    await warm.close()
    gc.collect()
    tracemalloc.start()
    conns = []
    try:
        before = tracemalloc.get_traced_memory()[0]
        for _ in range(pages):
            conn = Conn(shape, mode, transport, 0.0)
            await conn.open()
            for _ in range(3):
                await conn.turn()
            conns.append(conn)
        gc.collect()
        after = tracemalloc.get_traced_memory()[0]
    finally:
        tracemalloc.stop()
        for conn in conns:
            await conn.close()
    return (after - before) / 1024.0 / max(1, pages)


async def run(args: argparse.Namespace) -> Dict[str, Any]:
    cells: Dict[str, Dict[str, Any]] = {}
    for rtt in args.rtt:
        rounds = args.rounds if rtt == 0 else args.rtt_rounds
        modes = args.modes if rtt == 0 else [m for m in args.modes if m in args.rtt_modes]
        for shape in args.shapes:
            combos = [
                (t, m) for t in args.transports for m in modes if (shape, t) not in NOT_APPLICABLE
            ]
            if not combos:
                continue
            print("  rtt %g ms: %s ..." % (rtt, shape), flush=True)
            cells.update(await measure_cells(shape, combos, rtt, rounds))
    memory: Dict[str, float] = {}
    if args.memory:
        for shape in args.shapes:
            for transport in args.transports:
                if (shape, transport) in NOT_APPLICABLE:
                    continue
                for mode in args.modes:
                    key = "%s/%s/%s" % (shape, transport, mode)
                    memory[key] = await measure_memory(shape, transport, mode, args.pages)
    return {"cells": cells, "memory_kb": memory}


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #


def machine() -> Dict[str, Any]:
    import django
    from djust import __version__ as djust_version

    cpu = platform.processor() or platform.machine()
    try:
        with open("/proc/cpuinfo") as handle:
            for line in handle:
                if line.startswith("model name"):
                    cpu = line.split(":", 1)[1].strip()
                    break
    except OSError:
        pass  # no /proc/cpuinfo (not Linux): keep the platform name
    return {
        "python": sys.version.split()[0],
        "django": django.get_version(),
        "djust": djust_version,
        "platform": platform.platform(),
        "cpu": cpu,
        "cpus": os.cpu_count(),
    }


def format_tables(result: Dict[str, Any]) -> str:
    lines: List[str] = []
    cells = result["cells"]
    rtts = sorted({c["rtt_ms"] for c in cells.values()})
    head = "%-10s %-5s %-6s %6s %8s %8s %8s %8s %8s %8s %8s  %s" % (
        "shape",
        "trans",
        "mode",
        "turns",
        "p50 ms",
        "p95 ms",
        "cpu ms",
        "lag95",
        "lagmax",
        "up B",
        "down B",
        "answers",
    )
    for rtt in rtts:
        lines.append("")
        lines.append("simulated RTT %g ms" % rtt)
        lines.append(head)
        for cell in cells.values():
            if cell["rtt_ms"] != rtt:
                continue
            lines.append(
                "%-10s %-5s %-6s %6d %8.2f %8.2f %8.2f %8.2f %8.2f %8d %8d  %s"
                % (
                    cell["shape"],
                    cell["transport"],
                    cell["mode"],
                    cell["turns"],
                    cell["p50_ms"],
                    cell["p95_ms"],
                    cell["cpu_ms"],
                    cell["lag_p95_ms"],
                    cell["lag_max_ms"],
                    cell["bytes_up"],
                    cell["bytes_down"],
                    ",".join("%s:%d" % kv for kv in sorted(cell["answers"].items())),
                )
            )
    for (shape, transport), why in NOT_APPLICABLE.items():
        lines.append("(no %s/%s row: %s)" % (shape, transport, why))
    if result.get("memory_kb"):
        lines.append("")
        lines.append("retained per open page after 3 turns (tracemalloc, KB; HTTP keeps no view)")
        for key, kb in result["memory_kb"].items():
            lines.append("  %-28s %8.1f" % (key, kb))
    return "\n".join(lines)


def format_compare(result: Dict[str, Any], baseline: Dict[str, Any]) -> str:
    """p50 and CPU per turn against a saved run, cell by cell."""
    lines = ["", "against %s" % baseline.get("label", "the baseline")]
    lines.append(
        "%-32s %10s %10s %7s %10s %10s %7s"
        % ("cell", "base p50", "p50", "ratio", "base cpu", "cpu", "ratio")
    )
    for key, cell in result["cells"].items():
        old = baseline.get("cells", {}).get(key)
        if not old:
            continue
        lines.append(
            "%-32s %10.2f %10.2f %7.2f %10.2f %10.2f %7.2f"
            % (
                key,
                old["p50_ms"],
                cell["p50_ms"],
                cell["p50_ms"] / max(old["p50_ms"], 1e-9),
                old["cpu_ms"],
                cell["cpu_ms"],
                cell["cpu_ms"] / max(old["cpu_ms"], 1e-9),
            )
        )
    return "\n".join(lines)


def _csv(value: str, allowed: Tuple[str, ...]) -> List[str]:
    items = [v.strip() for v in value.split(",") if v.strip()]
    bad = [v for v in items if v not in allowed]
    if bad:
        raise argparse.ArgumentTypeError("unknown %s (choose from %s)" % (bad, ",".join(allowed)))
    return items


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--shapes", default=",".join(SHAPES), type=lambda v: _csv(v, SHAPES))
    parser.add_argument(
        "--transports", default=",".join(TRANSPORTS), type=lambda v: _csv(v, TRANSPORTS)
    )
    parser.add_argument("--modes", default=",".join(MODES), type=lambda v: _csv(v, MODES))
    parser.add_argument(
        "--rtt",
        default=None,
        type=lambda v: [float(x) for x in v.split(",") if x.strip()],
        help="simulated round-trip times in ms (default 0,20,100,250)",
    )
    parser.add_argument("--rounds", type=int, default=40, help="turns per cell at RTT 0")
    parser.add_argument("--rtt-rounds", type=int, default=10, help="turns per cell at RTT > 0")
    parser.add_argument(
        "--rtt-modes",
        default="save",
        type=lambda v: _csv(v, MODES),
        help="save modes the RTT > 0 sweep runs (default: save, the shipped default)",
    )
    parser.add_argument("--rows", type=int, default=1000, help="table rows (default 1000)")
    parser.add_argument(
        "--table-body",
        choices=("flat", "nested"),
        default="flat",
        help="nested: each table row also renders a nested {%% for %%} over its tags (#4325)",
    )
    parser.add_argument("--no-memory", dest="memory", action="store_false")
    parser.add_argument(
        "--pages", type=int, default=5, help="pages open at once in the memory pass (default 5)"
    )
    parser.add_argument("--json", help="also write the result as JSON to this path")
    parser.add_argument("--label", default="", help="a name for this run, stored in --json")
    parser.add_argument("--compare", help="a JSON file a previous run wrote; prints ratios")
    parser.add_argument(
        "--quick",
        action="store_true",
        help="seconds, not minutes: 2 rounds, 20 rows, RTT 0 and 20 ms (unless --rtt)",
    )
    args = parser.parse_args(argv)
    if args.quick:
        args.rounds, args.rtt_rounds, args.rows = 2, 1, 20
        args.pages = 1
    if args.rtt is None:
        args.rtt = [0.0, 20.0] if args.quick else [0.0, 20.0, 100.0, 250.0]

    tmp_dir = _configure()
    try:
        return _measure_and_report(args)
    finally:
        from django.db import connections

        connections.close_all()
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _measure_and_report(args: argparse.Namespace) -> int:
    _install_views(args.rows, args.table_body)
    info = machine()
    print(
        "djust %(djust)s  python %(python)s  django %(django)s  %(cpu)s x%(cpus)s" % info,
        flush=True,
    )
    print(
        "rounds=%d (rtt>0: %d)  rows=%d  rtt=%s ms"
        % (args.rounds, args.rtt_rounds, args.rows, ",".join("%g" % r for r in args.rtt)),
        flush=True,
    )
    result = asyncio.run(run(args))
    result.update(
        machine=info,
        label=args.label,
        settings={
            "rounds": args.rounds,
            "rtt_rounds": args.rtt_rounds,
            "rows": args.rows,
            "rtt_ms": args.rtt,
            "warmup": WARMUP,
        },
    )
    print(format_tables(result))
    if args.compare:
        with open(args.compare) as handle:
            print(format_compare(result, json.load(handle)))
    if args.json:
        with open(args.json, "w") as handle:
            json.dump(result, handle, indent=1, sort_keys=True)
        print("\nwrote %s" % args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
