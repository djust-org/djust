"""Tests for automatic change tracking — Phoenix-style render optimization."""

import pytest
from functools import lru_cache
from unittest.mock import Mock
from django.test import RequestFactory

from djust import LiveView
from djust.mixins.rust_bridge import _collect_sub_ids
from djust.websocket import _snapshot_assigns, _compute_changed_keys


# ---------------------------------------------------------------------------
# Test views
# ---------------------------------------------------------------------------


class CounterView(LiveView):
    """Simple view to test changed/unchanged attribute tracking."""

    template = "<div dj-root>{{ count }}</div>"

    def mount(self, request, **kwargs):
        self.count = 0


class CachedDemoView(LiveView):
    """View using @lru_cache for expensive computed data."""

    template = "<div dj-root>{{ demos }} {{ count }}</div>"

    def mount(self, request, **kwargs):
        self.count = 0

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["demos"] = _get_cached_demos()
        return context


@lru_cache(maxsize=1)
def _get_cached_demos():
    """Simulates an expensive computation cached at module level."""
    return [{"id": "counter", "html": "<h1>Counter Demo</h1>"}]


class MultiAttrView(LiveView):
    """View with multiple attributes for selective change testing."""

    template = "<div dj-root>{{ a }} {{ b }} {{ c }}</div>"

    def mount(self, request, **kwargs):
        self.a = "alpha"
        self.b = [1, 2, 3]
        self.c = {"key": "value"}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_request():
    factory = RequestFactory()
    request = factory.get("/")
    request.session = Mock()
    request.session.session_key = "test-session"
    return request


# ---------------------------------------------------------------------------
# _compute_changed_keys
# ---------------------------------------------------------------------------


class TestComputeChangedKeys:
    """Test _compute_changed_keys correctness."""

    def test_no_changes(self):
        """Identical snapshots produce empty set."""
        pre = {"a": 1, "b": "hello"}
        post = {"a": 1, "b": "hello"}
        assert _compute_changed_keys(pre, post) == set()

    def test_modified_key(self):
        """Changed value is detected."""
        pre = {"count": 0, "name": "test"}
        post = {"count": 1, "name": "test"}
        assert _compute_changed_keys(pre, post) == {"count"}

    def test_added_key(self):
        """New key in post is detected."""
        pre = {"a": 1}
        post = {"a": 1, "b": 2}
        assert _compute_changed_keys(pre, post) == {"b"}

    def test_removed_key(self):
        """Key missing from post is detected."""
        pre = {"a": 1, "b": 2}
        post = {"a": 1}
        assert _compute_changed_keys(pre, post) == {"b"}

    def test_multiple_changes(self):
        """Multiple adds, removes, and modifications."""
        pre = {"a": 1, "b": 2, "c": 3}
        post = {"a": 1, "b": 99, "d": 4}
        result = _compute_changed_keys(pre, post)
        assert result == {"b", "c", "d"}

    def test_empty_snapshots(self):
        """Both empty produces no changes."""
        assert _compute_changed_keys({}, {}) == set()

    def test_list_mutation(self):
        """In-place list mutation detected via deep copy comparison."""
        pre = {"items": [1, 2]}
        post = {"items": [1, 2, 3]}
        assert _compute_changed_keys(pre, post) == {"items"}


# ---------------------------------------------------------------------------
# _snapshot_assigns: immutable type optimization
# ---------------------------------------------------------------------------


