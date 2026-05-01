"""Tests for ``@action`` Server Actions decorator (v0.8.0).

The decorator is the React-19-equivalent companion to ``dj-form-pending``
(client-side, shipped in PR #1023). ``@action`` covers the SERVER-side
state shape: every action's name becomes a context variable carrying
``{pending, error, result}``, populated by the decorator's wrapper at
handler entry/exit.

Tests cover:

1. Decorator metadata (`_djust_decorators["action"]` set, also
   `_djust_decorators["event_handler"]` since every action is also
   an event handler).
2. Sync handler success — pending/error/result transitions.
3. Sync handler exception — error captured + re-raised.
4. Multiple actions on one view — independent state.
5. Re-running an action resets state correctly.
6. Template context injection — action name → state dict.
7. Bare-form `@action` and called-form `@action(description=...)`.
8. `is_action()` detection helper.
"""

from __future__ import annotations

import tests.conftest  # noqa: F401  -- configure Django settings

import pytest

from djust.decorators import action, event_handler, is_action, is_event_handler


class _FakeView:
    """Minimal stand-in for a LiveView — only what @action touches."""

    def __init__(self):
        self._action_state: dict = {}


# ---------------------------------------------------------------------------
# 1. Decorator metadata
# ---------------------------------------------------------------------------


class TestActionMetadata:
    def test_action_sets_action_metadata_key(self):
        @action
        def create_todo(self, title: str = "", **kwargs):
            return {"id": 42}

        meta = create_todo._djust_decorators
        assert "action" in meta
        assert meta["action"]["name"] == "create_todo"

    def test_action_also_marks_event_handler(self):
        """Every @action is also an @event_handler — same dispatch path."""

        @action
        def create_todo(self, title: str = "", **kwargs):
            return None

        assert is_action(create_todo)
        assert is_event_handler(create_todo)

    def test_event_handler_is_not_action(self):
        """Negative: a plain @event_handler is not an action."""

        @event_handler()
        def search(self, value: str = "", **kwargs):
            return None

        assert is_event_handler(search)
        assert not is_action(search)

    def test_is_action_on_undecorated(self):
        def plain(self):
            pass

        assert not is_action(plain)


# ---------------------------------------------------------------------------
# 2. Sync handler success
# ---------------------------------------------------------------------------


class TestActionSuccess:
    def test_action_records_result_on_success(self):
        @action
        def create_todo(self, title: str = "", **kwargs):
            return {"created": 42}

        view = _FakeView()
        result = create_todo(view, title="x")

        assert result == {"created": 42}
        state = view._action_state["create_todo"]
        assert state["pending"] is False
        assert state["error"] is None
        assert state["result"] == {"created": 42}

    def test_action_handles_none_return(self):
        @action
        def noop(self, **kwargs):
            return None

        view = _FakeView()
        noop(view)

        state = view._action_state["noop"]
        assert state["pending"] is False
        assert state["error"] is None
        assert state["result"] is None

    def test_action_self_initializes_action_state(self):
        """If the view forgot to set _action_state in __init__ (e.g. a
        subclass that didn't call super().__init__()), the decorator
        must self-initialize rather than crash."""

        class _BareView:
            pass  # no _action_state

        @action
        def noop(self, **kwargs):
            return None

        view = _BareView()
        noop(view)

        assert hasattr(view, "_action_state")
        assert view._action_state["noop"]["pending"] is False


# ---------------------------------------------------------------------------
# 3. Sync handler exception
# ---------------------------------------------------------------------------


class TestActionException:
    def test_action_captures_exception_message(self):
        @action
        def create_todo(self, title: str = "", **kwargs):
            raise ValueError("Title is required")

        view = _FakeView()
        # Per #1276 fix: @action records and swallows; does NOT re-raise.
        # The dispatcher proceeds to re-render with the recorded error
        # visible to the template via _action_state[name]["error"].
        result = create_todo(view, title="")
        assert result is None

        state = view._action_state["create_todo"]
        assert state["pending"] is False
        assert state["error"] == "Title is required"
        assert state["result"] is None

    def test_action_falls_back_to_class_name_for_empty_message(self):
        """Some exceptions have no message. Fall back to the exception
        class name so the template never sees an empty string."""

        @action
        def boom(self, **kwargs):
            raise ValueError()

        view = _FakeView()
        # Per #1276 fix: no propagation; recorded silently.
        result = boom(view)
        assert result is None

        assert view._action_state["boom"]["error"] == "ValueError"

    def test_action_does_NOT_re_raise_after_recording(self):
        """Closes #1276. The exception must NOT propagate to the
        dispatcher — re-raising routes the dispatcher to its
        exception-frame path and bypasses the re-render that would
        surface ``{{ name.error }}`` to the template.
        """

        @action
        def boom(self, **kwargs):
            raise RuntimeError("kaboom")

        view = _FakeView()
        # Must NOT raise.
        result = boom(view)
        assert result is None
        # State recorded so the template's next re-render shows the error.
        assert view._action_state["boom"]["error"] == "kaboom"
        assert view._action_state["boom"]["result"] is None
        assert view._action_state["boom"]["pending"] is False

    def test_action_keyboard_interrupt_still_propagates(self):
        """``BaseException`` subclasses (``KeyboardInterrupt``,
        ``SystemExit``) propagate by Python convention — @action's
        ``except Exception`` deliberately doesn't catch them.
        """

        @action
        def boom(self, **kwargs):
            raise KeyboardInterrupt()

        view = _FakeView()
        with pytest.raises(KeyboardInterrupt):
            boom(view)


