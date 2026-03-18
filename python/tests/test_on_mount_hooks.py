"""
Tests for on_mount hooks — @on_mount decorator, collection, and execution.
"""

from unittest.mock import MagicMock

from djust.hooks import (
    on_mount,
    is_on_mount,
    _collect_on_mount_hooks,
    run_on_mount_hooks,
)


# ---------------------------------------------------------------------------
# Decorator tests
# ---------------------------------------------------------------------------


class TestOnMountDecorator:
    def test_marks_function(self):
        @on_mount
        def my_hook(view, request, **kwargs):
            pass

        assert my_hook._djust_on_mount is True

    def test_returns_original_function(self):
        def my_hook(view, request, **kwargs):
            pass

        result = on_mount(my_hook)
        assert result is my_hook


class TestIsOnMount:
    def test_true_for_decorated(self):
        @on_mount
        def hook(view, request, **kwargs):
            pass

        assert is_on_mount(hook) is True

    def test_false_for_plain_function(self):
        def plain(view, request, **kwargs):
            pass

        assert is_on_mount(plain) is False

    def test_false_for_non_function(self):
        assert is_on_mount("not a function") is False
        assert is_on_mount(42) is False


# ---------------------------------------------------------------------------
# Collection tests
# ---------------------------------------------------------------------------


@on_mount
def hook_a(view, request, **kwargs):
    pass


@on_mount
def hook_b(view, request, **kwargs):
    pass


@on_mount
def hook_c(view, request, **kwargs):
    pass


class _Base:
    on_mount = [hook_a]


class _Child(_Base):
    on_mount = [hook_b]


class _GrandChild(_Child):
    on_mount = [hook_c]


class _ChildWithDup(_Base):
    """Declares hook_a again — should be deduplicated."""

    on_mount = [hook_a, hook_b]


class _Empty:
    pass


class TestCollectHooks:
    def test_empty_class(self):
        assert _collect_on_mount_hooks(_Empty) == []

    def test_single_hook(self):
        hooks = _collect_on_mount_hooks(_Base)
        assert hooks == [hook_a]

    def test_parent_then_child(self):
        hooks = _collect_on_mount_hooks(_Child)
        assert hooks == [hook_a, hook_b]

    def test_grandchild_order(self):
        hooks = _collect_on_mount_hooks(_GrandChild)
        assert hooks == [hook_a, hook_b, hook_c]

    def test_deduplication(self):
        hooks = _collect_on_mount_hooks(_ChildWithDup)
        # hook_a appears in both _Base and _ChildWithDup but should appear once
        assert hooks.count(hook_a) == 1
        assert hooks == [hook_a, hook_b]


# ---------------------------------------------------------------------------
# Execution tests
# ---------------------------------------------------------------------------


class _StubView:
    on_mount = []


class TestRunHooks:
    def test_all_pass_returns_none(self):
        @on_mount
        def pass_through(view, request, **kwargs):
            return None

        class MyView(_StubView):
            on_mount = [pass_through]

        view = MyView()
        result = run_on_mount_hooks(view, MagicMock())
        assert result is None

    def test_halt_with_redirect(self):
        @on_mount
        def redirect_hook(view, request, **kwargs):
            return "/login/"

        class MyView(_StubView):
            on_mount = [redirect_hook]

        view = MyView()
        result = run_on_mount_hooks(view, MagicMock())
        assert result == "/login/"

    def test_halt_stops_subsequent_hooks(self):
        call_order = []

        @on_mount
        def first(view, request, **kwargs):
            call_order.append("first")
            return "/blocked/"

        @on_mount
        def second(view, request, **kwargs):
            call_order.append("second")
            return None

        class MyView(_StubView):
            on_mount = [first, second]

        view = MyView()
        result = run_on_mount_hooks(view, MagicMock())
        assert result == "/blocked/"
        assert call_order == ["first"]

    def test_receives_view_and_request(self):
        captured = {}

        @on_mount
        def capture(view, request, **kwargs):
            captured["view"] = view
            captured["request"] = request
            return None

        class MyView(_StubView):
            on_mount = [capture]

        view = MyView()
        req = MagicMock()
        run_on_mount_hooks(view, req)
        assert captured["view"] is view
        assert captured["request"] is req

    def test_hook_receives_kwargs(self):
        captured_kwargs = {}

        @on_mount
        def capture_kw(view, request, **kwargs):
            captured_kwargs.update(kwargs)
            return None

        class MyView(_StubView):
            on_mount = [capture_kw]

        view = MyView()
        run_on_mount_hooks(view, MagicMock(), slug="test", pk=42)
        assert captured_kwargs == {"slug": "test", "pk": 42}

    def test_empty_hooks_returns_none(self):
        class MyView(_StubView):
            on_mount = []

        view = MyView()
        assert run_on_mount_hooks(view, MagicMock()) is None

    def test_no_on_mount_attr_returns_none(self):
        class MyView:
            pass

        view = MyView()
        assert run_on_mount_hooks(view, MagicMock()) is None