class TestSnapshotAssigns:
    """Test _snapshot_assigns immutable optimization and correctness."""

    def test_includes_all_public_attrs(self):
        """All non-underscore instance attributes are included."""
        view = CounterView()
        view.count = 42
        snapshot = _snapshot_assigns(view)
        assert "count" in snapshot
        assert snapshot["count"] == 42

    def test_user_private_attrs_included(self):
        """User-defined ``_``-prefixed attrs are included (#1281).
        Framework ``_``-prefixed attrs set at __init__ are excluded."""
        view = CounterView()
        view.count = 1
        view._private = "secret"
        snapshot = _snapshot_assigns(view)
        assert "_private" in snapshot, "#1281: user private attrs must be in snapshot"
        assert "_changed_keys" not in snapshot
        assert "count" in snapshot

    def test_immutable_types_not_deepcopied(self):
        """Immutable types should be stored directly (same id)."""
        view = LiveView()
        view.name = "hello"
        view.count = 42
        view.ratio = 3.14
        view.active = True
        view.data = None
        view.raw = b"bytes"
        view.coords = (1, 2, 3)
        view.tags = frozenset(["a", "b"])

        snapshot = _snapshot_assigns(view)

        # For immutables, the snapshot value should be the exact same object
        assert snapshot["name"] is view.name
        assert snapshot["count"] is view.count
        assert snapshot["ratio"] is view.ratio
        assert snapshot["active"] is view.active
        assert snapshot["data"] is view.data
        assert snapshot["raw"] is view.raw
        assert snapshot["coords"] is view.coords
        assert snapshot["tags"] is view.tags

    def test_mutable_types_fingerprinted(self):
        """Mutable types use identity+fingerprint (id, length, etc.)."""
        view = LiveView()
        view.items = [1, 2, 3]
        view.config = {"key": "value"}

        snapshot = _snapshot_assigns(view)

        # Lists: (id, length, content_fingerprint) for non-empty lists
        assert snapshot["items"][0] == id(view.items)
        assert snapshot["items"][1] == 3
        # Dicts: (id, length, keys_tuple)
        assert snapshot["config"][0] == id(view.config)
        assert snapshot["config"][1] == 1  # length

        # Same values produce same snapshot
        snapshot2 = _snapshot_assigns(view)
        assert snapshot == snapshot2

        # Mutation detected: append changes length
        view.items.append(4)
        snapshot3 = _snapshot_assigns(view)
        assert snapshot3["items"] != snapshot["items"]

    def test_non_copyable_uses_identity(self):
        """Non-copyable objects use id() — reassignment detected, mutation not."""

        class Custom:
            pass

        view = LiveView()
        view.weird = Custom()

        snapshot = _snapshot_assigns(view)
        assert "weird" in snapshot
        # Same object → same snapshot
        snapshot2 = _snapshot_assigns(view)
        assert snapshot["weird"] == snapshot2["weird"]
        # New object → different snapshot
        view.weird = Custom()
        snapshot3 = _snapshot_assigns(view)
        assert snapshot3["weird"] != snapshot["weird"]

    def test_no_static_assigns_skip(self):
        """After removing static_assigns, all public attrs are always included."""
        view = LiveView()
        view.demos = "<h1>Big HTML</h1>"
        view.count = 42

        snapshot = _snapshot_assigns(view)
        assert "demos" in snapshot
        assert "count" in snapshot


# ---------------------------------------------------------------------------
# RustBridgeMixin: change-aware _sync_state_to_rust
# ---------------------------------------------------------------------------


