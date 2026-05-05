"""
Regression tests for concurrent same-session HTTP renders (#1353).

When two HTTP requests for the same ``(session, view_path)`` pair use the
in-memory state backend, they share the SAME ``RustLiveView`` Python
object. Concurrent calls to ``_sync_state_to_rust`` → ``update_state``
race inside Rust's ``RefCell::borrow_mut`` and panic with
``RuntimeError: Already borrowed``.

The fix wraps the ``update_state`` window in a per-``RustLiveView``
``threading.Lock`` (``rust_bridge._get_rust_view_lock``).
"""

from concurrent.futures import ThreadPoolExecutor

import pytest

from djust._rust import RustLiveView
from djust.mixins.rust_bridge import (
    _RUST_VIEW_LOCKS,
    _get_rust_view_lock,
)


class _MinimalRustBridge:
    """Skinny stand-in that exercises the same lock path as
    ``RustBridgeMixin._sync_state_to_rust`` without the full LiveView
    machinery (Django request, get_context_data, serialization, etc.).

    The only goal is to demonstrate that concurrent calls to
    ``update_state`` against a shared cached ``RustLiveView`` do not
    raise ``RuntimeError: Already borrowed`` once the lock is in place.
    """

    def __init__(self, rust_view: RustLiveView):
        self._rust_view = rust_view

    def sync(self, payload: dict):
        # Mirrors the locked window in
        # ``rust_bridge.RustBridgeMixin._sync_state_to_rust``.
        with _get_rust_view_lock(self._rust_view):
            self._rust_view.update_state(payload)


class TestConcurrentSyncStateToRust:
    """Two threads concurrently calling ``update_state`` on a shared view
    must not raise ``RuntimeError: Already borrowed`` (#1353)."""

    def test_two_threads_no_borrow_error(self):
        """Baseline reproduction: SHARED RustLiveView + 2 threads.

        Without the lock, this race is non-deterministic but typically
        fires within a handful of iterations (NYC Claims observed 17.5%
        500-rate at concurrency 2). With the lock, it must never fire.
        """
        view = RustLiveView("<div>{{count}}</div>")
        bridge_a = _MinimalRustBridge(view)
        bridge_b = _MinimalRustBridge(view)

        errors: list[BaseException] = []

        def worker(bridge, label, n):
            try:
                for i in range(n):
                    bridge.sync({"count": f"{label}-{i}"})
            except BaseException as e:  # noqa: BLE001
                errors.append(e)

        # 200 iterations × 2 threads is enough to surface the race
        # reliably without taking long under the lock.
        with ThreadPoolExecutor(max_workers=2) as ex:
            f1 = ex.submit(worker, bridge_a, "a", 200)
            f2 = ex.submit(worker, bridge_b, "b", 200)
            f1.result()
            f2.result()

        for e in errors:
            # Surface the offending exception in the failure message.
            if isinstance(e, RuntimeError) and "Already borrowed" in str(e):
                pytest.fail(
                    f"Concurrent _sync_state_to_rust raised borrow collision despite lock: {e!r}"
                )
            # Re-raise any other unexpected exception
            pytest.fail(f"Unexpected exception in worker: {e!r}")

    def test_lock_releases_after_call(self):
        """A third call after two completed calls must succeed without
        deadlocking — sanity check that the lock is properly released.
        """
        view = RustLiveView("<div>{{x}}</div>")
        bridge = _MinimalRustBridge(view)
        bridge.sync({"x": 1})
        bridge.sync({"x": 2})
        bridge.sync({"x": 3})  # Would deadlock if lock leaked from earlier
        # Render to confirm state actually applied
        html = view.render()
        assert ">3</div>" in html

    def test_distinct_views_get_distinct_locks(self):
        """Two independent ``RustLiveView`` instances must get different
        locks (otherwise we'd serialize unrelated views unnecessarily)."""
        view_a = RustLiveView("<div>{{x}}</div>")
        view_b = RustLiveView("<div>{{x}}</div>")
        lock_a = _get_rust_view_lock(view_a)
        lock_b = _get_rust_view_lock(view_b)
        assert lock_a is not lock_b, "Distinct RustLiveView instances must have distinct locks"

    def test_same_view_returns_same_lock(self):
        """Repeated lookups on the same view must return the SAME lock."""
        view = RustLiveView("<div>{{x}}</div>")
        lock_1 = _get_rust_view_lock(view)
        lock_2 = _get_rust_view_lock(view)
        assert lock_1 is lock_2, "Same RustLiveView must always return the same lock instance"

    def test_lock_dict_module_level(self):
        """Sanity check: the lock dict is the documented module-level
        ``_RUST_VIEW_LOCKS`` and entries are populated on first lookup.

        Note: ``id()`` reuse across freed objects is harmless — a stale
        entry under the same id() simply gets reused. We don't assert
        the absence of a pre-existing entry because previous tests in
        the same process may have left entries under ids that CPython
        has since recycled.
        """
        view = RustLiveView("<div>{{x}}</div>")
        lock = _get_rust_view_lock(view)
        assert id(view) in _RUST_VIEW_LOCKS, (
            "Lock should be populated in module-level dict after first lookup"
        )
        assert _RUST_VIEW_LOCKS[id(view)] is lock
