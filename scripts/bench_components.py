#!/usr/bin/env python3
"""Benchmark: djust's components one at a time, with saved results and a profiling mode.

The other benchmarks each answer one question (``bench_turn_transports.py``: a
complete turn over each transport; ``bench_client_turns.py``: the browser
side; ``cargo bench``: Rust kernels). This one measures each layer of the stack
on its own, so a regression or a hotspot can be pinned to the layer it lives
in, and records the results in one JSON file to compare between runs:

``tag``        a ``{% load djust_components %}`` template tag (simple and
               block tags): compile and render, and render of a compiled one.
``component``  each Python component in the gallery
               (``djust.components.components.*``): construct and ``render()``
               on its first gallery example.
``render``     the Rust template engine: a cold render (new ``RustLiveView``,
               parse and render) and a warm one, a 1,000-row table and a page
               of 60 includes; the same table through Django's
               ``djust`` template backend.
``vdom``       diff and patch in Rust: ``diff_html`` of two parsed documents
               (one cell changed in a 1,000-row table, and a 4 KB editor), and
               a ``RustLiveView.render_with_diff`` turn with Rust's own phase
               timings (render / parse / diff / serialize).
``page``       a full-page GET through Django's middleware and URLconf, and a
               socket mount (``LiveViewConsumer``) of the page it served. Pages:
               the four inline-template workloads of the transport benchmark,
               ``includes`` (30 ``{% include %}`` sites of a partial that loads
               ``djust_components``), ``plain-includes`` (30 sites of a partial
               that loads no library) and ``big`` (~80 KB of plain markup). The
               last three are file templates through the Rust loader, the shape
               of #4363.
``turn``       one event turn that changes one value, per workload, over the
               WebSocket, plus the ``table`` workload over SSE and the HTTP
               fallback.

Every case reports p50 / p95 / mean wall time, CPU per op (``process_time``;
client and server share the process), and the memory one op allocates at its
peak (``tracemalloc``, a separate pass, so it does not slow the timed rounds).
``--memory`` also measures what one open page retains.

Recording and comparing::

    make benchmark-components                                    # all suites
    python scripts/bench_components.py --json run.json
    python scripts/bench_components.py --compare run.json        # deltas vs run.json
    python scripts/bench_components.py --suites page,turn --cases includes

Profiling (find the hotspots)::

    python scripts/bench_components.py --profile prof/ --cases includes
        # cProfile per case: prof/<case>.prof (open with snakeviz) and
        # prof/hotspots.txt, the top functions per case and across cases
    python scripts/bench_components.py --pyspy prof/ --cases table
        # re-runs itself under ``py-spy record --native``: one flame graph
        # (prof/pyspy.svg) with the Rust frames (``pip install py-spy``)

The Rust-side split of a render comes with every ``vdom`` / ``render`` case
(``rust_*`` fields); ``make benchmark-rust`` runs the Criterion kernels.

A RELEASE ``_rust`` build is required for meaningful numbers. Exit code is
always 0 unless a case fails to run: a measurement tool, not a gate.
"""

from __future__ import annotations

import argparse
import asyncio
import gc
import importlib.util
import inspect
import json
import os
import pstats
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import tracemalloc
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.join(ROOT, "python") not in sys.path:
    sys.path.insert(0, os.path.join(ROOT, "python"))

SUITES = ("tag", "component", "render", "vdom", "page", "turn")
PAGE_MODULE = "djust_bench_component_pages"
INCLUDE_SITES = 30
TABLE_ROWS = 1000


