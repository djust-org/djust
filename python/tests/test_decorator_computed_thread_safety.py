"""Regression tests for #1289 — @computed cache-dict mutation is thread-safe.

Before the fix, concurrent property access from background threads could
race against template rendering on the main loop, potentially causing
KeyError or stale reads due to the cache-dict mutation not being
protected by a lock.
"""

import threading

from djust import LiveView
from djust.decorators import computed


class TestComputedThreadSafety:
    """#1289: @computed memoized cache is thread-safe."""

    def test_concurrent_computed_access_no_errors(self):
        """50 threads accessing a memoized @computed property concurrently
        should complete without KeyError or stale reads."""
        errors = []

        class DashView(LiveView):
            def update(self):
                pass

            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.items = [
                    {"category": "A", "price": 10, "qty": 2},
                    {"category": "A", "price": 5, "qty": 1},
                    {"category": "B", "price": 20, "qty": 3},
                ]
                self.filter = "A"

            @computed("items", "filter")
            def filtered_total(self):
                return sum(
                    i["price"] * i["qty"] for i in self.items if i["category"] == self.filter
                )

        view = DashView()

        # Warm up the cache
        expected = view.filtered_total
        assert expected == 25  # (10*2 + 5*1)

        barrier = threading.Barrier(50)

        def access_property():
            barrier.wait()  # synchronize to maximize contention
            try:
                for _ in range(100):
                    result = view.filtered_total
                    if result != expected:
                        errors.append(f"stale read: expected {expected}, got {result}")
            except Exception as e:
                errors.append(f"{type(e).__name__}: {e}")

        threads = [threading.Thread(target=access_property) for _ in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"#1289: concurrent @computed access produced errors: {errors}"

    def test_cache_remains_consistent_after_concurrent_access(self):
        """Cache should remain consistent and match the recomputed value
        after concurrent access."""
        side_effect_count = 0

        class DashView(LiveView):
            def update(self):
                pass

            @computed("counter")
            def expensive(self):
                nonlocal side_effect_count
                side_effect_count += 1
                return self.counter * 2

        view = DashView()
        view.counter = 5

        # Warm up
        result = view.expensive
        assert result == 10

        barrier = threading.Barrier(50)

        def access():
            barrier.wait()
            for _ in range(50):
                view.expensive  # noqa: B018

        threads = [threading.Thread(target=access) for _ in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Cache should still be correct and no re-computation
        # since counter didn't change.
        assert view.expensive == 10
        # side_effect_count might be >1 if threads recomputed before
        # the lock was acquired, but should be bounded by thread count
        # not thread_count * iterations (which would indicate no
        # caching at all).
        assert side_effect_count <= 51, (
            f"#1289: excessive recomputations ({side_effect_count}), "
            f"expected <= 51 (1 warmup + 50 thread first-access races)"
        )

    def test_lock_exists_on_instance(self):
        """The per-instance lock is created lazily on __dict__."""

        class DashView(LiveView):
            def update(self):
                pass

            @computed("a")
            def my_prop(self):
                return self.a * 2

        view = DashView()
        assert "_djust_computed_lock" not in view.__dict__

        view.a = 3
        _ = view.my_prop

        assert "_djust_computed_lock" in view.__dict__
        assert isinstance(view.__dict__["_djust_computed_lock"], type(threading.Lock()))
