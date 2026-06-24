"""Direct-runtime tests for the actor-event transport hook (#1901, ADR-022 Iter 2 Phase 2.3a).

Phase 2.3a's load-bearing fold: ``dispatch_event`` had NO actor branch, while the
``use_actors`` guard lived only in ``dispatch_mount`` (which refuses SSE). A WS
view mounts in actor mode (``use_actors=True`` + an ``actor_handle``); once Phase
2.3b flips WS events onto the runtime, such a view's events would hit
``dispatch_event`` with no actor branch and silently run the handler IN-PROCESS,
desyncing the actor's server-side diff baseline.

The fix adds two Transport hooks:

* ``transport.uses_actors(view)`` — WS: ``consumer.use_actors and
  consumer.actor_handle is not None``; SSE: ``False``.
* ``transport.dispatch_actor_event(view, event_name, params, ...)`` — WS runs the
  bespoke actor block VERBATIM against ``consumer.actor_handle``; SSE never called
  (raises ``NotImplementedError`` if invoked).

``_dispatch_event_inner`` routes to ``dispatch_actor_event`` (OUTSIDE
``event_context`` — the actor block holds no render lock, matching WS) when
``uses_actors`` is true AND the event is NOT routed to a sticky child (the WS
``not is_embedded_child_target`` mutual exclusion, websocket.py:3280-3282).

These tests build a ``ViewRuntime`` over a ``WSConsumerTransport`` wrapping a FAKE
consumer (no real Channels stack) with ``use_actors=True`` + a fake ``actor_handle``,
and assert:

* ``dispatch_event`` routes to the actor (``actor_handle.event`` called + the
  framed result sent via ``_send_update`` with the consumer-owned wire version),
  NOT the normal in-process handler;
* ``uses_actors`` is False for SSE + for a WS consumer WITHOUT an actor_handle →
  the normal (non-actor) render path runs instead;
* a ``view_id``-routed (sticky-child) event is NOT sent to the actor (WS
  mutual exclusion);
* gate-off (#1468): forcing ``uses_actors`` to always-False makes the
  actor-routing test go RED (the event falls to the in-process handler).
"""

from __future__ import annotations

import pytest

from djust.decorators import event_handler
from djust.runtime import (
    SSESessionTransport,
    Transport,
    ViewRuntime,
    WSConsumerTransport,
)


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #


class _FakeActorHandle:
    """Stand-in for the Rust ``SessionActorHandle``.

    ``.event(name, params)`` records its call and returns a known render result
    (a single patch + internal version), mirroring what the real actor returns.
    """

    def __init__(self) -> None:
        self.event_calls: list = []
        # A known result the WS bespoke block frames + sends.
        self.result = {
            "patches": [{"op": "setText", "path": [0], "text": "from-actor"}],
            "html": None,
            "version": 99,  # actor-internal version; IGNORED for the wire (#1788)
        }

    async def event(self, event_name: str, params: dict):
        self.event_calls.append((event_name, dict(params)))
        return self.result


class _FakeRateLimiter:
    """Minimal limiter — the handler under test has no @rate_limit, so the
    per-handler branch in ``_validate_event_security`` is never entered."""

    warnings = 0
    max_warnings = 10

    def should_disconnect(self) -> bool:
        return False


class _FakeConsumer:
    """Minimal stand-in for ``LiveViewConsumer`` exposing only the attrs the WS
    ``dispatch_actor_event`` + ``uses_actors`` read."""

    def __init__(self, *, use_actors: bool = True, actor_handle=None) -> None:
        self.use_actors = use_actors
        self.actor_handle = actor_handle
        self._rate_limiter = _FakeRateLimiter()
        self.channel_name = "chan.actor.1901"
        self.session_id = "sess-1901"
        self._client_ip = None
        self._wire_version = 0
        # Recorded outputs.
        self.send_update_calls: list = []
        self.send_error_calls: list = []
        self.send_json_calls: list = []
        self.tt_push_calls: list = []

    def _next_version(self) -> int:
        self._wire_version += 1
        return self._wire_version

    async def _send_update(self, **kwargs):
        self.send_update_calls.append(kwargs)

    async def send_error(self, error, **kwargs):
        self.send_error_calls.append((error, kwargs))

    async def send_json(self, data):
        self.send_json_calls.append(data)

    async def _maybe_push_tt_event(self, view, snapshot):
        self.tt_push_calls.append((view, snapshot))


