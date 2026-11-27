"""#4322: every server answer to an event names the event's ``ref``.

A hook's ``pushEvent(..., {outcome: true})`` settles by ``ref`` (transport plan
D2). An error frame sent while an event's turn runs echoes that ref, so the
client settles that one request (``error`` / ``rejected`` / ``rate_limited``)
instead of every pending one; a turn-level rate-limit refusal carries it too.
"""

from __future__ import annotations

import contextlib
import uuid
from typing import Any, Dict, List, Optional

import pytest

from djust import LiveView
from djust.decorators import event_handler, rate_limit
from djust.runtime import ViewRuntime
from djust.security.error_handling import build_error_frame, event_error_ref

pytestmark = [pytest.mark.pr_tier]


class _Transport:
    """Sends errors through the shared ``build_error_frame``, as WS and SSE do."""

    def __init__(self) -> None:
        self.session_id = str(uuid.uuid4())
        self.client_ip: Optional[str] = None
        self.sent: List[Dict[str, Any]] = []

    async def send(self, data: Dict[str, Any]) -> None:
        self.sent.append(data)

    async def send_error(self, error: str, **kwargs: Any) -> None:
        self.sent.append(build_error_frame(error, **kwargs))

    async def close(self, code: int = 1000) -> None:
        pass

    def next_client_version(self, html: Optional[str], rust_version: int) -> int:
        return rust_version

    def build_request(self) -> Optional[Any]:
        return None

    def on_view_mounted(self, view_instance: Any) -> None:
        pass

    @contextlib.asynccontextmanager
    async def event_context(self, view: Any):
        yield


class _Page(LiveView):
    template = '<div dj-root dj-id="0">p</div>'

    @event_handler()
    def pick(self, choice: int):
        pass

    @event_handler()
    @rate_limit(rate=0.001, burst=1, on_exceed="drop")
    def once(self):
        pass


async def _runtime():
    from djust.tests._live_session import amount_on_runtime

    transport = _Transport()
    runtime = ViewRuntime(transport)
    await amount_on_runtime(runtime, _Page())
    transport.sent.clear()
    return runtime, transport


def _errors(transport):
    return [f for f in transport.sent if f.get("type") == "error"]


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_strict_parameter_rejection_echoes_the_ref():
    runtime, transport = await _runtime()
    await runtime.dispatch_event(
        {"type": "event", "event": "pick", "params": {"choice": "x"}, "ref": 41}
    )
    (frame,) = _errors(transport)
    assert frame["ref"] == 41
    assert frame["validation_details"]


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_unknown_handler_error_echoes_the_ref():
    runtime, transport = await _runtime()
    await runtime.dispatch_event({"type": "event", "event": "nope", "params": {}, "ref": 7})
    (frame,) = _errors(transport)
    assert frame["ref"] == 7


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_per_handler_rate_limit_is_coded_and_echoes_the_ref():
    runtime, transport = await _runtime()
    await runtime.dispatch_event({"type": "event", "event": "once", "params": {}, "ref": 1})
    transport.sent.clear()
    await runtime.dispatch_event({"type": "event", "event": "once", "params": {}, "ref": 2})
    (frame,) = _errors(transport)
    assert frame["code"] == "rate_limited"
    assert frame["ref"] == 2


def test_error_frame_outside_an_event_has_no_ref():
    assert "ref" not in build_error_frame("x")


def test_error_frame_keeps_its_own_ref_and_source():
    with event_error_ref(5):
        assert build_error_frame("x")["ref"] == 5
        assert build_error_frame("x", ref=9)["ref"] == 9
        # Background work started by the turn inherits the context; its frames
        # are not answers to the event.
        assert "ref" not in build_error_frame("x", source="async")
    assert "ref" not in build_error_frame("x")


@pytest.mark.parametrize("ref", [None, "5", float("nan"), float("inf"), [5]])
def test_only_an_int_or_finite_float_ref_is_echoed(ref):
    with event_error_ref(ref):
        assert "ref" not in build_error_frame("x")


def test_float_ref_is_echoed_as_int():
    with event_error_ref(12.0):
        assert build_error_frame("x")["ref"] == 12


@pytest.mark.asyncio
async def test_turn_rate_limit_refusal_carries_the_ref():
    from djust._turn_ports import RuntimeTurnPort
    from djust.turns import Refusal, Turn

    transport = _Transport()

    class _Port(RuntimeTurnPort):
        def __init__(self):
            self.transport = transport

    turn = Turn.__new__(Turn)
    turn.kind = "event"
    turn._ref = 33
    await _Port().turn_refused(turn, Refusal(kind="rate_limited"))
    limited = [f for f in transport.sent if f.get("type") == "rate_limit_exceeded"]
    assert limited and limited[0]["ref"] == 33
