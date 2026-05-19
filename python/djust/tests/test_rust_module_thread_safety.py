"""Python ``threading`` smoke test for the free-threaded ``djust._rust`` module (#1432).

HONEST SCOPE NOTE — this is a SMOKE TEST, not a race detector.

``djust._rust`` is declared ``#[pymodule(gil_used = false)]`` (see
``crates/djust_live/src/lib.rs``). On a free-threaded interpreter
(CPython ``3.13t`` / ``3.14t``) that declaration tells CPython NOT to
auto-re-enable the GIL when the module is imported.

This test runs on whatever interpreter pytest is invoked with — in this
repo's ``.venv`` that is **CPython 3.12, a GIL-enabled build**. Under the
GIL, Python-level ``threading.Thread`` workers never execute the
underlying Rust code *truly* concurrently, so this file **cannot** catch
a no-GIL-only data race. The real gate for the ``Sync`` contracts is the
Rust ``std::thread`` stress test in
``crates/djust_templates/tests/free_threaded_safety.rs`` (and the
companion ``crates/djust_vdom/tests/free_threaded_safety.rs``), which
exercises genuine OS-thread parallelism with no GIL involved at all.

What this file DOES verify:

* ``import djust._rust`` succeeds — i.e. the ``gil_used = false`` slot did
  not break module init on a GIL'd interpreter (the declaration must be
  backwards-compatible).
* The Python-facing call paths (``render_template``, ``diff_html``,
  ``compute_template_hash``, ``RustLiveView``, the ``Rust*`` components)
  survive concurrent ``threading.Thread`` access without raising —
  catching gross logic errors and any PyO3 borrow-checker panic
  (``already borrowed``) surfaced by concurrent same-instance access.
* On a free-threaded build, the module loads without CPython re-enabling
  the GIL (guarded by ``skipif`` — a no-op on GIL'd CI, a real assertion
  the day a ``3.14t`` runner is added).
"""

from __future__ import annotations

import importlib
import sys
import threading

import pytest

# Number of concurrent Python threads. Kept modest — the point is
# call-path coverage, not load.
N_THREADS = 12
ITERS = 50


def _gil_enabled() -> bool:
    """True on a standard GIL build, False on a free-threaded (``t``) build."""
    is_enabled = getattr(sys, "_is_gil_enabled", None)
    if is_enabled is None:
        # Pre-3.13: no free-threaded build exists, GIL is always on.
        return True
    return bool(is_enabled())


def test_rust_module_imports_cleanly():
    """``import djust._rust`` must succeed — ``gil_used = false`` must not
    break module init on the GIL'd interpreter pytest runs under."""
    rust = importlib.import_module("djust._rust")
    # A handful of the public entrypoints must be present.
    for name in ("render_template", "diff_html", "compute_template_hash", "RustLiveView"):
        assert hasattr(rust, name), f"djust._rust is missing {name!r}"


@pytest.mark.skipif(
    _gil_enabled(),
    reason="free-threaded assertion — only meaningful on a python3.13t/3.14t build",
)
def test_module_does_not_reenable_gil_on_free_threaded_build():
    """On a free-threaded build, importing ``djust._rust`` must NOT cause
    CPython to re-enable the GIL — that is the whole point of
    ``#[pymodule(gil_used = false)]``.

    This is a no-op on GIL'd CI (skipped above) and becomes a real
    assertion the day a ``3.14t`` runner is added (Action #1079 follow-up)."""
    importlib.import_module("djust._rust")
    assert not _gil_enabled(), (
        "importing djust._rust re-enabled the GIL on a free-threaded build — "
        "gil_used = false should have prevented this"
    )


def _run_concurrently(worker) -> list[BaseException]:
    """Run ``worker(thread_index)`` on ``N_THREADS`` threads; collect any
    exception each thread raised. Returns the list of escaped exceptions
    (empty == clean run)."""
    errors: list[BaseException] = []
    errors_lock = threading.Lock()
    barrier = threading.Barrier(N_THREADS)

    def wrapped(idx: int) -> None:
        try:
            barrier.wait()  # maximise overlap
            worker(idx)
        except BaseException as exc:  # noqa: BLE001 - test harness collects all
            with errors_lock:
                errors.append(exc)

    threads = [threading.Thread(target=wrapped, args=(i,)) for i in range(N_THREADS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return errors


def test_concurrent_render_template_smoke():
    """Many threads call ``render_template`` concurrently against shared
    and per-thread inputs — exercises the template engine call path."""
    rust = importlib.import_module("djust._rust")

    def worker(idx: int) -> None:
        for i in range(ITERS):
            out = rust.render_template("<p>{{ greeting }}</p>", {"greeting": f"t{idx}-{i}"})
            assert out == f"<p>t{idx}-{i}</p>", f"thread {idx} got {out!r}"

    errors = _run_concurrently(worker)
    assert not errors, f"render_template raised under concurrency: {errors}"


def test_concurrent_diff_html_smoke():
    """Many threads call ``diff_html`` concurrently — exercises the VDOM
    parse + diff call path."""
    rust = importlib.import_module("djust._rust")

    def worker(idx: int) -> None:
        for i in range(ITERS):
            patches = rust.diff_html(f"<p>old{idx}</p>", f"<p>new{idx}-{i}</p>")
            assert patches, f"thread {idx} iter {i}: empty diff"

    errors = _run_concurrently(worker)
    assert not errors, f"diff_html raised under concurrency: {errors}"


def test_concurrent_compute_template_hash_is_stable():
    """Many threads hash the same source — every thread must agree."""
    rust = importlib.import_module("djust._rust")
    source = "<div>{{ a }}{% for x in xs %}{{ x }}{% endfor %}</div>"
    expected = rust.compute_template_hash(source)

    def worker(idx: int) -> None:
        for _ in range(ITERS):
            assert rust.compute_template_hash(source) == expected

    errors = _run_concurrently(worker)
    assert not errors, f"compute_template_hash diverged under concurrency: {errors}"


def test_concurrent_rust_liveview_distinct_instances():
    """Each thread builds its OWN ``RustLiveView`` and renders it — distinct
    pyclass instances must be fully independent under concurrent access."""
    rust = importlib.import_module("djust._rust")

    def worker(idx: int) -> None:
        view = rust.RustLiveView("<p>{{ count }}</p>")
        for i in range(ITERS):
            view.update_state({"count": idx * 1000 + i})
            html = view.render()
            assert str(idx * 1000 + i) in html, f"thread {idx} iter {i}: {html!r}"

    errors = _run_concurrently(worker)
    assert not errors, f"RustLiveView raised under concurrency: {errors}"