def _load_transport_bench() -> Any:
    """The transport benchmark's views and connection helper, reused as is."""
    path = os.path.join(ROOT, "scripts", "bench_turn_transports.py")
    spec = importlib.util.spec_from_file_location("bench_turn_transports", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["bench_turn_transports"] = module
    spec.loader.exec_module(module)
    return module


TB = _load_transport_bench()


# --------------------------------------------------------------------------- #
# Django setup
# --------------------------------------------------------------------------- #


def _write_templates(directory: str) -> None:
    head = "<div dj-root>"
    tail = "</div>"
    files = {
        "part.html": '{% load djust_components %}<div class="row">{% badge label="New" %}</div>',
        "plain.html": '<div class="row"><p>{{ title }}</p></div>',
        "includes.html": head + '{% include "part.html" %}' * INCLUDE_SITES + "<p>end</p>" + tail,
        "plain-includes.html": head
        + '{% include "plain.html" %}' * INCLUDE_SITES
        + "<p>end</p>"
        + tail,
        "big.html": head
        + "".join(
            '<div class="card"><h3>Card %d</h3><p>Some text for card %d, with a '
            '<a href="/x/%d">link</a>.</p></div>' % (i, i, i)
            for i in range(900)
        )
        + tail,
    }
    for name, source in files.items():
        with open(os.path.join(directory, name), "w") as handle:
            handle.write(source)


#: Set by --state-store / --save-interval before configure().
_STORE_SETTINGS: Dict[str, Any] = {}


def configure(mode: str) -> str:
    """Configure Django (DEBUG off, djust's template backend, a fresh SQLite
    file for sessions) and return the temporary directory (removed at exit)."""
    import django
    from django.conf import settings

    tmp_dir = tempfile.mkdtemp(prefix="djust-bench-components-")
    tpl_dir = os.path.join(tmp_dir, "templates")
    os.mkdir(tpl_dir)
    _write_templates(tpl_dir)
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
            "djust.components",
        ],
        MIDDLEWARE=[
            "django.contrib.sessions.middleware.SessionMiddleware",
            "django.middleware.csrf.CsrfViewMiddleware",
            "django.contrib.auth.middleware.AuthenticationMiddleware",
        ],
        ROOT_URLCONF=TB.URL_MODULE,
        DATABASES={
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": os.path.join(tmp_dir, "bench.sqlite3"),
            }
        },
        SESSION_ENGINE="django.contrib.sessions.backends.db",
        CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}},
        TEMPLATES=[
            {
                "NAME": "djust",
                "BACKEND": "djust.template.backend.DjustTemplateBackend",
                "DIRS": [tpl_dir],
                "APP_DIRS": True,
            },
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "DIRS": [tpl_dir],
                "APP_DIRS": True,
            },
        ],
        LIVEVIEW_ALLOWED_MODULES=[TB.VIEW_MODULE, PAGE_MODULE],
        LIVEVIEW_CONFIG={"rate_limit": {"rate": 10**6, "burst": 10**6, "max_warnings": 10**6}},
        STATIC_URL="/static/",
        USE_TZ=True,
        LOGGING_CONFIG=None,
        **_STORE_SETTINGS,
    )
    django.setup()
    from django.core.management import call_command

    call_command("migrate", verbosity=0, run_syncdb=True)
    _install_views(mode)
    return tmp_dir


def _install_views(mode: str) -> None:
    """The transport benchmark's views, plus one LiveView per file page."""
    import types

    from django.urls import path

    from djust import LiveView, state

    TB._install_views(TABLE_ROWS)
    module = types.ModuleType(PAGE_MODULE)
    sys.modules[PAGE_MODULE] = module
    patterns = []
    for page in FILE_PAGES:
        name = "Page_" + page.replace("-", "_")
        cls = type(
            name,
            (LiveView,),
            {
                "__module__": PAGE_MODULE,
                "template_name": "%s.html" % page,
                "automatic_state": mode == "save",
                "title": state("Inventory"),
            },
        )
        setattr(module, name, cls)
        patterns.append(path("page-%s/" % page, cls.as_view()))
    sys.modules[TB.URL_MODULE].urlpatterns += patterns


INLINE_PAGES = ("form", "table", "dashboard", "editor")
FILE_PAGES = ("includes", "plain-includes", "big")


def page_url(page: str, mode: str) -> str:
    if page in FILE_PAGES:
        return "/page-%s/" % page
    return "/%s-%s/" % (page, mode)


# --------------------------------------------------------------------------- #
# Cases
# --------------------------------------------------------------------------- #


@dataclass
class Case:
    """One measured operation.

    ``make()`` runs once, untimed, and returns the op. When ``each`` is set it
    runs before every op, untimed, and its result is the op's argument;
    ``after`` (sync or async) cleans up after every op, untimed. ``inner`` ops
    run per sample (for microsecond ops), and the figures are per op.
    """

    name: str
    make: Callable[[], Callable[..., Any]]
    inner: int = 1
    each: Optional[Callable[[], Any]] = None
    after: Optional[Callable[[Any], Any]] = None
    note: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def suite(self) -> str:
        return self.name.split("/", 1)[0]


