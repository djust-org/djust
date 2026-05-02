"""
Regression tests for #1276.

The ``@action`` decorator's docstring promises that template can read
``{{ <action_name>.error }}`` after an exception. For that to work, the
re-render must fire — which means the exception must NOT propagate to
the dispatcher's exception path (which would send a ``{"type": "error"}``
frame and bypass the re-render).

Previous behavior: ``@action`` recorded state THEN re-raised. The
dispatcher caught and emitted an error frame. Template never saw the
recorded ``error`` field.

Current behavior: ``@action`` records state and **does not** re-raise.
The exception is logged but swallowed; the dispatcher proceeds with
normal re-render. Template sees ``{{ name.error }}`` populated.
"""

import logging

from djust.decorators import action


# ---------------------------------------------------------------------------
# Minimal host class (no LiveView dependencies needed for the contract test).
# ---------------------------------------------------------------------------


class _MinimalActionHost:
    """Minimal host that satisfies @action's runtime contract.

    @action expects ``self._action_state`` (dict). LiveView normally
    provides this, but the decorator is supposed to defensively
    initialize it on demand — we exercise that path too.
    """

    def __init__(self):
        self._action_state = {}


class _ActionTestView(_MinimalActionHost):
    @action
    def succeeds(self, **kwargs):
        return {"ok": True}

    @action
    def raises_value_error(self, **kwargs):
        raise ValueError("title required")

    @action
    def raises_runtime_error(self, **kwargs):
        raise RuntimeError("downstream failure")

    @action
    def raises_with_empty_message(self, **kwargs):
        raise ValueError()

    @action
    def raises_keyboard_interrupt(self, **kwargs):
        raise KeyboardInterrupt()


# ---------------------------------------------------------------------------
# Success path — already working before #1276; lock it in.
# ---------------------------------------------------------------------------


class TestActionSuccessRecordsState:
    def test_success_records_result(self):
        v = _ActionTestView()
        result = v.succeeds()
        assert result == {"ok": True}
        state = v._action_state["succeeds"]
        assert state == {"pending": False, "error": None, "result": {"ok": True}}


# ---------------------------------------------------------------------------
# Exception path — the #1276 fix.
# ---------------------------------------------------------------------------


class TestActionExceptionDoesNotPropagate:
    """Closes #1276: exception is recorded but does NOT re-raise."""

    def test_exception_is_swallowed_returning_none(self):
        v = _ActionTestView()
        # The decorator must NOT re-raise. Caller (dispatcher) sees
        # normal return so it can proceed with re-render.
        result = v.raises_value_error()
        assert result is None

    def test_exception_records_error_message(self):
        v = _ActionTestView()
        v.raises_value_error()
        state = v._action_state["raises_value_error"]
        assert state["pending"] is False
        assert state["error"] == "title required"
        assert state["result"] is None

    def test_exception_with_empty_message_uses_class_name(self):
        v = _ActionTestView()
        v.raises_with_empty_message()
        state = v._action_state["raises_with_empty_message"]
        # str(ValueError()) is the empty string; fallback is the class name.
        assert state["error"] == "ValueError"

    def test_runtime_error_also_swallowed(self):
        """All Exception subclasses surface via _action_state, not re-raise."""
        v = _ActionTestView()
        result = v.raises_runtime_error()
        assert result is None
        assert v._action_state["raises_runtime_error"]["error"] == "downstream failure"

    def test_baseexception_still_propagates(self):
        """KeyboardInterrupt, SystemExit, etc. (BaseException subclasses) must
        propagate — by Python convention those should never be silently caught.
        """
        v = _ActionTestView()
        try:
            v.raises_keyboard_interrupt()
        except KeyboardInterrupt:
            pass
        else:
            raise AssertionError(
                "KeyboardInterrupt must propagate; @action should only "
                "catch Exception, not BaseException"
            )

    def test_exception_is_logged(self, caplog):
        """Exception diagnostics must NOT be lost — logged at ERROR level
        (via ``logger.exception``)."""
        with caplog.at_level(logging.ERROR, logger="djust.decorators"):
            v = _ActionTestView()
            v.raises_value_error()
        # logger.exception() logs at ERROR level + includes traceback.
        records = [r for r in caplog.records if "@action" in r.getMessage()]
        assert len(records) == 1, (
            "@action's ERROR-level log message must fire when an exception "
            "is recorded; otherwise diagnostics are silently lost"
        )
        assert records[0].levelno == logging.ERROR