class _ActorView:
    """A view with a real ``@event_handler`` so ``_validate_event_security``
    passes. ``in_process_ran`` flips True ONLY if the handler is called directly
    (the in-process path) — the actor path must NOT flip it (the actor runs the
    handler inside the Rust actor, not via this Python attribute)."""

    _view_id = "top-actor-view"
    request = None

    def __init__(self) -> None:
        self.in_process_ran = False
        self._framework_attrs = frozenset(self.__dict__.keys())

    @event_handler
    def bump(self, **kwargs):
        self.in_process_ran = True


def _ws_runtime(consumer) -> ViewRuntime:
    rt = ViewRuntime(WSConsumerTransport(consumer))
    rt.view_instance = _ActorView()
    return rt


# --------------------------------------------------------------------------- #
# uses_actors: WS precondition + negatives
# --------------------------------------------------------------------------- #


def test_uses_actors_true_when_ws_consumer_has_actor_handle():
    consumer = _FakeConsumer(use_actors=True, actor_handle=_FakeActorHandle())
    transport = WSConsumerTransport(consumer)
    assert transport.uses_actors(_ActorView()) is True


def test_uses_actors_false_when_ws_consumer_lacks_actor_handle():
    consumer = _FakeConsumer(use_actors=True, actor_handle=None)
    transport = WSConsumerTransport(consumer)
    assert transport.uses_actors(_ActorView()) is False


def test_uses_actors_false_when_consumer_not_in_actor_mode():
    consumer = _FakeConsumer(use_actors=False, actor_handle=_FakeActorHandle())
    transport = WSConsumerTransport(consumer)
    assert transport.uses_actors(_ActorView()) is False


def test_sse_uses_actors_always_false():
    class _FakeSession:
        session_id = "sse-1901"

    transport = SSESessionTransport(_FakeSession())
    assert transport.uses_actors(_ActorView()) is False


@pytest.mark.asyncio
async def test_sse_dispatch_actor_event_raises_not_implemented():
    class _FakeSession:
        session_id = "sse-1901"

    transport = SSESessionTransport(_FakeSession())
    with pytest.raises(NotImplementedError):
        await transport.dispatch_actor_event(_ActorView(), "bump", {})


# --------------------------------------------------------------------------- #
# dispatch_event routes to the actor (NOT the in-process handler)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_dispatch_event_routes_to_actor_not_in_process_handler():
    actor = _FakeActorHandle()
    consumer = _FakeConsumer(use_actors=True, actor_handle=actor)
    rt = _ws_runtime(consumer)

    await rt.dispatch_event({"type": "event", "event": "bump", "params": {"amount": 1}, "ref": 7})

    # The actor handled the event ...
    assert actor.event_calls == [("bump", {"amount": 1})], (
        "the actor_handle.event() must be called with the event name + params"
    )
    # ... and the framed result was sent via the consumer's _send_update.
    assert len(consumer.send_update_calls) == 1, (
        "the actor render result must be framed + sent via consumer._send_update"
    )
    sent = consumer.send_update_calls[0]
    assert sent["patches"] == actor.result["patches"]
    assert sent["event_name"] == "bump"
    # Consumer-owned wire version (#1788) — NOT the actor's internal 99.
    assert sent["version"] == 1, (
        "the wire version must come from consumer._next_version() (#1788), "
        f"not the actor's internal result['version']; got {sent['version']!r}"
    )
    # The in-process Python handler must NOT have run (the actor owns execution).
    assert rt.view_instance.in_process_ran is False, (
        "the in-process handler must NOT run on the actor path"
    )


@pytest.mark.asyncio
async def test_actor_path_records_and_pushes_time_travel():
    """The actor path runs the tt record/push (finally block) — the hook is the
    single source of the bespoke block's finally side effects."""
    consumer = _FakeConsumer(use_actors=True, actor_handle=_FakeActorHandle())
    rt = _ws_runtime(consumer)

    await rt.dispatch_event({"type": "event", "event": "bump", "params": {}, "ref": 1})

    assert len(consumer.tt_push_calls) == 1, (
        "the actor path must call consumer._maybe_push_tt_event in its finally"
    )