#: (tag name, template body). A body that fails to render is reported and skipped.
TAG_CASES = [
    ("badge", '{% badge label="New" status="success" %}'),
    ("dj_button", '{% dj_button label="Save" event="save" %}'),
    ("dj_input", '{% dj_input name="email" label="Email" value="a@b.c" %}'),
    ("progress", "{% progress value=40 label='Upload' %}"),
    ("avatar", '{% avatar initials="JD" status="online" %}'),
    ("pagination", "{% pagination page=3 total_pages=9 %}"),
    ("breadcrumb", "{% breadcrumb items=crumbs %}"),
    ("stat_card", '{% stat_card label="Revenue" value="$12k" trend="+4%" %}'),
    ("dj_tag", '{% dj_tag label="beta" dismissible=True %}'),
    ("card", '{% card title="Card" %}<p>Body {{ n }}</p>{% endcard %}'),
    ("alert", '{% alert variant="warning" %}Careful{% endalert %}'),
    ("modal", '{% modal id="m1" title="Title" %}<p>Body</p>{% endmodal %}'),
    ("data_table", "{% data_table rows=rows columns=columns %}"),
]


def _tag_context(body: str) -> Dict[str, Any]:
    """Only the names ``body`` uses: the ``djust`` backend serializes every
    context value on each render, so an unused one would be charged to the tag
    (``tag/badge/render+unused-context`` measures exactly that)."""
    full = {
        "n": 7,
        "crumbs": [{"label": "Home", "url": "/"}, {"label": "Docs", "url": "/d/"}],
        "rows": [{"id": i, "name": "Row %d" % i, "qty": i % 7} for i in range(50)],
        "columns": [
            {"key": "id", "label": "ID"},
            {"key": "name", "label": "Name"},
            {"key": "qty", "label": "Qty"},
        ],
    }
    if body == "*":
        return full
    return {k: v for k, v in full.items() if re.search(r"\b%s\b" % k, body)}


def tag_cases() -> List[Case]:
    from django.template import engines

    engine = engines["djust"]
    out = []
    for tag, body in TAG_CASES:
        source = "{% load djust_components %}" + body

        def make_cold(source: str = source) -> Callable[[], Any]:
            ctx = _tag_context(source)
            return lambda: engine.from_string(source).render(ctx)

        def make_warm(source: str = source) -> Callable[[], Any]:
            ctx = _tag_context(source)
            compiled = engine.from_string(source)
            return lambda: compiled.render(ctx)

        out.append(Case("tag/%s/compile+render" % tag, make_cold, inner=20))
        out.append(Case("tag/%s/render" % tag, make_warm, inner=50))

    def unused_context() -> Callable[[], Any]:
        compiled = engine.from_string("{% load djust_components %}" + TAG_CASES[0][1])
        ctx = _tag_context("*")
        return lambda: compiled.render(ctx)

    out.append(Case("tag/badge/render+unused-context", unused_context, inner=20))
    return out


def component_cases(limit: Optional[int]) -> List[Case]:
    from djust.theming.gallery.component_registry import (
        PYTHON_COMPONENT_EXAMPLES,
        _load_component_class,
    )

    out = []
    for name in sorted(PYTHON_COMPONENT_EXAMPLES)[:limit]:
        examples = PYTHON_COMPONENT_EXAMPLES[name]
        if not examples:
            continue

        def make(name: str = name, kwargs: Dict[str, Any] = examples[0]) -> Callable[[], Any]:
            cls, _ = _load_component_class(name)
            if cls is None:
                raise RuntimeError("no component class for %s" % name)
            cls(**kwargs).render()  # fail here, not in the timed loop
            return lambda: cls(**kwargs).render()

        out.append(Case("component/%s" % name, make, inner=20))
    return out


def _table_template() -> str:
    return (
        "<div dj-root><h1>{{ title }}</h1><table>"
        '{% for row in items %}<tr id="row-{{ row.id }}"><td>{{ row.sku }}</td>'
        "<td>{{ row.name }}</td><td>{{ row.price }}</td><td>{{ row.qty }}</td>"
        "<td>{% if row.active %}on{% else %}off{% endif %}</td></tr>"
        "{% endfor %}</table></div>"
    )


def _table_rows() -> List[Dict[str, Any]]:
    return [
        {
            "id": i,
            "sku": "SKU-%05d" % i,
            "name": "Catalogue item number %d" % i,
            "price": round(i * 3.75, 2),
            "qty": i % 7,
            "active": i % 3 != 0,
        }
        for i in range(TABLE_ROWS)
    ]