# ---------------------------------------------------------------------------
# 4. Multiple actions, independent state
# ---------------------------------------------------------------------------


class TestMultipleActions:
    def test_two_actions_independent_state(self):
        @action
        def create_todo(self, **kwargs):
            return {"id": 1}

        @action
        def delete_todo(self, **kwargs):
            return {"deleted": True}

        view = _FakeView()
        create_todo(view)
        delete_todo(view)

        assert view._action_state["create_todo"]["result"] == {"id": 1}
        assert view._action_state["delete_todo"]["result"] == {"deleted": True}

    def test_one_action_failing_does_not_affect_other(self):
        @action
        def good(self, **kwargs):
            return "ok"

        @action
        def bad(self, **kwargs):
            raise ValueError("nope")

        view = _FakeView()
        good(view)
        # Per #1276 fix: bad() records the error and returns None; no raise.
        bad(view)

        # Good action stays green, bad records error.
        assert view._action_state["good"]["error"] is None
        assert view._action_state["good"]["result"] == "ok"
        assert view._action_state["bad"]["error"] == "nope"
        assert view._action_state["bad"]["result"] is None


# ---------------------------------------------------------------------------
# 5. Re-running an action resets state
# ---------------------------------------------------------------------------


class TestReRunSemantics:
    def test_re_running_clears_previous_error(self):
        """If an action failed and is then re-run successfully, the
        error field must be cleared to None — otherwise a successful
        retry would still show the old error in the template."""

        @action
        def maybe(self, ok: bool = True, **kwargs):
            if not ok:
                raise ValueError("first call failed")
            return "second call ok"

        view = _FakeView()
        # Per #1276 fix: failed call records and returns None; no raise.
        maybe(view, ok=False)
        assert view._action_state["maybe"]["error"] == "first call failed"

        # Retry — error should clear, result should populate.
        result = maybe(view, ok=True)
        assert result == "second call ok"
        assert view._action_state["maybe"]["error"] is None
        assert view._action_state["maybe"]["result"] == "second call ok"

    def test_re_running_clears_previous_result(self):
        """Inverse: if a previous successful run set a result, a
        subsequent failure must clear it. Templates should not see a
        stale result alongside a fresh error."""

        @action
        def maybe(self, ok: bool = True, **kwargs):
            if ok:
                return "first result"
            raise ValueError("second call failed")

        view = _FakeView()
        maybe(view, ok=True)
        assert view._action_state["maybe"]["result"] == "first result"

        # Per #1276 fix: failed call records and returns None; no raise.
        maybe(view, ok=False)
        assert view._action_state["maybe"]["result"] is None
        assert view._action_state["maybe"]["error"] == "second call failed"


# ---------------------------------------------------------------------------
# 6. Bare-form vs called-form
# ---------------------------------------------------------------------------


class TestDecoratorForms:
    def test_bare_form_works(self):
        @action
        def f(self):
            return 1

        assert is_action(f)

    def test_called_form_with_description(self):
        @action(description="Make a todo")
        def make(self):
            return 1

        assert is_action(make)
        # The underlying @event_handler stores the description.
        assert make._djust_decorators["event_handler"]["description"] == "Make a todo"


# ---------------------------------------------------------------------------
# 7. Template context injection (via ContextMixin.get_context_data)
# ---------------------------------------------------------------------------


class TestContextInjection:
    def test_action_state_injected_into_context_via_mixin(self):
        """The ContextMixin.get_context_data() exposes _action_state[name]
        as the context variable `name`. End-to-end test that the chain
        from @action wrapper → _action_state dict → context dict works."""
        from djust.mixins.context import ContextMixin

        class _ContextTestView(ContextMixin):
            template_name = "test.html"

            def __init__(self):
                self._action_state = {}
                self.todos = ["a", "b"]

            @action
            def create_todo(self, **kwargs):
                return {"created": 99}

        view = _ContextTestView()
        view.create_todo()
        ctx = view.get_context_data()

        # User attribute survived (regression check).
        assert ctx["todos"] == ["a", "b"]
        # Action state is exposed under its name.
        assert "create_todo" in ctx
        assert ctx["create_todo"]["pending"] is False
        assert ctx["create_todo"]["error"] is None
        assert ctx["create_todo"]["result"] == {"created": 99}

    def test_no_action_state_means_no_action_keys_in_context(self):
        """Negative regression: a view without any @action methods must
        not have any synthetic action keys leak into context."""
        from djust.mixins.context import ContextMixin

        class _NoActionView(ContextMixin):
            template_name = "test.html"

            def __init__(self):
                self._action_state = {}
                self.x = 1

        view = _NoActionView()
        ctx = view.get_context_data()

        assert "x" in ctx
        assert ctx["x"] == 1
        # No phantom action keys.
        assert all(not k.startswith("_") for k in ctx if k != "_jit_serialized_keys")
