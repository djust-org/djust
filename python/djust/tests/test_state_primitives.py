"""Tests for v0.5.1 state primitives: @computed (memoized), dirty tracking,
unique_id, and provide_context / consume_context.
"""

from __future__ import annotations

import pytest

from djust.decorators import computed
from djust.live_view import LiveView


# ─────────────────────────────────────────────────────────────────────────────
# @computed — plain form (existing behavior, regression guard)
# ─────────────────────────────────────────────────────────────────────────────


def test_computed_plain_form_recomputes_each_access():
    """``@computed`` with no args stays a plain property — recomputes every read."""
    calls = {"n": 0}

    class V(LiveView):
        count = 0

        @computed
        def doubled(self):
            calls["n"] += 1
            return self.count * 2

    v = V()
    assert v.doubled == 0
    assert v.doubled == 0
    assert calls["n"] == 2  # Two accesses, two calls (no memo).
    # Mark as computed for template consumers.
    assert getattr(V.doubled, "_is_computed", False) is True


# ─────────────────────────────────────────────────────────────────────────────
# @computed — memoized form with explicit deps
# ─────────────────────────────────────────────────────────────────────────────


def test_computed_memoized_caches_until_dep_changes():
    calls = {"n": 0}

    class V(LiveView):
        items = []
        tax_rate = 0.0

        @computed("items", "tax_rate")
        def total_price(self):
            calls["n"] += 1
            subtotal = sum(i["price"] * i["qty"] for i in self.items)
            return subtotal * (1 + self.tax_rate)

    v = V()
    v.items = [{"price": 10, "qty": 2}]
    v.tax_rate = 0.1

    assert v.total_price == 22.0
    assert v.total_price == 22.0
    assert calls["n"] == 1  # Cached on second access.


def test_computed_memoized_recomputes_on_reassignment():
    calls = {"n": 0}

    class V(LiveView):
        items = [{"price": 5, "qty": 1}]

        @computed("items")
        def total(self):
            calls["n"] += 1
            return sum(i["price"] * i["qty"] for i in self.items)

    v = V()
    assert v.total == 5
    assert calls["n"] == 1

    # Reassign — new id(), cache invalidated.
    v.items = [{"price": 5, "qty": 3}]
    assert v.total == 15
    assert calls["n"] == 2


def test_computed_memoized_detects_in_place_append_via_length_change():
    """In-place ``list.append`` changes length → fingerprint differs → cache busts."""
    calls = {"n": 0}

    class V(LiveView):
        items = []

        @computed("items")
        def count_items(self):
            calls["n"] += 1
            return len(self.items)

    v = V()
    v.items = [1, 2]
    assert v.count_items == 2
    v.items.append(3)
    assert v.count_items == 3
    assert calls["n"] == 2


def test_computed_memoized_rejects_non_string_deps():
    with pytest.raises(TypeError):

        class V(LiveView):
            @computed(42, "ok")
            def bad(self):
                return None


def test_computed_memoized_handles_missing_attr():
    """Referencing an undefined attr in the dep list must not crash."""

    class V(LiveView):
        @computed("nope_never_set")
        def go(self):
            return "ok"

    v = V()
    assert v.go == "ok"
    assert v.go == "ok"  # still works on second call


# ─────────────────────────────────────────────────────────────────────────────
# Dirty tracking
# ─────────────────────────────────────────────────────────────────────────────


def test_is_dirty_false_before_baseline_captured():
    class V(LiveView):
        count = 0

    v = V()
    v.count = 5
    assert v.is_dirty is False
    assert v.changed_fields == set()


def test_is_dirty_detects_public_attr_change():
    class V(LiveView):
        count = 0
        name = ""

    v = V()
    v._capture_dirty_baseline()
    assert v.is_dirty is False
    v.count = 5
    assert v.is_dirty is True
    assert v.changed_fields == {"count"}


def test_is_dirty_ignores_private_attrs():
    class V(LiveView):
        count = 0

    v = V()
    v._capture_dirty_baseline()
    v._secret = "hello"  # underscore = private → not tracked
    assert v.is_dirty is False


def test_mark_clean_resets_baseline():
    class V(LiveView):
        count = 0

    v = V()
    v._capture_dirty_baseline()
    v.count = 5
    assert v.is_dirty is True
    v.mark_clean()
    assert v.is_dirty is False


def test_dirty_tracks_multiple_attrs():
    class V(LiveView):
        a = 0
        b = 0
        c = 0

    v = V()
    v._capture_dirty_baseline()
    v.a = 1
    v.c = 3
    assert v.changed_fields == {"a", "c"}


def test_dirty_respects_static_assigns():
    class V(LiveView):
        config = "immutable"
        static_assigns = ["config"]

    v = V()
    v._capture_dirty_baseline()
    v.config = "changed"
    # Excluded from tracking.
    assert v.is_dirty is False


# ─────────────────────────────────────────────────────────────────────────────
# unique_id (useId equivalent)
# ─────────────────────────────────────────────────────────────────────────────


def test_unique_id_sequence_is_stable_across_resets():
    class V(LiveView):
        pass

    v = V()
    first_pass = [v.unique_id() for _ in range(3)]
    v.reset_unique_ids()
    second_pass = [v.unique_id() for _ in range(3)]
    assert first_pass == second_pass


def test_unique_id_monotonic_within_a_render():
    class V(LiveView):
        pass

    v = V()
    a, b, c = v.unique_id(), v.unique_id(), v.unique_id()
    assert a != b != c
    assert a == "djust-v-0"
    assert b == "djust-v-1"
    assert c == "djust-v-2"


def test_unique_id_with_suffix():
    class V(LiveView):
        pass

    v = V()
    assert v.unique_id("label") == "djust-v-0-label"


def test_unique_id_uses_class_name_slug():
    class MyFancyView(LiveView):
        pass

    v = MyFancyView()
    assert v.unique_id().startswith("djust-myfancyview-")


# ─────────────────────────────────────────────────────────────────────────────
# provide_context / consume_context
# ─────────────────────────────────────────────────────────────────────────────


def test_provide_and_consume_context_same_view():
    class V(LiveView):
        pass

    v = V()
    v.provide_context("theme", "dark")
    assert v.consume_context("theme") == "dark"


def test_consume_context_falls_back_to_default():
    class V(LiveView):
        pass

    v = V()
    assert v.consume_context("missing") is None
    assert v.consume_context("missing", default="fallback") == "fallback"


def test_consume_context_walks_parent_chain():
    class Parent(LiveView):
        pass

    class Child(LiveView):
        pass

    parent = Parent()
    parent.provide_context("theme", "dark")
    child = Child()
    child._djust_context_parent = parent
    assert child.consume_context("theme") == "dark"


def test_child_provider_shadows_parent():
    class V(LiveView):
        pass

    parent, child = V(), V()
    parent.provide_context("theme", "dark")
    child._djust_context_parent = parent
    child.provide_context("theme", "light")
    assert child.consume_context("theme") == "light"


def test_clear_context_providers():
    class V(LiveView):
        pass

    v = V()
    v.provide_context("theme", "dark")
    v.provide_context("locale", "en")
    v.clear_context_providers()
    assert v.consume_context("theme") is None
    assert v.consume_context("locale") is None


def test_consume_returns_default_when_parent_has_no_providers():
    """A parent set but without any provider dict still degrades gracefully."""

    class V(LiveView):
        pass

    child = V()
    child._djust_context_parent = V()  # no provide_context called on parent
    assert child.consume_context("theme", default="x") == "x"
