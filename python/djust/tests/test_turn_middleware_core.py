"""Core of the turn-middleware chain (ADR-044): building, order, refusal, context."""

from __future__ import annotations

import asyncio
import contextvars

import pytest
from django.core.exceptions import ImproperlyConfigured, PermissionDenied
from django.test import override_settings

from djust import turns
from djust.observability import timings
from djust.turns import Refusal, Refuse, Turn, current_turn, run_turn

MOD = __name__
CALLS: list = []
caller: contextvars.ContextVar = contextvars.ContextVar("test_caller", default=None)


async def mw_a(turn, next):
    CALLS.append("a-in")
    result = await next(turn)
    CALLS.append("a-out")
    return result


async def mw_b(turn, next):
    CALLS.append("b-in")
    result = await next(turn)
    CALLS.append("b-out")
    return result


async def mw_deny(turn, next):
    CALLS.append("deny")
    return Refuse.deny("nope")


async def mw_raise_pd(turn, next):
    raise PermissionDenied("x")


async def mw_raise(turn, next):
    raise RuntimeError("secret-detail")


async def mw_set_caller(turn, next):
    token = caller.set("alice")
    try:
        return await next(turn)
    finally:
        caller.reset(token)


def mw_sync(turn, next):  # not async: rejected
    return next(turn)


not_callable = 42


class _Port:
    def __init__(self, handled=True, reauth_ok=True):
        self.refused = []
        self.failed = []
        self.reauths = []
        self.handled = handled
        self.reauth_ok = reauth_ok

    async def turn_refused(self, turn, refusal):
        self.refused.append(refusal)

    async def turn_failed(self, turn, exc):
        self.failed.append(exc)
        return self.handled

    async def turn_reauth(self, turn, *, server):
        self.reauths.append(server)
        return self.reauth_ok


@pytest.fixture(autouse=True)
def _clean():
    CALLS.clear()
    turns.reset_chain()
    yield
    turns.reset_chain()


def _mw(*names):
    return override_settings(LIVEVIEW_MIDDLEWARE=[f"{MOD}.{n}" for n in names])


async def _body(turn):
    CALLS.append("body")


@pytest.mark.asyncio
async def test_no_app_middleware_runs_body():
    port = _Port()
    assert await run_turn(Turn("event", "ws", port=port), _body) is None
    assert CALLS == ["body"]
    assert not turns.app_chain_configured()


@pytest.mark.asyncio
async def test_app_middleware_runs_in_listed_order_around_body():
    with _mw("mw_a", "mw_b"):
        assert turns.app_chain_configured()
        assert await run_turn(Turn("event", "ws", port=_Port()), _body) is None
    assert CALLS == ["a-in", "b-in", "body", "b-out", "a-out"]


@pytest.mark.asyncio
async def test_refusal_stops_chain_and_body_and_is_answered_once():
    port = _Port()
    with _mw("mw_a", "mw_deny", "mw_b"):
        refusal = await run_turn(Turn("event", "ws", port=port), _body)
    assert isinstance(refusal, Refusal) and refusal.kind == "deny"
    assert refusal.message == "nope" and refusal.sent
    assert CALLS == ["a-in", "deny", "a-out"]  # b and the body never ran
    assert port.refused == [refusal]


@pytest.mark.asyncio
async def test_permission_denied_anywhere_becomes_deny():
    port = _Port()
    with _mw("mw_raise_pd"):
        refusal = await run_turn(Turn("mount", "sse", port=port), _body)
    assert refusal.kind == "deny"
    assert port.refused and not port.failed

    async def body_raises(turn):
        raise PermissionDenied

    port = _Port()
    refusal = await run_turn(Turn("event", "ws", port=port), body_raises)
    assert refusal.kind == "deny"


@pytest.mark.asyncio
async def test_error_boundary_hands_errors_to_the_port():
    port = _Port(handled=True)
    with _mw("mw_raise"):
        assert await run_turn(Turn("event", "ws", port=port), _body) is None
    assert len(port.failed) == 1 and isinstance(port.failed[0], RuntimeError)

    port = _Port(handled=False)
    with _mw("mw_raise"):
        with pytest.raises(RuntimeError):
            await run_turn(Turn("event", "ws", port=port), _body)

    # No port (nothing to answer on): the error propagates.
    with _mw("mw_raise"):
        with pytest.raises(RuntimeError):
            await run_turn(Turn("event", "http"), _body)