class TestSyncStateChangeTracking:
    """Test that _sync_state_to_rust only sends changed values."""

    def test_first_render_sends_everything(self, mock_request):
        """First render (no prev_refs) sends all context to Rust."""
        view = CounterView()
        view.mount(mock_request)

        mock_rust = Mock()
        view._rust_view = mock_rust

        view._sync_state_to_rust()

        call_args = mock_rust.update_state.call_args[0][0]
        assert "count" in call_args

    def test_unchanged_attrs_skipped(self, mock_request):
        """Unchanged attributes are not sent on subsequent syncs."""
        view = MultiAttrView()
        view.mount(mock_request)

        mock_rust = Mock()
        view._rust_view = mock_rust

        # First sync — sends everything
        view._sync_state_to_rust()
        first_call = mock_rust.update_state.call_args[0][0]
        assert "a" in first_call
        assert "b" in first_call
        assert "c" in first_call

        # Mark only 'a' as changed (simulate handle_event result)
        view._changed_keys = {"a"}
        # Change 'a' so id() differs
        view.a = "beta"

        view._sync_state_to_rust()
        second_call = mock_rust.update_state.call_args[0][0]
        assert "a" in second_call
        # b and c should be skipped (same id, not in _changed_keys)
        assert "b" not in second_call
        assert "c" not in second_call

    def test_lru_cache_value_skipped_on_rerender(self, mock_request):
        """@lru_cache returns same object — id() unchanged → skipped."""
        view = CachedDemoView()
        view.mount(mock_request)

        mock_rust = Mock()
        view._rust_view = mock_rust

        # First sync — sends everything including demos
        view._sync_state_to_rust()
        first_call = mock_rust.update_state.call_args[0][0]
        assert "demos" in first_call

        # Second sync — demos from lru_cache has same id(), should be skipped
        # Only count is still in _changed_keys or new
        view._changed_keys = {"count"}
        view.count = 1

        view._sync_state_to_rust()
        second_call = mock_rust.update_state.call_args[0][0]
        assert "count" in second_call
        assert "demos" not in second_call

    def test_new_key_always_sent(self, mock_request):
        """Keys not in prev_refs are always sent."""
        view = CounterView()
        view.mount(mock_request)

        mock_rust = Mock()
        view._rust_view = mock_rust

        # First sync
        view._sync_state_to_rust()

        # Add a new attribute after first render
        view.new_attr = "hello"
        view._changed_keys = {"new_attr"}

        view._sync_state_to_rust()
        second_call = mock_rust.update_state.call_args[0][0]
        assert "new_attr" in second_call

    def test_changed_keys_cleared_after_sync(self, mock_request):
        """_changed_keys should be None after _sync_state_to_rust."""
        view = CounterView()
        view.mount(mock_request)

        mock_rust = Mock()
        view._rust_view = mock_rust

        view._changed_keys = {"count"}
        view._sync_state_to_rust()
        assert view._changed_keys is None

    def test_server_push_sends_changed_refs(self, mock_request):
        """Server push path (no _changed_keys) uses id() comparison.

        Note: dict values pass through _deep_serialize_dict() which creates a
        new dict, but Python may reuse the same memory address after GC — so
        id() comparison for dicts is non-deterministic. We only assert on
        reliably deterministic cases.
        """
        view = MultiAttrView()
        view.mount(mock_request)

        mock_rust = Mock()
        view._rust_view = mock_rust

        # First sync — sends everything
        view._sync_state_to_rust()

        # Simulate server push: no _changed_keys set, but b changed reference
        view.b = [4, 5, 6]  # new list object

        view._sync_state_to_rust()
        second_call = mock_rust.update_state.call_args[0][0]
        assert "b" in second_call
        # 'a' is a str (immutable) — same object, same id() → skipped
        assert "a" not in second_call
        # Note: 'c' (dict) is not asserted — _deep_serialize_dict() creates a
        # new dict each time, but Python's allocator may reuse the same address
        # after GC frees the previous one, making id() comparison flaky.

    def test_prev_context_refs_stored(self, mock_request):
        """_prev_context_refs is populated after sync."""
        view = CounterView()
        view.mount(mock_request)

        mock_rust = Mock()
        view._rust_view = mock_rust

        assert not hasattr(view, "_prev_context_refs") or not view._prev_context_refs

        view._sync_state_to_rust()
        assert hasattr(view, "_prev_context_refs")
        assert "count" in view._prev_context_refs


# ---------------------------------------------------------------------------
# ContextMixin: always includes all keys (no static_assigns filtering)
# ---------------------------------------------------------------------------


class TestContextAlwaysComplete:
    """After removing static_assigns, context always includes all keys."""

    def test_all_attrs_in_context(self, mock_request):
        """get_context_data includes all public attributes."""
        view = MultiAttrView()
        view.mount(mock_request)
        context = view.get_context_data()
        assert "a" in context
        assert "b" in context
        assert "c" in context

    def test_repeated_calls_always_include_all(self, mock_request):
        """Multiple get_context_data calls always return all keys."""
        view = MultiAttrView()
        view.mount(mock_request)

        ctx1 = view.get_context_data()
        ctx2 = view.get_context_data()

        for key in ("a", "b", "c"):
            assert key in ctx1
            assert key in ctx2


# ---------------------------------------------------------------------------
# _collect_sub_ids helper
# ---------------------------------------------------------------------------


class TestCollectSubIds:
    """Unit tests for the _collect_sub_ids helper."""

    def test_flat_dict(self):
        """Collects id of dict and its values."""
        inner = {"a": 1}
        d = {"key": inner}
        collected = set()
        _collect_sub_ids(d, collected)
        assert id(d) in collected
        assert id(inner) in collected

    def test_nested_list(self):
        """Collects ids through lists."""
        inner = [1, 2]
        outer = {"items": inner}
        collected = set()
        _collect_sub_ids(outer, collected)
        assert id(inner) in collected

    def test_circular_reference_does_not_hang(self):
        """Self-referencing dict terminates without error."""
        d = {}
        d["self"] = d
        collected = set()
        _collect_sub_ids(d, collected)  # should not hang
        assert id(d) in collected

    def test_depth_limit_respected(self):
        """Nesting deeper than 8 levels is not traversed."""
        # Build a chain of 12 nested dicts
        leaf = {"leaf": True}
        current = leaf
        for _ in range(12):
            current = {"child": current}

        collected = set()
        _collect_sub_ids(current, collected)
        # The leaf at depth 13 should NOT be collected (depth cap is 8)
        assert id(leaf) not in collected

    def test_empty_value(self):
        """Non-container scalar doesn't recurse but is collected."""
        collected = set()
        _collect_sub_ids(42, collected)
        assert id(42) in collected


