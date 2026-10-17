"""The WebSocket consumer and the SSE session build error frames the same way.

Both ``send_error`` implementations go through
``djust.security.error_handling.build_error_frame``, so debug fields are
handled in one place: ``debug_detail``, ``hint`` and ``_exc_info`` are never
copied onto a frame as given, and ``debug_detail`` / ``hint`` / the
abbreviated ``traceback`` are added only under ``DEBUG``. Each case sends the
same arguments through both transports and compares the frames.
"""

import asyncio
from unittest.mock import AsyncMock

import pytest
from django.test import override_settings

from djust.security.error_handling import DEBUG_ONLY_ERROR_KEYS, build_error_frame
from djust.sse import SSESession
from djust.websocket import LiveViewConsumer


def _raise_and_capture():
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        return sys.exc_info()


CASES = {
    "plain": ("Something failed", {}),
    "routing fields": ("Something failed", {"code": "render_error", "source": "async", "ref": 3}),
    "debug detail": ("Something failed", {"debug_detail": "raw detail", "hint": "try this"}),
    "same detail": ("Something failed", {"debug_detail": "Something failed"}),
    "exc info": ("Something failed", {"_exc_info": _raise_and_capture()}),
    "no traceback": ("Something failed", {"_exc_info": (None, None, None)}),
}


def _ws_frame(error, context):
    consumer = LiveViewConsumer()
    consumer.send_json = AsyncMock()
    asyncio.run(consumer.send_error(error, **context))
    return consumer.send_json.await_args.args[0]


def _sse_frame(error, context):
    session = SSESession("parity-session")
    pushed = []
    session.push = pushed.append
    asyncio.run(session.send_error(error, **context))
    [frame] = pushed
    return frame


@pytest.mark.parametrize("debug", [False, True])
@pytest.mark.parametrize("case", sorted(CASES))
def test_both_transports_send_the_same_frame(case, debug):
    error, context = CASES[case]
    with override_settings(DEBUG=debug):
        ws = _ws_frame(error, context)
        sse = _sse_frame(error, context)
    assert ws == sse
    assert ws["type"] == "error" and ws["error"] == error
    assert "_exc_info" not in ws


@pytest.mark.parametrize("case", sorted(CASES))
def test_without_debug_no_debug_field_is_sent(case):
    error, context = CASES[case]
    with override_settings(DEBUG=False):
        frame = build_error_frame(error, **context)
    assert not (set(frame) & (DEBUG_ONLY_ERROR_KEYS | {"traceback"})), frame


def test_under_debug_the_detail_hint_and_traceback_are_added():
    with override_settings(DEBUG=True):
        frame = build_error_frame(
            "Something failed",
            debug_detail="raw detail",
            hint="try this",
            _exc_info=_raise_and_capture(),
        )
    assert frame["debug_detail"] == "raw detail"
    assert frame["hint"] == "try this"
    assert "ValueError" not in frame["error"]
    assert "_raise_and_capture" in frame["traceback"]


def test_under_debug_an_unchanged_detail_and_an_empty_exc_info_add_nothing():
    with override_settings(DEBUG=True):
        frame = build_error_frame("Same", debug_detail="Same", _exc_info=(None, None, None))
    assert frame == {"type": "error", "error": "Same"}