@pytest.mark.parametrize(
    "value",
    [
        [f"{MOD}.mw_sync"],
        [f"{MOD}.not_callable"],
        [f"{MOD}.does_not_exist"],
        "djust.not.a.list",
        [42],
    ],
)
def test_bad_configuration_is_refused(value):
    with override_settings(LIVEVIEW_MIDDLEWARE=value):
        with pytest.raises(ImproperlyConfigured):
            turns.app_chain_configured()


@pytest.mark.asyncio
async def test_override_settings_rebuilds_the_chain():
    with _mw("mw_a"):
        await run_turn(Turn("event", "ws", port=_Port()), _body)
    assert CALLS == ["a-in", "body", "a-out"]
    CALLS.clear()
    await run_turn(Turn("event", "ws", port=_Port()), _body)
    assert CALLS == ["body"]


@pytest.mark.asyncio
async def test_current_turn_is_the_running_turn_and_closes():
    seen = {}
    spawned = []

    async def later():
        await asyncio.sleep(0)
        seen["task_after_turn"] = current_turn()

    async def body(turn):
        seen["inside"] = current_turn()
        spawned.append(asyncio.ensure_future(later()))

    turn = Turn("event", "ws", port=_Port())
    await run_turn(turn, body)
    await spawned[0]
    assert seen["inside"] is turn
    assert seen["task_after_turn"] is None  # the spawning turn has ended
    assert current_turn() is None


@pytest.mark.asyncio
async def test_context_set_by_middleware_reaches_handler_sync_code_and_tasks():
    from asgiref.sync import sync_to_async

    seen = {}
    spawned = []

    async def background():
        await asyncio.sleep(0.01)  # outlives the turn and the middleware's reset
        seen["task"] = caller.get()

    async def body(turn):
        seen["handler"] = caller.get()
        seen["sync"] = await sync_to_async(caller.get)()
        spawned.append(asyncio.ensure_future(background()))

    with _mw("mw_set_caller"):
        await run_turn(Turn("event", "ws", port=_Port()), body)
    await spawned[0]
    assert seen == {"handler": "alice", "sync": "alice", "task": "alice"}
    assert caller.get() is None  # reset in the caller's own context


class _View:
    login_required = True


class _OpenView:
    login_required = False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,transport,expected",
    [
        ("event", "ws", [False]),
        ("params", "sse", [False]),
        ("render", "ws", [False]),
        ("push", "ws", [True]),
        ("async", "sse", [True]),
        ("mount", "ws", []),
        ("reconnect", "ws", []),
        ("event", "http", []),
        ("event", "api", []),
    ],
)
async def test_reauth_builtin_runs_for_live_turns_only(kind, transport, expected):
    port = _Port()
    with override_settings(LIVEVIEW_CONFIG={"reauth_on_event": True}):
        await run_turn(Turn(kind, transport, view=_View(), port=port), _body)
    assert port.reauths == expected
    assert CALLS == ["body"]


@pytest.mark.asyncio
async def test_reauth_builtin_off_by_default_and_for_open_views():
    port = _Port()
    await run_turn(Turn("event", "ws", view=_View(), port=port), _body)
    with override_settings(LIVEVIEW_CONFIG={"reauth_on_event": True}):
        await run_turn(Turn("event", "ws", view=_OpenView(), port=port), _body)
    assert port.reauths == []


@pytest.mark.asyncio
async def test_failed_reauth_refuses_before_app_middleware_and_body():
    port = _Port(reauth_ok=False)
    with override_settings(LIVEVIEW_CONFIG={"reauth_on_event": True}), _mw("mw_a"):
        refusal = await run_turn(Turn("event", "ws", view=_View(), port=port), _body)
    assert refusal.kind == "login" and refusal.sent
    assert CALLS == []  # app middleware is inside the built-ins
    assert port.refused == []  # the transport already answered


@pytest.mark.asyncio
async def test_turn_timing_is_recorded():
    timings._clear_timings()

    class TimedView:
        pass

    await run_turn(Turn("push", "ws", source="tick", view=TimedView(), port=_Port()), _body)
    rows = timings.get_turn_timing_stats(kind="push")
    assert [(r["source"], r["transport"], r["view_class"], r["count"]) for r in rows] == [
        ("tick", "ws", "TimedView", 1)
    ]


def test_turn_user_and_session_fall_back_to_view_request_then_scope():
    class Req:
        user = "req-user"
        session = "req-session"

    class V:
        request = Req()

    assert Turn("event", "ws", view=V()).user == "req-user"
    t = Turn("mount", "ws", scope={"user": "scope-user", "session": "scope-session"})
    assert (t.user, t.session) == ("scope-user", "scope-session")
    assert Turn("event", "ws", view=V(), request=None).session == "req-session"
    assert Turn("event", "ws").user is None
    assert Turn("event", "ws", view=V()).view_class is V