# ---------------------------------------------------------------------------
# Defensive: _action_state lazy-init when host doesn't initialize it.
# ---------------------------------------------------------------------------


class _LazyHost:
    """Host that doesn't initialize _action_state in __init__ — exercises
    the defensive fallback in @action_wrapper."""

    @action
    def lazy_handler(self, **kwargs):
        return "ok"


class TestActionLazyInitializesActionState:
    def test_action_state_created_on_demand(self):
        v = _LazyHost()
        assert not hasattr(v, "_action_state")
        v.lazy_handler()
        assert hasattr(v, "_action_state")
        assert v._action_state["lazy_handler"]["result"] == "ok"


# ---------------------------------------------------------------------------
# #1299: @action + @background combo — error in _action_state, not callback.
# ---------------------------------------------------------------------------


class _ActionBackgroundHost:
    """Minimal host that provides both @action's _action_state and
    @background's start_async + handle_async_result."""

    def __init__(self):
        self._action_state = {}
        self._async_tasks = {}
        self.handle_async_result_calls = []
        self._task_counter = 0

    def start_async(self, callback, *args, name=None, **kw):
        if name is None:
            self._task_counter += 1
            name = f"_async_{self._task_counter}"
        self._async_tasks[name] = (callback, args, kw)

    def handle_async_result(self, name, result=None, error=None):
        self.handle_async_result_calls.append((name, result, error))


class TestActionBackgroundCombo:
    """#1299: @action + @background contract.

    When combined, @action swallows exceptions → @background never sees them.
    The error signal is _action_state[name]["error"], NOT handle_async_result's
    error parameter.
    """

    def test_action_background_raises_error_in_action_state_not_re_raised(self):
        from djust.decorators import action, background, event_handler

        class TestView(_ActionBackgroundHost):
            @event_handler
            @background
            @action
            def slow_op(self, **kwargs):
                raise ValueError("boom")

        view = TestView()
        view.slow_op()

        # The callback should be scheduled via start_async.
        assert len(view._async_tasks) == 1
        _, (cb, args, kwargs) = list(view._async_tasks.items())[0]

        # Run the callback.  It must NOT re-raise — @action swallows.
        result = cb(*args, **kwargs)
        assert result is None, "@action swallowed the exception; callback returns None"

        # Error is recorded in _action_state.
        state = view._action_state["slow_op"]
        assert state["error"] == "boom"
        assert state["pending"] is False
        assert state["result"] is None

    def test_action_background_handle_async_result_receives_error_none(self):
        """When @action swallows the exception, the callback returns normally,
        so _run_async_work would call handle_async_result with error=None.
        """
        from djust.decorators import action, background, event_handler

        class TestView(_ActionBackgroundHost):
            @event_handler
            @background
            @action
            def slow_op(self, **kwargs):
                raise ValueError("boom")

        view = TestView()
        view.slow_op()
        _, (cb, args, kwargs) = list(view._async_tasks.items())[0]

        # Simulate what _run_async_work does on success.
        result = cb(*args, **kwargs)
        view.handle_async_result("slow_op", result=result, error=None)

        assert len(view.handle_async_result_calls) == 1
        assert view.handle_async_result_calls[0] == ("slow_op", None, None), (
            "handle_async_result receives error=None because @action "
            "swallowed the exception — the error is in _action_state"
        )

        # _action_state still has the real error.
        assert view._action_state["slow_op"]["error"] == "boom"

    def test_action_background_success_populates_result(self):
        """Happy path: @action + @background success still works."""
        from djust.decorators import action, background, event_handler

        class TestView(_ActionBackgroundHost):
            @event_handler
            @background
            @action
            def slow_op(self, **kwargs):
                return {"ok": True}

        view = TestView()
        view.slow_op()
        _, (cb, args, kwargs) = list(view._async_tasks.items())[0]

        result = cb(*args, **kwargs)
        assert result == {"ok": True}

        view.handle_async_result("slow_op", result=result, error=None)
        assert view.handle_async_result_calls[0] == ("slow_op", {"ok": True}, None)

        state = view._action_state["slow_op"]
        assert state["error"] is None
        assert state["result"] == {"ok": True}
