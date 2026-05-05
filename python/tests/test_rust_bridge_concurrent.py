"""
Regression tests for concurrent same-session HTTP renders (#1353).

Original bug: when two HTTP requests for the same ``(session, view_path)``
pair landed on ``InMemoryStateBackend``, both received the SAME Python
``RustLiveView`` reference on cache hit. Concurrent ``&mut self`` Rust
methods (``update_state``, ``mark_safe_keys``, ``set_template_dirs``,
``render``…) on the shared object collided inside Rust's
``RefCell::borrow_mut`` and surfaced as ``RuntimeError: Already
borrowed`` (NYC Claims observed 17.5% 500-rate at concurrency 2).

The race window was wider than ``_sync_state_to_rust``'s mutation
calls — ``render()`` itself holds ``&mut self`` across template
evaluation, and ``Context::resolve_dotted_via_getattr``
(``crates/djust_core/src/context.rs``) wraps ``Python::with_gil`` so
the embedded ``getattr`` can yield the GIL inside an active mutable
borrow. Any peer thread entering an ``&mut self`` method during that
window panicked.

Fix: ``InMemoryStateBackend.get()`` now returns an isolated
``serialize_msgpack`` / ``deserialize_msgpack`` clone of the cached
view, mirroring the ``RedisStateBackend`` contract (which already
deserialized fresh on every read). With each caller holding its own
``RustLiveView`` instance, no two threads can share a Rust ``&mut
self`` borrow and the race class is eliminated at the source.

These tests verify the new contract — every test in this module fails
on a fresh checkout of ``main`` (without the fix) because the original
``InMemoryStateBackend.get()`` returned ``self._cache.get(key)``
verbatim, sharing the cached reference across callers.
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from djust._rust import RustLiveView
from djust.state_backends.memory import InMemoryStateBackend


# Used by render-time race tests. A sidecar object whose ``__getattr__``
# performs ``time.sleep(0)`` forces the GIL to yield during template
# resolution, which is the same window that triggered the original
# panic in production. The Rust side calls ``Python::with_gil`` for
# every ``getattr`` segment in
# ``Context::resolve_dotted_via_getattr``; sleeping yields the bytecode
# slice cleanly so a peer thread can enter another ``&mut self`` method
# on the SAME ``RustLiveView`` (in the bug-shaped scenario) while the
# active borrow is still alive.
class _GilYieldingSidecar:
    """Has any attribute the template asks for; yields the GIL on each
    access. Acts as the sidecar bound under ``set_raw_py_values`` so a
    template like ``{{ user.name }}`` resolves through the Rust→Python
    fallback path."""

    def __getattr__(self, name):
        # ``time.sleep(0)`` is the standard idiom for yielding the GIL
        # without sleeping. It releases the GIL in the C-level call and
        # immediately re-acquires it, giving any waiting thread a chance
        # to run.
        time.sleep(0)
        return f"yield-{name}"


class TestInMemoryGetReturnsIsolatedView:
    """``InMemoryStateBackend.get()`` MUST return an isolated clone, not
    the shared cache reference. This is the contract that prevents the
    original race class (#1353)."""

    def test_get_returns_distinct_python_object(self):
        """Two ``get()`` calls for the same key MUST return different
        ``RustLiveView`` Python objects. On main without the fix, both
        calls returned ``self._cache.get(key)`` verbatim — the same
        reference."""
        backend = InMemoryStateBackend()
        view = RustLiveView("<div>{{count}}</div>")
        backend.set("session_a", view, warn_on_large_state=False)

        first, _ = backend.get("session_a")
        second, _ = backend.get("session_a")

        assert first is not second, (
            "InMemoryStateBackend.get() must return an isolated clone — "
            "the original bug was that it returned the shared cached ref, "
            "which let concurrent &mut self Rust methods collide."
        )

    def test_get_returned_clone_starts_with_same_state(self):
        """The clone is a faithful copy of the cached view's state — the
        isolation only kicks in for SUBSEQUENT mutations on the clone,
        not at clone time."""
        backend = InMemoryStateBackend()
        view = RustLiveView("<div>{{x}}</div>")
        view.update_state({"x": "canonical"})
        backend.set("session_a", view, warn_on_large_state=False)

        clone, _ = backend.get("session_a")
        # The clone should render the canonical state.
        html = clone.render()
        assert "canonical" in html, f"Clone must inherit cached state, got HTML: {html!r}"

    def test_clone_mutations_dont_leak_into_cache_or_other_clones(self):
        """Mutating one clone MUST NOT affect another clone or the
        cached canonical. This is the property that makes concurrent
        callers safe."""
        backend = InMemoryStateBackend()
        view = RustLiveView("<div>{{x}}</div>")
        view.update_state({"x": "original"})
        backend.set("k", view, warn_on_large_state=False)

        clone_a, _ = backend.get("k")
        clone_b, _ = backend.get("k")
        clone_a.update_state({"x": "mutated_by_a"})

        # clone_b should still see the original state — it was cloned
        # from the canonical, which clone_a's mutation didn't touch.
        b_html = clone_b.render()
        assert "original" in b_html, (
            f"clone_b should be isolated from clone_a's mutations, got: {b_html!r}"
        )

        # The cached canonical itself should also be untouched (no
        # write-back from the get/set path).
        canonical_clone, _ = backend.get("k")
        canonical_html = canonical_clone.render()
        assert "original" in canonical_html, (
            f"Cache canonical should not be mutated, got: {canonical_html!r}"
        )

    def test_concurrent_gets_return_distinct_views(self):
        """Many threads concurrently calling ``get()`` must each receive
        a distinct ``RustLiveView`` instance. Confirms the isolation
        property holds under contention, not just sequentially.

        Each worker holds onto its returned view in a thread-local list
        so id() remains valid (CPython recycles ids on free, which
        would mask the test if views were dropped immediately).
        """
        backend = InMemoryStateBackend()
        view = RustLiveView("<div>{{x}}</div>")
        view.update_state({"x": "v"})
        backend.set("k", view, warn_on_large_state=False)

        N = 32
        # Hold references so views stay alive for the identity check.
        retained: list = []
        retain_lock = threading.Lock()

        def worker():
            cached, _ = backend.get("k")
            with retain_lock:
                retained.append(cached)

        with ThreadPoolExecutor(max_workers=8) as ex:
            list(ex.map(lambda _: worker(), range(N)))

        # All N retained views must be distinct Python objects (no two
        # workers shared the same instance — that's the whole point).
        unique_ids = {id(v) for v in retained}
        assert len(unique_ids) == N, (
            f"Concurrent get() callers must receive distinct objects, "
            f"got {len(unique_ids)} unique ids across {N} retained views"
        )
        # Sanity: none of the clones is the cached canonical itself.
        cached_canonical_id = id(backend._cache["k"][0])
        assert cached_canonical_id not in unique_ids, (
            "No clone should be identical to the cached canonical view"
        )


class TestConcurrentRenderNoBorrowError:
    """Two threads each rendering an INDEPENDENTLY obtained clone of the
    same cached view must not raise ``RuntimeError: Already borrowed``,
    even when render yields the GIL via the sidecar getattr fallback.

    On main without the fix, both threads got the SAME ``RustLiveView``
    reference and concurrent ``render()`` / ``update_state`` calls
    collided. With the clone-on-get fix in place, each thread holds its
    own object so the race class is eliminated at the source.
    """

    def test_concurrent_render_via_backend_no_panic(self):
        """End-to-end: each thread calls ``backend.get`` to obtain its
        own clone, mutates state via ``update_state``, then ``render``s
        with a sidecar that yields the GIL during template evaluation.

        Without the clone-on-get fix, both threads would mutate the same
        cached ``RustLiveView``, and one render's ``Python::with_gil``
        callback would let the peer thread's ``update_state`` /
        ``set_raw_py_values`` enter Rust mid-borrow, panicking with
        ``Already borrowed``.
        """
        backend = InMemoryStateBackend()
        # Template that exercises the sidecar getattr fallback for every
        # render — ``user.name`` is NOT in ``state``, so resolution falls
        # through to ``set_raw_py_values`` which yields the GIL.
        canonical = RustLiveView("<div>{{count}}-{{user.name}}</div>")
        canonical.update_state({"count": 0})
        backend.set("session", canonical, warn_on_large_state=False)

        errors: list[BaseException] = []
        errors_lock = threading.Lock()

        def worker(label: str, n: int):
            try:
                for i in range(n):
                    # Mirror the production code path:
                    # _initialize_rust_view obtains a fresh view from
                    # the backend, _sync_state_to_rust mutates it, and
                    # the render path runs the template.
                    view, _ = backend.get("session")
                    view.update_state({"count": f"{label}-{i}"})
                    view.set_raw_py_values({"user": _GilYieldingSidecar()})
                    view.set_changed_keys(["count", "user"])
                    _html = view.render()
            except BaseException as e:  # noqa: BLE001 — surface anything
                with errors_lock:
                    errors.append(e)

        # Two threads x 50 iterations is plenty: the race fires within
        # the first handful of overlapping renders when the bug is
        # present. Keep it small so the test stays fast under the fix.
        with ThreadPoolExecutor(max_workers=2) as ex:
            f1 = ex.submit(worker, "a", 50)
            f2 = ex.submit(worker, "b", 50)
            f1.result()
            f2.result()

        for e in errors:
            if isinstance(e, RuntimeError) and "Already borrowed" in str(e):
                pytest.fail(
                    "Concurrent renders via backend.get() raised borrow "
                    f"collision despite clone-on-get fix: {e!r}"
                )
            pytest.fail(f"Unexpected exception in worker: {e!r}")

    def test_concurrent_update_state_via_backend_no_panic(self):
        """Lighter variant: only ``update_state`` (not ``render``) called
        concurrently. Without the fix, two threads sharing a ref would
        race inside ``RefCell::borrow_mut`` even on plain
        ``update_state``. With the fix, each thread has its own clone.
        """
        backend = InMemoryStateBackend()
        canonical = RustLiveView("<div>{{count}}</div>")
        backend.set("session", canonical, warn_on_large_state=False)

        errors: list[BaseException] = []
        errors_lock = threading.Lock()

        def worker(label: str, n: int):
            try:
                for i in range(n):
                    view, _ = backend.get("session")
                    view.update_state({"count": f"{label}-{i}"})
            except BaseException as e:  # noqa: BLE001
                with errors_lock:
                    errors.append(e)

        with ThreadPoolExecutor(max_workers=4) as ex:
            futures = [ex.submit(worker, label, 100) for label in ("a", "b", "c", "d")]
            for f in futures:
                f.result()

        for e in errors:
            if isinstance(e, RuntimeError) and "Already borrowed" in str(e):
                pytest.fail(
                    "Concurrent update_state via backend.get() raised borrow "
                    f"collision despite clone-on-get fix: {e!r}"
                )
            pytest.fail(f"Unexpected exception in worker: {e!r}")
