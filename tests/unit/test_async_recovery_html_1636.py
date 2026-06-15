"""Regression: ``start_async`` completion must refresh the recovery baseline (#1636).

The bug
-------

When a client VDOM patch fails (e.g. an ``{% if %}`` block that adds a sibling),
the client sends ``request_html`` and the server serves ``_recovery_html`` —
then clears it (one-time use, ``handle_request_html`` at ``websocket.py:4520``).

``LiveViewConsumer._run_async_work`` re-renders + sends patches when a
``start_async`` background callback completes, but — unlike ``handle_event``
(``websocket.py:3633``) and ``server_push`` (``websocket.py:4990``, the #1202
fix) — it never updated ``_recovery_html`` / ``_recovery_version``. So after a
recovery consumed the baseline, the next ``request_html`` (triggered by a
re-failing async patch) found ``_recovery_html=None``, returned
"Recovery HTML unavailable", and the client froze at the transitional state
even though the backend had advanced.

This is the reporter's stated "most painful" half of #1636: after one
``{% if %}`` patch failure, async-callback state pushes stop reaching the
client.

The fix
-------

``_run_async_work`` sets ``self._recovery_html = html`` and
``self._recovery_version = version`` after ``render_with_diff()``, in BOTH the
patches branch and the full-HTML fallback branch — mirroring ``handle_event`` /
``server_push``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from djust.websocket import LiveViewConsumer


def _make_consumer(render_return):
    """Minimal consumer wired to drive ``_run_async_work`` deterministically."""
    consumer = LiveViewConsumer()
    consumer.view_instance = MagicMock()
    consumer.view_instance._skip_render = False
    consumer.view_instance._sync_state_to_rust = MagicMock()
    # No cancellation / no handle_async_result hooks for the happy path.
    del consumer.view_instance._async_cancelled
    del consumer.view_instance.handle_async_result
    consumer.view_instance.render_with_diff = MagicMock(return_value=render_return)
    consumer.view_instance._strip_comments_and_whitespace = MagicMock(side_effect=lambda h: h)
    consumer.view_instance._extract_liveview_content = MagicMock(side_effect=lambda h: h)
    consumer.use_binary = False
    consumer.send = AsyncMock()
    consumer._send_update = AsyncMock()
    # Flush helpers are no-ops here.
    for name in (
        "_flush_push_events",
        "_flush_flash",
        "_flush_page_metadata",
        "_flush_pending_layout",
        "_flush_deferred",
    ):
        setattr(consumer, name, AsyncMock())
    return consumer


async def _run(consumer):
    await consumer._run_async_work(
        task_name="t",
        callback=lambda: None,
        args=(),
        kwargs={},
        event_name="pick",
    )


@pytest.mark.asyncio
async def test_async_render_stores_recovery_html():
    """After an async-completion render with patches, the recovery baseline
    must be refreshed so a later ``request_html`` has fresh HTML to serve."""
    consumer = _make_consumer(("<div>after-async</div>", '[{"type":"SetText"}]', 42))

    await _run(consumer)

    consumer._send_update.assert_awaited_once()
    assert consumer._recovery_html == "<div>after-async</div>"
    # #1788: _recovery_version is now the CONSUMER-owned wire version
    # (_last_sent_version after one _next_version() in this single async render),
    # NOT the Rust version (42). Decoupling the wire version from the Rust
    # baseline is the whole point of #1788.
    assert consumer._recovery_version == 1


@pytest.mark.asyncio
async def test_recovery_html_refreshed_across_multiple_async_renders():
    """Consecutive async renders must each refresh the recovery baseline so a
    stale earlier render is never served on a later recovery."""
    consumer = _make_consumer(None)
    # #1788: use NON-sequential Rust versions (10, 20, 30) to prove the
    # recovery version tracks the CONSUMER counter (1, 2, 3 across three
    # renders), not the Rust version.
    consumer.view_instance.render_with_diff = MagicMock(
        side_effect=[
            ("<div>first</div>", '[{"type":"SetText","v":1}]', 10),
            ("<div>second</div>", '[{"type":"SetText","v":2}]', 20),
            ("<div>third</div>", '[{"type":"SetText","v":3}]', 30),
        ]
    )

    for _ in range(3):
        await _run(consumer)

    assert consumer._recovery_html == "<div>third</div>"
    # Consumer counter after 3 renders == 3 (decoupled from Rust version 30).
    assert consumer._recovery_version == 3


@pytest.mark.asyncio
async def test_async_full_html_fallback_stores_recovery_html():
    """The full-HTML fallback branch (``patches is None``) sends full HTML to
    the client, so the recovery baseline must track that HTML too."""
    consumer = _make_consumer(("<div>full-html-fallback</div>", None, 7))

    await _run(consumer)

    consumer._send_update.assert_awaited_once()
    # The async else-branch sends full HTML; recovery baseline must match it.
    assert consumer._recovery_html == "<div>full-html-fallback</div>"
    # #1788: consumer-owned wire version (1 after one render), not Rust 7.
    assert consumer._recovery_version == 1


@pytest.mark.asyncio
async def test_request_html_after_async_completion_serves_fresh_html():
    """End-to-end continuity (the load-bearing test): a recovery that consumed
    ``_recovery_html`` must be re-armed by a subsequent async completion, so the
    NEXT ``request_html`` serves the advanced state instead of failing.

    Mirrors the reporter's sequence: event → patch fails → request_html (clears
    baseline) → start_async completes → patch fails again → request_html must
    succeed (not "Recovery HTML unavailable")."""
    consumer = _make_consumer(("<div>routed</div>", '[{"type":"SetText"}]', 99))

    # Simulate the first recovery having already consumed the baseline.
    consumer._recovery_html = None
    consumer._recovery_version = 0

    # Async work completes and re-renders the advanced (routed) state.
    await _run(consumer)

    # A subsequent request_html now has fresh HTML to serve.
    await consumer.handle_request_html({})

    sent = consumer.send.await_args
    # handle_request_html uses self.send_json -> self.send; assert it served
    # the routed HTML rather than an error envelope.
    payload = (
        sent.kwargs.get("text_data")
        if sent and sent.kwargs
        else (sent.args[0] if sent and sent.args else "")
    )
    assert "routed" in str(payload), (
        f"request_html after async completion must serve the advanced HTML; got: {payload!r}"
    )
    assert "unavailable" not in str(payload).lower()