# --------------------------------------------------------------------------- #
# Non-actor path: normal in-process render when uses_actors is False
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_no_actor_handle_falls_to_in_process_path():
    """WS consumer in actor mode but WITHOUT an actor_handle → uses_actors False
    → the event runs the normal (non-actor) render path, which calls the
    in-process handler. We don't assert the full render here (no real consumer
    render stack); we assert the actor branch was NOT taken."""
    consumer = _FakeConsumer(use_actors=False, actor_handle=None)
    rt = _ws_runtime(consumer)

    # The normal path enters event_context (borrows the consumer's render-lock).
    # Our fake consumer has no _render_lock, so the borrow raises AttributeError
    # — which itself PROVES the actor branch was bypassed (it would have returned
    # before reaching event_context). That is the load-bearing assertion.
    with pytest.raises(AttributeError):
        await rt.dispatch_event({"type": "event", "event": "bump", "params": {}, "ref": 1})

    # The actor was never consulted.
    assert consumer.send_update_calls == [], "no actor frame should have been sent"


# --------------------------------------------------------------------------- #
# Sticky-child mutual exclusion: view_id-routed events skip the actor
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_view_id_routed_event_skips_actor():
    """An event with a ``view_id`` for a DIFFERENT (child) view is NOT sent to
    the actor — mirrors WS ``not is_embedded_child_target`` (websocket.py:3280)."""
    actor = _FakeActorHandle()
    consumer = _FakeConsumer(use_actors=True, actor_handle=actor)
    rt = _ws_runtime(consumer)

    # view_id != the top-level view's _view_id => routes to a child => actor skipped.
    # The normal path then enters event_context → borrows the (missing) lock →
    # AttributeError, which proves the actor branch was bypassed.
    with pytest.raises(AttributeError):
        await rt.dispatch_event(
            {
                "type": "event",
                "event": "bump",
                "params": {"view_id": "some-child-view"},
                "ref": 1,
            }
        )
    assert actor.event_calls == [], "a sticky-child (view_id) event must NOT reach the actor"


@pytest.mark.asyncio
async def test_view_id_equal_to_top_view_still_routes_to_actor():
    """A ``view_id`` equal to the TOP view's _view_id is NOT a child target — the
    event is for the top view, so the actor path still applies (WS parity)."""
    actor = _FakeActorHandle()
    consumer = _FakeConsumer(use_actors=True, actor_handle=actor)
    rt = _ws_runtime(consumer)

    await rt.dispatch_event(
        {
            "type": "event",
            "event": "bump",
            "params": {"view_id": "top-actor-view"},  # == _ActorView._view_id
            "ref": 1,
        }
    )
    assert len(actor.event_calls) == 1, (
        "a view_id equal to the top view's _view_id is NOT a child target — "
        "the actor path must still fire"
    )


# --------------------------------------------------------------------------- #
# Gate-off (#1468): force uses_actors=False → actor-routing test goes RED
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_gate_off_uses_actors_false_falls_to_in_process(monkeypatch):
    """Prove the actor-routing test is non-tautological.

    GATE-OFF: monkeypatch the WS transport's ``uses_actors`` to always return
    False. The same frame that routed to the actor above now FALLS to the normal
    path (event_context → missing-lock AttributeError), and the actor is never
    consulted — demonstrating the routing genuinely depends on ``uses_actors``."""
    actor = _FakeActorHandle()
    consumer = _FakeConsumer(use_actors=True, actor_handle=actor)
    rt = _ws_runtime(consumer)

    monkeypatch.setattr(rt.transport, "uses_actors", lambda view: False)

    with pytest.raises(AttributeError):
        await rt.dispatch_event(
            {"type": "event", "event": "bump", "params": {"amount": 1}, "ref": 7}
        )

    assert actor.event_calls == [], (
        "GATE-OFF: with uses_actors forced False, the event must NOT reach the actor"
    )
    assert consumer.send_update_calls == [], "GATE-OFF: no actor render frame should have been sent"


# --------------------------------------------------------------------------- #
# Protocol + adapter surface
# --------------------------------------------------------------------------- #


def test_hooks_on_protocol_and_both_adapters():
    assert hasattr(Transport, "uses_actors")
    assert hasattr(Transport, "dispatch_actor_event")
    assert hasattr(WSConsumerTransport, "uses_actors")
    assert hasattr(WSConsumerTransport, "dispatch_actor_event")
    assert hasattr(SSESessionTransport, "uses_actors")
    assert hasattr(SSESessionTransport, "dispatch_actor_event")
