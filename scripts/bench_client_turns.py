#!/usr/bin/env python3
"""Benchmark: the browser side of a complete turn on the five baseline workloads (#4314).

``scripts/bench_turn_transports.py`` (#3657) measures the server side of a turn.
This measures the other half in a real browser, on the same five pages (form,
1,000-row table, dashboard, server push, editor), served by a real server:

* the same Django project and views as the server bench (``_configure`` and
  ``_install_views`` are imported from it), served in-process by uvicorn with
  ``LiveViewConsumer`` on ``/ws/live/``;
* a headless Chromium (Playwright) loads each page, the shipped client mounts
  it over WebSocket, or over the page-POST fallback with WebSocket and
  EventSource switched off, and the script drives real DOM events: a typed
  value on a ``dj-input`` (form, editor) or a click on a ``dj-click`` button
  (table, dashboard, push).

Per turn, from ``performance.now()`` in the page:

``send``     input event dispatched -> the event frame handed to
             ``WebSocket.send`` / ``fetch`` (binding, rate-limit wrapper,
             parameter collection, serialization).
``apply``    the time inside the client's response handler
             (``handleServerResponse``: ``applyPatches`` or the whole-page
             morph, then ``reinitAfterDOMUpdate``), summed over the turn's
             answers (a push turn has two).
``total``    input event -> the last answer applied: includes the server's
             turn and the loopback network, so it is context, not a client
             figure.
``long``     main-thread long tasks (>= 50 ms, ``PerformanceObserver``) that
             started during the turn or in the 50 ms settle after it: count
             and longest.
``down``     bytes of the answer (WebSocket message length; ``fetch``
             resource ``decodedBodySize``).

Cells (``--cells``): ``ws`` (the shipped default; state saved), ``http`` (the
page-POST fallback with the save: answered with patches) and ``http-nosave``
(``automatic_state = False``: every POST re-mounts and answers with the whole
page, e.g. ~193 KB of JSON for the table). The push workload has no HTTP cell
(no live channel).

Two changes to the server bench's pages, both to let a browser drive them: the
table, dashboard and push pages get one ``<button dj-click>`` (the server bench
calls the handler directly), and the form and editor inputs carry
``dj-debounce="0"``. The shipped default debounces a typed ``dj-input`` by
300 ms, a fixed timer by design: with it, ``send`` is that 300 ms plus the
figure measured here (one ``setTimeout(0)`` tick remains).

The page loads the unminified ``client-core.js`` in place of
``client-core.min.js`` (same source; the timing hook in the response handler
is inserted into it), and the chunk files as shipped.

Run::

    make benchmark-client-turns                          # ~2 min here
    python scripts/bench_client_turns.py --turns 30 --json out.json
    python scripts/bench_client_turns.py --shapes table --cells ws,http-nosave
    git show <rev>:python/djust/static/djust/client-core.js > /tmp/old.js
    python scripts/bench_client_turns.py --client /tmp/old.js   # an older client

Chromium: Playwright's bundled browser when installed, else ``--chrome``
(``/usr/bin/google-chrome`` is tried). Exit code is 0 unless a turn fails: a
measurement tool, not a gate. ``scripts/bench_client_patch_turn.py`` (#4001)
is the companion that measures one patch turn by page size.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import statistics
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(ROOT, "python"), os.path.join(ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import bench_turn_transports as server_bench  # noqa: E402  (path set up above)

STATIC = os.path.join(ROOT, "python", "djust", "static", "djust")
SHAPES = server_bench.SHAPES
CELLS = ("ws", "http", "http-nosave")
#: The trigger each page gets: (template text to find, text to put in its place).
TRIGGERS = {
    "form": ('dj-input="validate"', 'dj-input="validate" dj-debounce="0"'),
    "editor": ('dj-input="edit"', 'dj-input="edit" dj-debounce="0"'),
    "table": ("<div dj-root>", '<div dj-root><button id="djb-go" dj-click="bump">go</button>'),
    "dashboard": ("<div dj-root>", '<div dj-root><button id="djb-go" dj-click="tick">go</button>'),
    "push": ("<div dj-root>", '<div dj-root><button id="djb-go" dj-click="work">go</button>'),
}
WARMUP = 3
SETTLE_MS = 50

# Installed before any page script. Records the event frame's send time, answer
# bytes, long tasks; pins the transport for the HTTP cells (the same pins as
# tests/playwright/_transports.py: the page's config script assigns
# DJUST_USE_WEBSOCKET after init scripts run).
_INIT_JS = """
window.__djb = {armed: false, sendAt: null, down: 0, applies: [], longtasks: []};
(() => {
    const b = window.__djb;
    const W = window.WebSocket;
    window.WebSocket = class extends W {
        constructor(...a) {
            super(...a);
            this.addEventListener('message', (e) => {
                if (!b.armed) return;
                const d = e.data;
                b.down += typeof d === 'string' ? d.length : (d.byteLength || d.size || 0);
            });
        }
        send(frame) {
            if (b.armed && b.sendAt === null) b.sendAt = performance.now();
            return super.send(frame);
        }
    };
    const f = window.fetch;
    window.fetch = function (...a) {
        const init = a[1] || {};
        if (b.armed && b.sendAt === null && (init.method || 'GET').toUpperCase() === 'POST') {
            b.sendAt = performance.now();
        }
        return f.apply(this, a);
    };
    try {
        new PerformanceObserver((list) => {
            for (const e of list.getEntries()) b.longtasks.push([e.startTime, e.duration]);
        }).observe({type: 'longtask', buffered: true});
    } catch (_e) { /* no long-task timing in this browser */ }
})();
"""
_PIN_HTTP = (
    "Object.defineProperty(window, 'DJUST_USE_WEBSOCKET', "
    "{configurable: false, get: () => false, set: () => {}});"
    "Object.defineProperty(window, 'EventSource', "
    "{configurable: false, get: () => undefined, set: () => {}});"
)

# The response handler is private to the bundle: wrap it with a timer.
_HANDLER = "\nasync function handleServerResponse("
_HANDLER_WRAP = """
async function handleServerResponse(data, ...rest) {
    const b = window.__djb, t0 = performance.now();
    try {
        return await __djbHandleServerResponse(data, ...rest);
    } finally {
        if (b && b.armed && data && (data.patches !== undefined || typeof data.html === 'string')) {
            b.applies.push([t0, performance.now(), typeof data.html === 'string' && data.patches === undefined]);
        }
    }
}
async function __djbHandleServerResponse("""

# One turn: dispatch the input, wait for the answers to be applied, settle.
_TURN_JS = (
    """
async ([shape, field, value, expected]) => {
    const b = window.__djb;
    b.sendAt = null; b.down = 0; b.applies = []; b.armed = true;
    performance.clearResourceTimings();
    const lt0 = b.longtasks.length;
    const t0 = performance.now();
    if (shape === 'form' || shape === 'editor') {
        const el = shape === 'form'
            ? document.querySelector('input[name="' + field + '"]')
            : document.querySelector('textarea');
        el.value = value;
        el.dispatchEvent(new Event('input', {bubbles: true}));
    } else {
        document.getElementById('djb-go').click();
    }
    const deadline = t0 + 20000;
    while (b.applies.length < expected) {
        if (performance.now() > deadline) { b.armed = false; return {error: 'timeout', applies: b.applies.length}; }
        await new Promise((r) => setTimeout(r, 1));
    }
    const end = b.applies[b.applies.length - 1][1];
    await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
    await new Promise((r) => setTimeout(r, %d));
    b.armed = false;
    for (const e of performance.getEntriesByType('resource')) {
        if (e.initiatorType === 'fetch' && e.startTime >= t0) b.down += e.decodedBodySize;
    }
    const long = b.longtasks.slice(lt0).filter(([s]) => s >= t0);
    return {
        send: b.sendAt === null ? null : b.sendAt - t0,
        apply: b.applies.reduce((s, [a, z]) => s + (z - a), 0),
        total: end - t0,
        whole: b.applies.some((x) => x[2]),
        long_n: long.length,
        long_max: long.reduce((m, [, d]) => Math.max(m, d), 0),
        down: b.down,
    };
}
"""
    % SETTLE_MS
)


# --------------------------------------------------------------------------- #
# Server
# --------------------------------------------------------------------------- #


def _install_browser_pages(views: Dict[Tuple[str, str], type]) -> None:
    """Register /cb/<shape>-<mode>/: the bench views with a browser trigger."""
    from django.urls import path

    module = sys.modules[server_bench.VIEW_MODULE]
    urls = sys.modules[server_bench.URL_MODULE]
    for (shape, mode), cls in views.items():
        old, new = TRIGGERS[shape]
        if old not in cls.template:
            raise SystemExit("the %s page no longer contains %r" % (shape, old))
        name = cls.__name__ + "_browser"
        sub = type(
            name,
            (cls,),
            # Every input of the form gets the trigger; the first match elsewhere.
            {
                "__module__": server_bench.VIEW_MODULE,
                "template": cls.template.replace(old, new, -1 if shape == "form" else 1),
            },
        )
        setattr(module, name, sub)
        urls.urlpatterns.append(path("cb/%s-%s/" % (shape, mode), sub.as_view()))


def _asgi_app():
    from channels.auth import AuthMiddlewareStack
    from channels.routing import ProtocolTypeRouter, URLRouter
    from django.core.asgi import get_asgi_application
    from django.urls import path

    from djust.websocket import LiveViewConsumer

    return ProtocolTypeRouter(
        {
            "http": get_asgi_application(),
            "websocket": AuthMiddlewareStack(
                URLRouter([path("ws/live/", LiveViewConsumer.as_asgi())])
            ),
        }
    )


class Server:
    """uvicorn on a loopback port, in a thread of this process."""

    def __init__(self, app) -> None:
        import uvicorn

        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        config = uvicorn.Config(
            app, host="127.0.0.1", port=self.port, log_level="warning", lifespan="off"
        )
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self) -> "Server":
        self.thread.start()
        deadline = time.monotonic() + 30
        while not self.server.started:
            if time.monotonic() > deadline or not self.thread.is_alive():
                raise SystemExit("uvicorn did not start")
            time.sleep(0.05)
        return self

    def __exit__(self, *exc: Any) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)


# --------------------------------------------------------------------------- #
# Browser
# --------------------------------------------------------------------------- #


def _client_core(path: Optional[str] = None) -> str:
    source = open(path or os.path.join(STATIC, "client-core.js"), encoding="utf-8").read()
    if source.count(_HANDLER) != 1:
        raise SystemExit("client-core.js no longer defines handleServerResponse once")
    return source.replace(_HANDLER, _HANDLER_WRAP, 1)


_STATIC_RE = re.compile(r"/djust/((?:chunks/)?[\w.-]+\.js)$")


def _serve_static(core: str):
    """A Playwright route handler for the client's script URLs."""

    def handler(route) -> None:
        url = route.request.url.split("?", 1)[0]
        match = _STATIC_RE.search(url)
        if match is None:
            route.continue_()
            return
        name = match.group(1)
        if name == "client-core.min.js":
            body = core
        else:
            file = os.path.join(STATIC, name)
            if not os.path.isfile(file):
                route.fulfill(status=404, body="")
                return
            body = open(file, encoding="utf-8").read()
        route.fulfill(status=200, content_type="application/javascript", body=body)

    return handler


def _launch(playwright, chrome: str):
    try:
        return playwright.chromium.launch()
    except Exception:  # bundled browser missing: use the system one
        return playwright.chromium.launch(executable_path=chrome)


def pct(samples: List[float], p: float) -> float:
    return server_bench.pct(samples, p)


def _summary(samples: List[Dict[str, Any]], key: str) -> Dict[str, float]:
    values = [s[key] for s in samples if s[key] is not None]
    if not values:
        return {"p50": None, "p95": None}
    return {"p50": round(statistics.median(values), 2), "p95": round(pct(values, 95), 2)}


def measure_cell(
    browser, base: str, core: str, shape: str, cell: str, turns: int
) -> Dict[str, Any]:
    mode = "nosave" if cell == "http-nosave" else "save"
    context = browser.new_context()
    page = context.new_page()
    page.add_init_script(_INIT_JS + (_PIN_HTTP if cell != "ws" else ""))
    page.route(lambda url: bool(_STATIC_RE.search(url.split("?", 1)[0])), _serve_static(core))
    errors: List[str] = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    sockets: List[str] = []
    page.on("websocket", lambda ws: sockets.append(ws.url))
    page.goto("%s/cb/%s-%s/" % (base, shape, mode), wait_until="load")
    if cell == "ws":
        page.wait_for_function(
            "() => window.djust && window.djust._mountReady === true", timeout=30000
        )
    else:
        page.wait_for_function(
            "() => window.djust && typeof window.djust.handleEvent === 'function'"
        )
    page.wait_for_timeout(300)
    if errors:
        raise SystemExit("%s/%s: the page failed to load: %s" % (shape, cell, errors[0]))
    if (cell == "ws") != bool(sockets):
        raise SystemExit("%s/%s: the page did not use the transport it claims" % (shape, cell))
    elements = page.evaluate("() => document.querySelectorAll('*').length")
    expected = 2 if shape == "push" else 1
    samples = []
    for n in range(1, WARMUP + turns + 1):
        event, params = server_bench._event_for(shape, n)
        out = page.evaluate(
            _TURN_JS, [shape, params.get("field", ""), params.get("value", ""), expected]
        )
        if "error" in out:
            raise SystemExit("%s/%s turn %d: %r (%s)" % (shape, cell, n, out, errors[:1]))
        if n > WARMUP:
            samples.append(out)
    if errors:
        raise SystemExit("%s/%s: page error during the turns: %s" % (shape, cell, errors[0]))
    context.close()
    return {
        "shape": shape,
        "cell": cell,
        "turns": len(samples),
        "elements": elements,
        "answer": "page" if samples[-1]["whole"] else "patches",
        "send": _summary(samples, "send"),
        "apply": _summary(samples, "apply"),
        "total": _summary(samples, "total"),
        "long_tasks": sum(s["long_n"] for s in samples),
        "long_max_ms": round(max(s["long_max"] for s in samples), 1),
        "down_bytes": int(statistics.median(s["down"] for s in samples)),
    }


def run(
    shapes: List[str],
    cells: List[str],
    turns: int,
    rows: int,
    chrome: str,
    client: Optional[str] = None,
) -> Dict[str, Any]:
    from playwright.sync_api import sync_playwright

    tmp_dir = server_bench._configure()
    try:
        views = server_bench._install_views(rows)
        _install_browser_pages(views)
        core = _client_core(client)
        results = []
        with Server(_asgi_app()) as server, sync_playwright() as p:
            browser = _launch(p, chrome)
            base = "http://127.0.0.1:%d" % server.port
            for shape in shapes:
                for cell in cells:
                    if cell != "ws" and (shape, "http") in server_bench.NOT_APPLICABLE:
                        continue
                    row = measure_cell(browser, base, core, shape, cell, turns)
                    results.append(row)
                    print(_format_row(row), flush=True)
            version = browser.version
            browser.close()
        info = server_bench.machine()
        info["browser"] = "Chromium %s (headless)" % version
        return {
            "machine": info,
            "turns": turns,
            "warmup": WARMUP,
            "rows": rows,
            "settle_ms": SETTLE_MS,
            "cells": results,
        }
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _format_row(row: Dict[str, Any]) -> str:
    return (
        "%-9s %-11s %5d el  send %5s/%5s  apply %6s/%6s  total %6s/%6s ms  "
        "long %d (max %s)  down %d B  %s"
        % (
            row["shape"],
            row["cell"],
            row["elements"],
            row["send"]["p50"],
            row["send"]["p95"],
            row["apply"]["p50"],
            row["apply"]["p95"],
            row["total"]["p50"],
            row["total"]["p95"],
            row["long_tasks"],
            row["long_max_ms"],
            row["down_bytes"],
            row["answer"],
        )
    )


def _csv(value: str, allowed: Tuple[str, ...]) -> List[str]:
    items = [v.strip() for v in value.split(",") if v.strip()]
    bad = [v for v in items if v not in allowed]
    if bad:
        raise SystemExit("unknown value(s) %s; choose from %s" % (bad, ",".join(allowed)))
    return items


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--shapes", default=",".join(SHAPES))
    parser.add_argument("--cells", default=",".join(CELLS))
    parser.add_argument("--turns", type=int, default=30, help="measured turns per cell")
    parser.add_argument("--rows", type=int, default=1000, help="rows in the table page")
    parser.add_argument("--chrome", default="/usr/bin/google-chrome")
    parser.add_argument(
        "--client",
        help="an unminified client-core.js to load instead of this checkout's "
        "(e.g. one from an older commit, for a before/after)",
    )
    parser.add_argument("--json", help="write the result to this file")
    args = parser.parse_args(argv)
    result = run(
        _csv(args.shapes, SHAPES),
        _csv(args.cells, CELLS),
        args.turns,
        args.rows,
        args.chrome,
        args.client,
    )
    print("%(browser)s  djust %(djust)s  python %(python)s  %(cpu)s x%(cpus)s" % result["machine"])
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2)
            fh.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