# ---------------------------------------------------------------------------
# Derived context sub-object sync (#703)
# ---------------------------------------------------------------------------


class DerivedContextView(LiveView):
    """View that exposes a sub-object of a mutated dict as a derived context var."""

    template = "<div dj-root>{{ person_data.first_name }}</div>"

    def mount(self, request, **kwargs):
        self.wizard_step_data = {}

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Returns the same inner dict object — same id() — on every call
        context["person_data"] = self.wizard_step_data.get("person", {})
        return context


class DerivedListView(LiveView):
    """View that exposes a list element sub-object as a derived context var."""

    template = "<div dj-root>{{ first_item.name }}</div>"

    def mount(self, request, **kwargs):
        self.items = [{"name": "original"}]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["first_item"] = self.items[0] if self.items else {}
        return context


class TestDerivedContextSubObjectSync:
    """Test that derived context vars sharing sub-objects with changed attrs are synced."""

    def test_derived_dict_synced_when_parent_mutated_in_place(self, mock_request):
        """Core bug: person_data shares an inner dict with wizard_step_data."""
        view = DerivedContextView()
        view.mount(mock_request)

        mock_rust = Mock()
        view._rust_view = mock_rust

        # First render — sends everything
        view._sync_state_to_rust()

        # Simulate in-place mutation (like a form validation handler)
        view.wizard_step_data["person"] = {"first_name": "Alice"}
        view._changed_keys = {"wizard_step_data"}

        view._sync_state_to_rust()
        second_call = mock_rust.update_state.call_args[0][0]

        # Both the parent AND the derived var must be synced
        assert "wizard_step_data" in second_call
        assert "person_data" in second_call

    def test_derived_list_element_synced_when_parent_mutated(self, mock_request):
        """List element sub-object synced when parent list item is mutated."""
        view = DerivedListView()
        view.mount(mock_request)

        mock_rust = Mock()
        view._rust_view = mock_rust

        # First render
        view._sync_state_to_rust()

        # Mutate the inner dict in-place
        view.items[0]["name"] = "updated"
        view._changed_keys = {"items"}

        view._sync_state_to_rust()
        second_call = mock_rust.update_state.call_args[0][0]

        assert "items" in second_call
        assert "first_item" in second_call

    def test_no_overhead_when_changed_keys_empty(self, mock_request):
        """When _changed_keys is None/empty, changed_sub_ids stays empty — no extra work.

        We use CounterView (immutable int state) to avoid the dict id() instability
        that mutable containers introduce through normalize_django_value.
        """
        view = CounterView()
        view.mount(mock_request)

        mock_rust = Mock()
        view._rust_view = mock_rust

        # First render
        view._sync_state_to_rust()

        # No changes — simulate a server push with no _changed_keys
        view._changed_keys = None
        view._sync_state_to_rust()
        second_call = mock_rust.update_state.call_args[0][0]

        # count is an int (immutable, same id) — should not be sent
        assert "count" not in second_call

    def test_derived_synced_when_parent_not_in_context(self, mock_request):
        """Bug from #703 re-open: changed_keys contains instance attr names
        that may not appear in the context dict. The fix must look up the
        changed value from self.__dict__, not from full_context."""

        class PrivateParentView(LiveView):
            template = "<div dj-root>{{ claimant.first_name }}</div>"

            def mount(self, request, **kwargs):
                # _step_data is private — won't appear in context
                self._step_data = {"claimant": {"first_name": "Alice"}}

            def get_context_data(self, **kwargs):
                context = super().get_context_data(**kwargs)
                # Derived var from private parent
                context["claimant"] = self._step_data.get("claimant", {})
                return context

        view = PrivateParentView()
        view.mount(mock_request)

        mock_rust = Mock()
        view._rust_view = mock_rust

        view._sync_state_to_rust()

        # Mutate nested dict via private parent
        view._step_data["claimant"]["first_name"] = "Bob"
        view._changed_keys = {"_step_data"}

        view._sync_state_to_rust()
        second_call = mock_rust.update_state.call_args[0][0]

        # claimant must be synced even though _step_data isn't in context
        assert "claimant" in second_call

    def test_unchanged_parent_does_not_cascade(self, mock_request):
        """When parent is NOT in _changed_keys, derived var is not force-synced."""
        view = DerivedContextView()
        view.mount(mock_request)

        mock_rust = Mock()
        view._rust_view = mock_rust

        # First render
        view.wizard_step_data["person"] = {"first_name": "Bob"}
        view._sync_state_to_rust()

        # Mark some other key as changed, not wizard_step_data
        view._changed_keys = {"some_other_key"}
        view.some_other_key = "new"

        view._sync_state_to_rust()
        second_call = mock_rust.update_state.call_args[0][0]

        # person_data should NOT be in second_call since its parent didn't change
        assert "person_data" not in second_call