def _tpl_dirs() -> List[str]:
    from django.conf import settings

    return list(settings.TEMPLATES[0]["DIRS"])


def render_cases() -> List[Case]:
    from django.template import engines

    from djust._rust import RustLiveView

    state = {"title": "Inventory", "items": _table_rows()}

    def table_cold() -> Callable[[], Any]:
        source = _table_template()

        def op() -> Any:
            view = RustLiveView(source, None)
            view.update_state(state)
            return view.render()

        return op

    def table_warm() -> Callable[[], Any]:
        view = RustLiveView(_table_template(), None)
        view.update_state(state)
        view.render()
        return view.render

    def includes(cold: bool) -> Callable[[], Callable[[], Any]]:
        def make() -> Callable[[], Any]:
            with open(os.path.join(_tpl_dirs()[0], "includes.html")) as handle:
                source = handle.read()
            dirs = _tpl_dirs()
            if cold:

                def op() -> Any:
                    view = RustLiveView(source, dirs)
                    view.update_state({"title": "Inventory"})
                    return view.render()

                return op
            view = RustLiveView(source, dirs)
            view.update_state({"title": "Inventory"})
            view.render()
            return view.render

        return make

    def django_backend() -> Callable[[], Any]:
        compiled = engines["djust"].from_string(_table_template())
        return lambda: compiled.render(state)

    return [
        Case("render/table/rust-cold", table_cold, inner=3),
        Case("render/table/rust-warm", table_warm, inner=3),
        Case("render/includes/rust-cold", includes(True), inner=3),
        Case("render/includes/rust-warm", includes(False), inner=3),
        Case("render/table/djust-backend", django_backend, inner=3),
    ]


