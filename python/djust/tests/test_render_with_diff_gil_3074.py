"""``RustLiveView.render_with_diff`` releases the GIL while it renders (#3074).

The template render, HTML parse and VDOM diff run with the calling thread
detached from the interpreter, and re-attach only to call into Python (the
raw-object ``getattr`` fallback, bridged tags and filters). Three things are
pinned here:

* **Other Python threads run during a render.** On a GIL build a busy Python
  thread makes progress while another thread is inside ``render_with_diff``.
  Without the detach it would be frozen for the whole render.
* **Concurrent renders stay correct and isolated.** Each thread renders its
  own view and gets exactly the HTML and patches a serial render produces.
* **The registry lock order holds.** A render that calls a Python tag handler
  runs while another thread keeps re-registering tag handlers. A detached
  reader that held the registry read lock while waiting for the GIL would
  deadlock against a writer that holds the GIL while waiting for the write
  lock (``registry.rs`` "Lock order"); every lookup now attaches first.

The Rust side has its own pin: ``render_with_diff_detaches_3074`` in
``crates/djust_live/src/lib.rs``.
"""

from __future__ import annotations

import sys
import threading
import time

import pytest

rust = pytest.importorskip("djust._rust")

ROWS_TEMPLATE = (
    '<div dj-root><ul>{% for r in rows %}<li class="row">{{ r }}</li>{% endfor %}</ul></div>'
)


def _gil_enabled() -> bool:
    is_enabled = getattr(sys, "_is_gil_enabled", None)
    return True if is_enabled is None else bool(is_enabled())


def _rows(n: int, tag: str = "row") -> dict:
    return {"rows": [f"{tag} {i}" for i in range(n)]}


@pytest.mark.skipif(not _gil_enabled(), reason="progress is only gated by the GIL on a GIL build")
def test_a_python_thread_runs_while_render_with_diff_renders():
    """A busy Python thread keeps counting while another thread renders."""
    view = rust.RustLiveView(ROWS_TEMPLATE)
    view.update_state(_rows(20_000))

    counter = [0]
    stop = threading.Event()
    started = threading.Event()

    def spin() -> None:
        started.set()
        while not stop.is_set():
            counter[0] += 1

    t = threading.Thread(target=spin, daemon=True)
    t.start()
    started.wait(5)
    try:
        # Several renders so the measured window is well above the switch
        # interval; count only what happens INSIDE render_with_diff.
        during = 0
        for i in range(3):
            view.update_state(_rows(20_000, tag=f"r{i}"))
            before = counter[0]
            view.render_with_diff()
            during += counter[0] - before
    finally:
        stop.set()
        t.join(5)
    # Without the detach the spinner cannot run at all while this thread is
    # inside the Rust call (it never reaches a bytecode boundary to drop the
    # GIL), so `during` would be 0.
    assert during > 0, "no Python thread ran while render_with_diff rendered"


def test_concurrent_renders_match_serial_renders():
    """N threads, each rendering its own view, get the serial result."""
    n_threads, iters = 8, 15

    def script(idx: int) -> list:
        view = rust.RustLiveView(ROWS_TEMPLATE)
        out = []
        for i in range(iters):
            view.update_state(_rows(40 + (i % 5), tag=f"t{idx}-{i}"))
            html, patches, version = view.render_with_diff()
            out.append((html, patches is None, version))
        return out

    expected = {idx: script(idx) for idx in range(n_threads)}
    results: dict = {}
    errors: list = []
    barrier = threading.Barrier(n_threads)

    def worker(idx: int) -> None:
        try:
            barrier.wait(10)
            results[idx] = script(idx)
        except Exception as exc:  # noqa: BLE001 - reported below
            errors.append((idx, repr(exc)))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert not errors, errors
    for idx in range(n_threads):
        got = results[idx]
        # HTML is compared with new-node dj-ids stripped: ids for inserted
        # nodes come from a process-wide counter, so they depend on how the
        # threads interleave. Everything else must match the serial run.
        assert [_strip_ids(h) for h, _, _ in got] == [_strip_ids(h) for h, _, _ in expected[idx]]
        assert [(p, v) for _, p, v in got] == [(p, v) for _, p, v in expected[idx]]
        assert f"t{idx}-{iters - 1} 0" in got[-1][0]


def _strip_ids(html: str) -> str:
    import re

    return re.sub(r' dj-id="[^"]*"', "", html)


def test_tag_handler_renders_survive_concurrent_registration():
    """Renders that call a Python tag handler finish while another thread keeps
    re-registering handlers (the registry read/write lock order, #3074)."""
    from djust.template_tags import TagHandler

    class Probe(TagHandler):
        def render(self, args, context):
            return "probe-ok"

    name = "gil_lock_order_probe_3074"
    other = "gil_lock_order_churn_3074"
    rust.register_tag_handler(name, Probe())
    template = (
        "<div dj-root>{% " + name + " %}<ul>{% for r in rows %}<li>{{ r }}</li>{% endfor %}</ul>"
        "{% " + name + " %}</div>"
    )
    stop = threading.Event()
    rendered = [0]
    errors: list = []

    def churn() -> None:
        handler = Probe()
        while not stop.is_set():
            rust.register_tag_handler(other, handler)

    def render() -> None:
        try:
            view = rust.RustLiveView(template)
            deadline = time.monotonic() + 1.5
            i = 0
            while time.monotonic() < deadline:
                view.update_state(_rows(200, tag=f"n{i}"))
                html, _patches, _version = view.render_with_diff()
                assert html.count("probe-ok") == 2
                rendered[0] += 1
                i += 1
        except Exception as exc:  # noqa: BLE001 - reported below
            errors.append(repr(exc))

    churners = [threading.Thread(target=churn, daemon=True) for _ in range(2)]
    renderers = [threading.Thread(target=render, daemon=True) for _ in range(3)]
    try:
        for t in churners + renderers:
            t.start()
        for t in renderers:
            t.join(30)
        hung = [t for t in renderers if t.is_alive()]
    finally:
        stop.set()
        for t in churners:
            t.join(5)
        rust.unregister_tag_handler(other)
        rust.unregister_tag_handler(name)
    assert not hung, "a render did not finish: registry lock-order deadlock"
    assert not errors, errors
    assert rendered[0] > 0
