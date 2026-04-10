"""
Regression tests for PerformanceTracker.track_context_size.

Before the fix for #649, track_context_size called sys.getsizeof(str(context)),
which triggered __repr__ on every value in the context dict. For unevaluated
Django QuerySets, __repr__ calls list(self[:21]) and hits the database. In the
async WebSocket request path this raises SynchronousOnlyOperation.

The fix replaces the str() call with a shallow per-value getsizeof sum, which
does not invoke __repr__ on values and therefore never evaluates lazy objects.
"""

from djust.performance import PerformanceTracker


class ReprExplodes:
    """A value whose __repr__ raises — stand-in for an unevaluated QuerySet.

    Mirrors the behaviour of QuerySet.__repr__() which evaluates the query
    (list(self[:21])) and would raise SynchronousOnlyOperation if called in
    an async context.  Using a plain exception here keeps the test free of
    Django database setup while exercising the same code path.
    """

    def __repr__(self):
        raise RuntimeError("__repr__ must not be called by track_context_size")

    def __str__(self):
        # __str__ falls back to __repr__ in Python by default, so override
        # explicitly to ensure str(self) also raises — this is what the old
        # sys.getsizeof(str(context)) path hit.
        raise RuntimeError("__str__ must not be called by track_context_size")


class TestTrackContextSizeDoesNotCallRepr:
    """track_context_size must not invoke __repr__ or __str__ on values.

    Regression test for https://github.com/djust-org/djust/issues/649
    """

    def test_value_with_exploding_repr_does_not_raise(self):
        """Passing a value whose __repr__ raises must not raise from tracker."""
        tracker = PerformanceTracker()
        # Should not raise — the fix avoids calling repr/str on values.
        tracker.track_context_size({"qs": ReprExplodes()})
        # Size should be > 0 (the tracker captured something, even if rough)
        assert tracker.context_size > 0

    def test_mixed_context_with_exploding_repr(self):
        """Mixing normal values with a repr-raising value must still work."""
        tracker = PerformanceTracker()
        tracker.track_context_size(
            {
                "user_id": 42,
                "title": "Claim Detail",
                "qs": ReprExplodes(),
                "items": [1, 2, 3],
            }
        )
        assert tracker.context_size > 0

    def test_value_with_exploding_str_does_not_raise(self):
        """Explicit __str__ that raises (like the old code path hit)."""

        class StrExplodes:
            def __str__(self):
                raise RuntimeError("str() must not be called")

            def __repr__(self):
                raise RuntimeError("repr() must not be called")

        tracker = PerformanceTracker()
        tracker.track_context_size({"obj": StrExplodes()})
        assert tracker.context_size > 0

    def test_empty_context(self):
        """Empty context dict should produce a valid (non-negative) size."""
        tracker = PerformanceTracker()
        tracker.track_context_size({})
        assert tracker.context_size >= 0

    def test_context_size_resilient_to_any_exception(self):
        """Broken value should not break the tracker (graceful degradation)."""

        class SizeofExplodes:
            def __sizeof__(self):
                raise RuntimeError("sizeof blew up")

        tracker = PerformanceTracker()
        # Even if getsizeof fails on a value, tracker falls back to 0.
        tracker.track_context_size({"bad": SizeofExplodes()})
        # The tracker should degrade gracefully — either skip the bad value
        # or set size to 0.  Must not raise.
        assert tracker.context_size >= 0

    def test_normal_context_still_tracks_size(self):
        """Normal values still produce a sensible size estimate."""
        tracker = PerformanceTracker()
        tracker.track_context_size(
            {
                "user_id": 42,
                "title": "Claim Detail",
                "items": list(range(100)),
            }
        )
        # Should be at least the size of the dict itself plus the list value
        assert tracker.context_size > 100
