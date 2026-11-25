#!/usr/bin/env python3
"""Benchmark: client work per patch turn in a real browser, by page size (#4001).

The issue's target is that the browser's work for one turn stays flat as the
page grows: an unrelated counter update should cost the same on a page of 100
elements as on one of 10,000. This measures it.

For each page size the script renders a real explicit-exposure LiveView twice
through the Rust engine (``render_with_diff``): the mount, then the same view
with one value changed, which gives the server's own patch list. A headless
Chromium (Playwright) loads a page holding the mounted root and the shipped
``client.js`` with no transport, then hands the patch frame to the client's
response handler ``--turns`` times. The timer covers one whole turn on the
client: patch application plus everything ``reinitAfterDOMUpdate`` does
afterwards (event binding, hooks, model binding, scans). No network, no
server time.

Two workloads (``--shapes``):

``counter``  a counter above a list of rows; the turn changes only the counter.
``cell``     the same page; the turn changes the text of the LAST row, deep in
             the list (a ``SetText`` that has no dj-id of its own).

Each turn also counts ``querySelectorAll`` / ``querySelector`` / ``closest``
calls made by the client.

Run::

    PYTHONPATH=python python scripts/bench_client_patch_turn.py
    python scripts/bench_client_patch_turn.py --sizes 100,1000,10000 --turns 60 --json out.json

Chromium: Playwright's bundled browser when installed, else ``--chrome`` (a
Chrome/Chromium binary; ``/usr/bin/google-chrome`` is tried). Exit code is 0
unless a turn's patches fail: a measurement tool, not a gate.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import sys
import tempfile
from typing import Any, Dict, List

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.join(ROOT, "python") not in sys.path:
    sys.path.insert(0, os.path.join(ROOT, "python"))

CLIENT_JS = os.path.join(ROOT, "python", "djust", "static", "djust", "client.js")
WARMUP = 10

# Instrumentation installed before the client loads: per-turn call counters for
# the DOM queries the issue counts. Counting stays off outside a measured turn.
_COUNTERS_JS = """
window.__djb = {on: false, qsa: 0, qs: 0, closest: 0};
for (const proto of [Element.prototype, Document.prototype, DocumentFragment.prototype]) {
    const qsa = proto.querySelectorAll, qs = proto.querySelector;
    proto.querySelectorAll = function (s) { if (window.__djb.on) window.__djb.qsa++; return qsa.call(this, s); };
    proto.querySelector = function (s) { if (window.__djb.on) window.__djb.qs++; return qs.call(this, s); };
}
const closest = Element.prototype.closest;
Element.prototype.closest = function (s) { if (window.__djb.on) window.__djb.closest++; return closest.call(this, s); };
window.DJUST_USE_WEBSOCKET = false;
"""

# One measured turn: hand the frame to the response handler and wait for it.
_TURN_JS = """
async ([patches, version, n]) => {
    for (const p of patches) if (p.type === 'SetText') p.text = String(n);
    const c = window.__djb;
    c.qsa = c.qs = c.closest = 0;
    c.on = true;
    const t0 = performance.now();
    await window.__djbHandle({type: 'patch', patches, version}, 'bump', null);
    const ms = performance.now() - t0;
    c.on = false;
    const marker = document.querySelector('[data-bench-target]');
    return {ms, qsa: c.qsa, qs: c.qs, closest: c.closest,
            text: marker ? marker.textContent : null};
}
"""


def _configure() -> None:
    import django
    from django.conf import settings

    settings.configure(
        SECRET_KEY="bench-secret-key-not-for-production-0123456789",
        DEBUG=False,
        INSTALLED_APPS=[
            "django.contrib.contenttypes",
            "django.contrib.auth",
            "django.contrib.sessions",
            "channels",
            "djust",
        ],
        DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
        TEMPLATES=[{"BACKEND": "django.template.backends.django.DjangoTemplates"}],
        USE_TZ=True,
        LOGGING_CONFIG=None,
    )
    django.setup()
    from django.core.management import call_command

    call_command("migrate", verbosity=0, run_syncdb=True)


def _view_class():
    from djust import LiveView, state
    from djust.decorators import event_handler

    class BenchView(LiveView):
        template = (
            '<div dj-root><header><button dj-click="bump">+</button>'
            "<span {% if shape == 'counter' %}data-bench-target{% endif %}>{{ count }}</span>"
            '</header><ul>{% for r in rows %}<li class="row">'
            "<span {% if forloop.last and shape == 'cell' %}data-bench-target{% endif %}>"
            "{% if forloop.last %}{{ last }}{% else %}{{ r }}{% endif %}</span></li>"
            "{% endfor %}</ul></div>"
        )
        count = state(0)
        last = state(0)
        rows = state(default_factory=list)
        shape = state("counter")

        @event_handler
        def bump(self) -> None:
            self.count += 1

    return BenchView


def _frames(elements: int, shape: str) -> Dict[str, Any]:
    """Mount HTML (with dj-ids) and the patch list for one turn of ``shape``."""
    from django.test import RequestFactory

    from djust.testing import with_live_session

    rows = max(1, (elements - 4) // 2)  # div, header, button, span, ul + li/span per row
    request = with_live_session(RequestFactory().get("/"))
    view = _view_class()()
    view.rows = list(range(rows))
    view.shape = shape
    mount_html, _, version = view.render_with_diff(request)
    if shape == "counter":
        view.count = 1
    else:
        view.last = 1
    _, patches, version = view.render_with_diff(request)
    return {"mount": mount_html, "patches": json.loads(patches or "[]"), "version": version}


def _launch(playwright, chrome: str):
    try:
        return playwright.chromium.launch()
    except Exception:  # bundled browser missing: use the system one
        return playwright.chromium.launch(executable_path=chrome)


def run(sizes: List[int], shapes: List[str], turns: int, chrome: str) -> List[Dict[str, Any]]:
    from playwright.sync_api import sync_playwright

    client = open(CLIENT_JS, encoding="utf-8").read()
    # The response handler is private to the bundle: expose it for the harness.
    marker = "\nasync function handleServerResponse("
    if marker not in client:
        raise SystemExit("client.js no longer defines handleServerResponse")
    client = client.replace(
        marker, "\nwindow.__djbHandle = (...a) => handleServerResponse(...a);" + marker, 1
    )
    results = []
    # Render every frame before the browser starts (Django's DB connection
    # must not be touched from inside Playwright's event loop).
    frames = {(shape, size): _frames(size, shape) for shape in shapes for size in sizes}
    tmp = tempfile.mkdtemp(prefix="djust-bench-client-")
    try:
        with sync_playwright() as p:
            browser = _launch(p, chrome)
            for shape in shapes:
                for size in sizes:
                    frame = frames[(shape, size)]
                    page_path = os.path.join(tmp, "page-%s-%d.html" % (shape, size))
                    with open(page_path, "w", encoding="utf-8") as fh:
                        fh.write(
                            "<!doctype html><html><head></head><body>%s</body></html>"
                            % frame["mount"]
                        )
                    page = browser.new_page()
                    page.add_init_script(_COUNTERS_JS)
                    errors: List[str] = []
                    page.on("pageerror", lambda exc, errors=errors: errors.append(str(exc)))
                    page.goto("file://" + page_path)
                    page.add_script_tag(content=client)
                    page.evaluate("() => new Promise(r => setTimeout(r, 50))")
                    if errors:
                        raise SystemExit("client failed to load: %s" % errors[0])
                    elements = page.evaluate(
                        "() => document.querySelector('[dj-root]').querySelectorAll('*').length + 1"
                    )
                    samples = []
                    version = frame["version"]
                    for n in range(WARMUP + turns):
                        version += 1
                        out = page.evaluate(_TURN_JS, [frame["patches"], version, n + 2])
                        if out["text"] != str(n + 2):
                            raise SystemExit("turn did not apply (%s, %d): %r" % (shape, size, out))
                        if n >= WARMUP:
                            samples.append(out)
                    page.close()
                    ms = sorted(s["ms"] for s in samples)
                    row = {
                        "shape": shape,
                        "elements": elements,
                        "patches": [p["type"] for p in frame["patches"]],
                        "p50_ms": round(statistics.median(ms), 3),
                        "p95_ms": round(ms[int(len(ms) * 0.95) - 1], 3),
                        "mean_ms": round(statistics.fmean(ms), 3),
                        "qsa": samples[-1]["qsa"],
                        "qs": samples[-1]["qs"],
                        "closest": samples[-1]["closest"],
                    }
                    results.append(row)
                    print(
                        "%-8s %6d el  p50 %7.3f ms  mean %7.3f ms  p95 %7.3f ms  qsa %3d  qs %3d  closest %3d  %s"
                        % (
                            shape,
                            elements,
                            row["p50_ms"],
                            row["mean_ms"],
                            row["p95_ms"],
                            row["qsa"],
                            row["qs"],
                            row["closest"],
                            ",".join(row["patches"]),
                        ),
                        flush=True,
                    )
            browser.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return results


def main(argv: List[str] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--sizes", default="100,1000,10000")
    parser.add_argument("--shapes", default="counter,cell")
    parser.add_argument("--turns", type=int, default=50)
    parser.add_argument("--chrome", default="/usr/bin/google-chrome")
    parser.add_argument("--json", help="write the rows to this file")
    args = parser.parse_args(argv)
    _configure()
    rows = run(
        [int(s) for s in args.sizes.split(",")],
        args.shapes.split(","),
        args.turns,
        args.chrome,
    )
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