class TestDerivedImmutableSync:
    """Derived IMMUTABLE context values (int/str counters like
    `completed_count = sum(...)`) must be synced when their computed value
    changes — even though they aren't in `_changed_keys` (because they're
    not instance attrs) and aren't detected by `id()` comparison (because
    Python interns small ints and strings).

    Regression: the Todo example's `{{ completed_count }}` / `{{ total_count }}`
    stats counters stayed stale after a todo was deleted — the stats text
    template node skipped re-render because its deps (`completed_count`) did
    not intersect `_changed_keys = {'todos'}`, and the new context value was
    never pushed to Rust because id() comparison was skipped for ints.
    """

    def test_derived_int_counter_synced_when_source_list_changes(self, mock_request):
        """int counter computed from a list length must sync when list changes."""

        class TodoStatsView(LiveView):
            template = "<div dj-root>{{ total_count }} / {{ completed_count }}</div>"

            def mount(self, request, **kwargs):
                self.todos = []

            def get_context_data(self, **kwargs):
                context = super().get_context_data(**kwargs)
                context["total_count"] = len(self.todos)
                context["completed_count"] = sum(1 for t in self.todos if t.get("completed"))
                return context

        view = TodoStatsView()
        view.mount(mock_request)

        mock_rust = Mock()
        view._rust_view = mock_rust

        # First render — sends everything
        view._sync_state_to_rust()

        # Append one COMPLETED todo; mark todos as changed. Both derived
        # counters change values (0→1).
        view.todos = [*view.todos, {"id": 1, "text": "buy milk", "completed": True}]
        view._changed_keys = {"todos"}
        view._sync_state_to_rust()
        second = mock_rust.update_state.call_args[0][0]

        # Both derived counters must be synced because both values changed.
        assert second.get("total_count") == 1
        assert second.get("completed_count") == 1

        # Now remove the todo (the case that was broken for delete).
        view.todos = []
        view._changed_keys = {"todos"}
        view._sync_state_to_rust()
        third = mock_rust.update_state.call_args[0][0]

        assert third.get("total_count") == 0
        assert third.get("completed_count") == 0

    def test_derived_string_synced_when_value_changes(self, mock_request):
        """str derived value must sync when its text changes."""

        class StatusView(LiveView):
            template = "<div dj-root>{{ label }}</div>"

            def mount(self, request, **kwargs):
                self.ready = False

            def get_context_data(self, **kwargs):
                context = super().get_context_data(**kwargs)
                context["label"] = "ready" if self.ready else "pending"
                return context

        view = StatusView()
        view.mount(mock_request)

        mock_rust = Mock()
        view._rust_view = mock_rust

        view._sync_state_to_rust()

        view.ready = True
        view._changed_keys = {"ready"}
        view._sync_state_to_rust()
        second = mock_rust.update_state.call_args[0][0]

        assert second.get("label") == "ready"

    def test_unchanged_immutable_not_resent(self, mock_request):
        """Immutable equality must be checked: identical values must NOT be
        resent to Rust (would trigger spurious node re-renders)."""

        class FixedView(LiveView):
            template = "<div dj-root>{{ n }}</div>"

            def mount(self, request, **kwargs):
                self.other = 0

            def get_context_data(self, **kwargs):
                context = super().get_context_data(**kwargs)
                context["n"] = 42  # constant
                return context

        view = FixedView()
        view.mount(mock_request)

        mock_rust = Mock()
        view._rust_view = mock_rust

        view._sync_state_to_rust()

        view.other = 1
        view._changed_keys = {"other"}
        view._sync_state_to_rust()
        second = mock_rust.update_state.call_args[0][0]

        # n didn't change — must not be in the diff sent to Rust
        assert "n" not in second