def vdom_cases() -> List[Case]:
    from djust._rust import RustLiveView, diff_html

    def table_html(qty: int) -> str:
        rows = _table_rows()
        rows[TABLE_ROWS // 2]["qty"] = qty
        return (
            "<div dj-root><table>"
            + "".join(
                '<tr id="row-%(id)s"><td>%(sku)s</td><td>%(name)s</td><td>%(price)s</td>'
                "<td>%(qty)s</td></tr>" % r
                for r in rows
            )
            + "</table></div>"
        )

    def diff_table() -> Callable[[], Any]:
        old, new = table_html(1), table_html(2)
        return lambda: diff_html(old, new)

    def diff_editor() -> Callable[[], Any]:
        text = TB.EDITOR_TEXT
        old = "<div dj-root><textarea>%s</textarea><p>%d</p></div>" % (text, len(text))
        new = "<div dj-root><textarea>%sx</textarea><p>%d</p></div>" % (text, len(text) + 1)
        return lambda: diff_html(old, new)

    timing_case = Case("vdom/table/render_with_diff", lambda: None, inner=1)

    def render_with_diff() -> Callable[[], Any]:
        rows = _table_rows()
        view = RustLiveView(_table_template(), None)
        view.update_state({"title": "Inventory", "items": rows})
        view.render_with_diff()
        view.render_with_diff()
        phases: Dict[str, List[float]] = {}
        timing_case.extra["phases"] = phases

        def op() -> Any:
            rows[TABLE_ROWS // 2]["qty"] += 1
            view.update_state_items("items", [TABLE_ROWS // 2], [dict(rows[TABLE_ROWS // 2])])
            result = view.render_with_diff()
            for key, value in (view.get_render_timing() or {}).items():
                phases.setdefault(key, []).append(float(value))
            return result

        return op

    timing_case.make = render_with_diff
    return [
        Case("vdom/table/diff_html", diff_table, inner=3),
        Case("vdom/editor/diff_html", diff_editor, inner=20),
        timing_case,
    ]


def page_cases(mode: str) -> List[Case]:
    from asgiref.sync import async_to_sync  # noqa: F401  (Django's async bridge loads)

    from djust.testing import open_live_page

    out = []
    for page in INLINE_PAGES + FILE_PAGES:
        url = page_url(page, mode)

        def make_get(url: str = url) -> Callable[[], Any]:
            open_live_page(url)
            return lambda: open_live_page(url)

        async def close(live: Any) -> None:
            await live.close()

        def make_mount(url: str = url) -> Callable[[Any], Any]:
            async def op(page_obj: Any) -> Any:
                live = await page_obj.connect(timeout=60)
                if live.mount.get("type") != "mount":
                    raise RuntimeError("mount failed: %r" % (live.mount,))
                return live

            return op

        out.append(Case("page/%s/get" % page, make_get))
        out.append(
            Case(
                "page/%s/mount" % page,
                make_mount,
                each=lambda url=url: open_live_page(url),
                after=close,
            )
        )
    return out


def turn_cases(mode: str, loop: asyncio.AbstractEventLoop) -> List[Case]:
    combos = [(shape, "ws") for shape in ("form", "table", "dashboard", "push", "editor")]
    combos += [("table", "sse"), ("table", "http")]
    out = []
    for shape, transport in combos:

        def make(shape: str = shape, transport: str = transport) -> Callable[[], Any]:
            conn = TB.Conn(shape, mode, transport, 0.0)
            loop.run_until_complete(conn.open())
            for _ in range(TB.WARMUP):
                loop.run_until_complete(conn.turn())
            OPEN_CONNS.append(conn)
            return conn.turn

        out.append(Case("turn/%s/%s" % (shape, transport), make))
    return out


OPEN_CONNS: List[Any] = []


# --------------------------------------------------------------------------- #
# Running
# --------------------------------------------------------------------------- #


def pct(samples: List[float], p: float) -> float:
    return TB.pct(samples, p)


class Runner:
    def __init__(self, args: argparse.Namespace, loop: asyncio.AbstractEventLoop) -> None:
        self.args = args
        self.loop = loop

    def _call(self, fn: Optional[Callable[..., Any]], *a: Any) -> Any:
        if fn is None:
            return None
        result = fn(*a)
        if inspect.isawaitable(result):
            result = self.loop.run_until_complete(result)
        return result

    def _sample(self, case: Case, op: Callable[..., Any]) -> tuple:
        arg = self._call(case.each) if case.each else None
        args = (arg,) if case.each else ()
        cpu0 = time.process_time()
        t0 = time.perf_counter()
        result = None
        for _ in range(case.inner):
            result = self._call(op, *args)
        wall = (time.perf_counter() - t0) / case.inner
        cpu = (time.process_time() - cpu0) / case.inner
        if case.after:
            self._call(case.after, result)
        return wall, cpu

    def run_case(self, case: Case, profiler: Any = None) -> Dict[str, Any]:
        op = case.make()
        for _ in range(self.args.warmup):
            self._sample(case, op)
        case.extra.get("phases", {}).clear() if "phases" in case.extra else None
        walls, cpus = [], []
        if profiler is not None:
            profiler.enable()
        for _ in range(self.args.rounds):
            wall, cpu = self._sample(case, op)
            walls.append(wall)
            cpus.append(cpu)
        if profiler is not None:
            profiler.disable()
        peak_kb = self._peak_alloc(case, op) if self.args.alloc else None
        out: Dict[str, Any] = {
            "rounds": len(walls),
            "inner": case.inner,
            "p50_ms": pct(walls, 50) * 1000,
            "p95_ms": pct(walls, 95) * 1000,
            "mean_ms": statistics.mean(walls) * 1000,
            "min_ms": min(walls) * 1000,
            "cpu_ms": statistics.mean(cpus) * 1000,
        }
        if peak_kb is not None:
            out["alloc_peak_kb"] = peak_kb
        for key, values in sorted(case.extra.get("phases", {}).items()):
            if values:
                out["rust_" + key] = statistics.median(values)
        return out

    def _peak_alloc(self, case: Case, op: Callable[..., Any]) -> float:
        gc.collect()
        arg = self._call(case.each) if case.each else None
        args = (arg,) if case.each else ()
        tracemalloc.start()
        try:
            tracemalloc.reset_peak()
            base = tracemalloc.get_traced_memory()[0]
            result = self._call(op, *args)
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        if case.after:
            self._call(case.after, result)
        return (peak - base) / 1024.0


def measure_retained(mode: str, loop: asyncio.AbstractEventLoop, pages: int) -> Dict[str, float]:
    """KB one open page retains (view, session, socket state) after 3 turns,
    per workload, over the WebSocket."""
    out = {}
    for shape in ("form", "table", "dashboard", "editor"):
        out["%s/ws" % shape] = loop.run_until_complete(TB.measure_memory(shape, "ws", mode, pages))
    for page in FILE_PAGES:
        out["%s/mount" % page] = loop.run_until_complete(_retained_file_page(page, mode, pages))
    return out


async def _retained_file_page(page: str, mode: str, pages: int) -> float:
    from asgiref.sync import sync_to_async

    from djust.testing import open_live_page

    url = page_url(page, mode)
    warm = await (await sync_to_async(open_live_page)(url)).connect(timeout=60)
    await warm.close()
    gc.collect()
    tracemalloc.start()
    lives = []
    try:
        before = tracemalloc.get_traced_memory()[0]
        for _ in range(pages):
            lives.append(await (await sync_to_async(open_live_page)(url)).connect(timeout=60))
        gc.collect()
        after = tracemalloc.get_traced_memory()[0]
    finally:
        tracemalloc.stop()
        for live in lives:
            await live.close()
    return (after - before) / 1024.0 / max(1, pages)


def build_cases(args: argparse.Namespace, loop: asyncio.AbstractEventLoop) -> List[Case]:
    cases: List[Case] = []
    if "tag" in args.suites:
        cases += tag_cases()
    if "component" in args.suites:
        cases += component_cases(8 if args.quick else None)
    if "render" in args.suites:
        cases += render_cases()
    if "vdom" in args.suites:
        cases += vdom_cases()
    if "page" in args.suites:
        cases += page_cases(args.mode)
    if "turn" in args.suites:
        cases += turn_cases(args.mode, loop)
    if args.cases:
        wanted = args.cases
        cases = [c for c in cases if any(w in c.name for w in wanted)]
    return cases


def _safe(name: str) -> str:
    return name.replace("/", "__").replace("+", "_")


def run(args: argparse.Namespace) -> Dict[str, Any]:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    runner = Runner(args, loop)
    results: Dict[str, Dict[str, Any]] = {}
    errors: Dict[str, str] = {}
    profiles: Dict[str, str] = {}
    try:
        cases = build_cases(args, loop)
        for case in cases:
            profiler = None
            if args.profile:
                import cProfile

                profiler = cProfile.Profile()
            try:
                results[case.name] = runner.run_case(case, profiler)
            except Exception as exc:  # a measurement tool: report and go on
                errors[case.name] = "%s: %s" % (type(exc).__name__, str(exc)[:300])
                print("  %-44s FAILED %s" % (case.name, errors[case.name]), flush=True)
                continue
            if profiler is not None:
                path = os.path.join(args.profile, _safe(case.name) + ".prof")
                profiler.dump_stats(path)
                profiles[case.name] = path
            r = results[case.name]
            print(
                "  %-44s p50 %9.3f ms  p95 %9.3f  cpu %9.3f%s"
                % (
                    case.name,
                    r["p50_ms"],
                    r["p95_ms"],
                    r["cpu_ms"],
                    "  alloc %8.1f KB" % r["alloc_peak_kb"] if "alloc_peak_kb" in r else "",
                ),
                flush=True,
            )
        retained = measure_retained(args.mode, loop, args.pages) if args.memory else {}
    finally:
        for conn in OPEN_CONNS:
            try:
                loop.run_until_complete(conn.close())
            except Exception as exc:  # closing after a failed case: note it, keep going
                print("  (closing a connection failed: %s)" % exc, file=sys.stderr)
        loop.close()
    out = {
        "machine": TB.machine(),
        "git": _git_rev(),
        "mode": args.mode,
        "rounds": args.rounds,
        "cases": results,
        "errors": errors,
        "retained_kb": retained,
    }
    if profiles:
        out["hotspots"] = write_hotspots(args.profile, profiles, args.top)
    return out


def _git_rev() -> str:
    try:
        return subprocess.run(
            ["git", "-C", ROOT, "describe", "--always", "--dirty"],
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


# --------------------------------------------------------------------------- #
# Profiling output
# --------------------------------------------------------------------------- #


def _func_label(func: tuple) -> str:
    filename, line, name = func
    for marker in ("/python/djust/", "/site-packages/"):
        if marker in filename:
            filename = filename.split(marker, 1)[1]
            if marker == "/python/djust/":
                filename = "djust/" + filename
            break
    if filename == "~":
        return name  # a builtin or a Rust function (``<built-in method ...>``)
    return "%s:%d(%s)" % (filename, line, name)


def write_hotspots(directory: str, profiles: Dict[str, str], top: int) -> List[Dict[str, Any]]:
    """Write ``hotspots.txt`` (per case and across cases, by own time) and
    return the across-cases list for the JSON."""
    lines: List[str] = []
    total: Dict[tuple, List[float]] = {}
    for case, path in profiles.items():
        stats = pstats.Stats(path)
        entries = stats.stats  # type: ignore[attr-defined]
        case_total = sum(v[2] for v in entries.values()) or 1e-12
        lines.append("== %s (own time %.1f ms profiled)" % (case, case_total * 1000))
        ranked = sorted(entries.items(), key=lambda kv: -kv[1][2])[:top]
        for func, (cc, nc, tt, ct, _callers) in ranked:
            lines.append(
                "  %5.1f%%  own %8.2f ms  cum %8.2f ms  %7d calls  %s"
                % (tt / case_total * 100, tt * 1000, ct * 1000, nc, _func_label(func))
            )
        for func, (cc, nc, tt, ct, _callers) in entries.items():
            share = total.setdefault(func, [0.0, 0.0])
            share[0] += tt / case_total  # share of each case's time, summed
            share[1] += tt
        lines.append("")
    ranked_all = sorted(total.items(), key=lambda kv: -kv[1][0])[:top]
    lines.insert(0, "")
    across = []
    for func, (share, tt) in reversed(ranked_all):
        lines.insert(
            0,
            "  %6.1f  own %9.2f ms  %s" % (share * 100, tt * 1000, _func_label(func)),
        )
    for func, (share, tt) in ranked_all:
        across.append(
            {"function": _func_label(func), "share_pct_sum": share * 100, "own_ms": tt * 1000}
        )
    lines.insert(
        0,
        "== across %d cases: each function's share of each case's own time, summed (%%)"
        % len(profiles),
    )
    with open(os.path.join(directory, "hotspots.txt"), "w") as handle:
        handle.write("\n".join(lines) + "\n")
    return across


# --------------------------------------------------------------------------- #
# Reports
# --------------------------------------------------------------------------- #


def format_compare(result: Dict[str, Any], baseline: Dict[str, Any], threshold: float) -> str:
    lines = [
        "Compared with %s (%s): p50, %% change (+ is slower); |change| >= %g%% marked *"
        % (baseline.get("git", "?"), baseline.get("machine", {}).get("djust", "?"), threshold),
        "%-44s %10s %10s %8s %10s %10s %8s"
        % ("case", "base p50", "now p50", "delta", "base cpu", "now cpu", "delta"),
    ]
    base_cases = baseline.get("cases", {})
    for name, now in result["cases"].items():
        base = base_cases.get(name)
        if not base:
            lines.append("%-44s %10s %10.3f   (new)" % (name, "-", now["p50_ms"]))
            continue

        def delta(key: str) -> str:
            if not base.get(key):
                return "-"
            change = (now[key] - base[key]) / base[key] * 100
            return "%+7.1f%%%s" % (change, "*" if abs(change) >= threshold else " ")

        lines.append(
            "%-44s %10.3f %10.3f %8s %10.3f %10.3f %8s"
            % (
                name,
                base["p50_ms"],
                now["p50_ms"],
                delta("p50_ms"),
                base["cpu_ms"],
                now["cpu_ms"],
                delta("cpu_ms"),
            )
        )
    missing = sorted(set(base_cases) - set(result["cases"]))
    if missing:
        lines.append("(not run this time: %s)" % ", ".join(missing))
    return "\n".join(lines)


def format_summary(result: Dict[str, Any]) -> str:
    lines = []
    cases = result["cases"]
    slowest = sorted(
        (n for n in cases if n.startswith("component/")), key=lambda n: -cases[n]["p50_ms"]
    )[:10]
    if slowest:
        lines.append("Slowest components (construct + render, p50 ms):")
        lines += ["  %-40s %8.3f" % (n, cases[n]["p50_ms"]) for n in slowest]
    timing = cases.get("vdom/table/render_with_diff")
    if timing:
        lines.append(
            "Rust render_with_diff split (median ms): "
            + "  ".join(
                "%s %.3f" % (k[5:], v)
                for k, v in timing.items()
                if k.startswith("rust_") and isinstance(v, (int, float))
            )
        )
    if result.get("retained_kb"):
        lines.append("Retained per open page (KB):")
        lines += ["  %-24s %8.1f" % kv for kv in result["retained_kb"].items()]
    if result.get("errors"):
        lines.append("Cases that failed to run:")
        lines += ["  %s: %s" % kv for kv in result["errors"].items()]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _csv(value: str, allowed: tuple) -> List[str]:
    items = [v.strip() for v in value.split(",") if v.strip()]
    bad = [v for v in items if v not in allowed]
    if bad:
        raise argparse.ArgumentTypeError("unknown: %s (choose from %s)" % (bad, ", ".join(allowed)))
    return items


def parse_args(argv: Optional[List[str]]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("--suites", type=lambda v: _csv(v, SUITES), default=list(SUITES))
    p.add_argument(
        "--cases",
        type=lambda v: [x for x in v.split(",") if x],
        default=[],
        help="keep only cases whose name contains one of these substrings",
    )
    p.add_argument("--rounds", type=int, default=30, help="timed samples per case")
    p.add_argument("--warmup", type=int, default=3)
    p.add_argument("--mode", choices=TB.MODES, default="save", help="state save after a turn")
    p.add_argument("--json", help="write the results here")
    p.add_argument("--compare", help="a previous --json file to compare against")
    p.add_argument(
        "--threshold", type=float, default=10.0, help="mark changes at least this big (%%)"
    )
    p.add_argument(
        "--no-alloc", dest="alloc", action="store_false", help="skip the tracemalloc pass"
    )
    p.add_argument("--memory", action="store_true", help="also measure memory per open page")
    p.add_argument("--pages", type=int, default=5, help="pages opened for --memory")
    p.add_argument(
        "--profile", metavar="DIR", help="cProfile each case into DIR, with hotspots.txt"
    )
    p.add_argument("--top", type=int, default=25, help="functions per list in hotspots.txt")
    p.add_argument(
        "--pyspy", metavar="DIR", help="re-run under py-spy --native; flame graph in DIR"
    )
    p.add_argument("--quick", action="store_true", help="tiny run (seconds), what the test runs")
    p.add_argument(
        "--state-store",
        help="DJUST_STATE_STORE for the run (e.g. djust.state_store.DatabaseViewStateStore)",
    )
    p.add_argument(
        "--save-interval",
        type=float,
        help="DJUST_STATE_SAVE_INTERVAL for the run (0: save before every reply)",
    )
    args = p.parse_args(argv)
    if args.quick:
        args.rounds, args.warmup, args.pages = 2, 1, 1
    return args


def run_pyspy(args: argparse.Namespace, argv: List[str]) -> int:
    exe = shutil.which("py-spy")
    if exe is None:
        print("py-spy is not installed: pip install py-spy", file=sys.stderr)
        return 2
    os.makedirs(args.pyspy, exist_ok=True)
    rest = []
    skip = False
    for item in argv:
        if skip:
            skip = False
            continue
        if item == "--pyspy":
            skip = True
            continue
        if item.startswith("--pyspy="):
            continue
        rest.append(item)
    out = os.path.join(args.pyspy, "pyspy.svg")
    cmd = [
        exe,
        "record",
        "--native",
        "--rate",
        "250",
        "-o",
        out,
        "--",
        sys.executable,
        os.path.abspath(__file__),
    ] + rest
    print("running: %s" % " ".join(cmd), flush=True)
    code = subprocess.call(cmd)
    print("flame graph: %s" % out)
    return code


def main(argv: Optional[List[str]] = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    args = parse_args(raw)
    if args.pyspy:
        return run_pyspy(args, raw)
    if args.profile:
        os.makedirs(args.profile, exist_ok=True)
    if args.state_store:
        _STORE_SETTINGS["DJUST_STATE_STORE"] = args.state_store
    if args.save_interval is not None:
        _STORE_SETTINGS["DJUST_STATE_SAVE_INTERVAL"] = args.save_interval
    tmp_dir = configure(args.mode)
    try:
        print(
            "djust component benchmark: suites %s, %d rounds, mode %s"
            % (",".join(args.suites), args.rounds, args.mode),
            flush=True,
        )
        result = run(args)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    summary = format_summary(result)
    if summary:
        print(summary)
    if args.compare:
        with open(args.compare) as handle:
            print(format_compare(result, json.load(handle), args.threshold))
    if args.json:
        with open(args.json, "w") as handle:
            json.dump(result, handle, indent=2, sort_keys=True)
        print("results: %s" % args.json)
    if args.profile:
        print("profiles: %s (hotspots.txt; open a .prof with snakeviz)" % args.profile)
    return 1 if result["errors"] and not result["cases"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
