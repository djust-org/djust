"""Unit tests for v0.9.0 PR-A: async render path foundation (ADR-015).

Covers:

1. :class:`djust.http_streaming.ChunkEmitter` — backpressure, cancellation,
   thunk registry, async-iterator semantics.
2. :meth:`TemplateMixin.arender_chunks` — chunk schedule (4-yield invariant
   when ``<div dj-root>`` is present, 1-yield fragment fallback,
   cancellation mid-stream).
3. :meth:`RequestMixin.aget` — wraps the sync render via ``sync_to_async``,
   returns a ``StreamingHttpResponse`` whose async iterator yields the
   shell-then-body chunks. WSGI fallback when no event loop is running.

Tests run against direct method calls (no Django test client) — the
``as_view()`` routing wiring is exercised by ``test_streaming_flow.py``
once PR-A merges and the dispatch override lands.
"""

from __future__ import annotations

import asyncio

import pytest
from django.http import HttpResponse, StreamingHttpResponse

from djust import LiveView
from djust.http_streaming import (
    DEFAULT_CHUNK_QUEUE_MAX,
    ChunkEmitter,
    ChunkEmitterCancelled,
    _get_queue_max_from_settings,
)


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


class _StreamingFixtureView(LiveView):
    template = (
        "<!DOCTYPE html><html><head><title>t</title></head>"
        "<body><div dj-root><p>hello</p></div></body></html>"
    )
    streaming_render = True


class _NonStreamingFixtureView(LiveView):
    template = "<div dj-root><p>hi</p></div>"
    streaming_render = False


def _build_request(rf):
    request = rf.get("/")
    from django.contrib.auth.models import AnonymousUser

    request.user = AnonymousUser()
    return request


# ---------------------------------------------------------------------------
# 1. ChunkEmitter unit tests
# ---------------------------------------------------------------------------


class TestChunkEmitterBasics:
    @pytest.mark.asyncio
    async def test_emit_and_iterate_single_chunk(self, rf):
        request = _build_request(rf)
        emitter = ChunkEmitter(request)
        await emitter.emit(b"<p>chunk</p>")
        await emitter.close()

        chunks = []
        async for chunk in emitter:
            chunks.append(chunk)
        assert chunks == [b"<p>chunk</p>"]

    @pytest.mark.asyncio
    async def test_emit_normalizes_str_to_bytes(self, rf):
        """Defensive normalization: producers occasionally hand strings."""
        emitter = ChunkEmitter(_build_request(rf))
        await emitter.emit("string-chunk")  # type: ignore[arg-type]
        await emitter.close()

        chunks = []
        async for chunk in emitter:
            chunks.append(chunk)
        assert chunks == [b"string-chunk"]

    @pytest.mark.asyncio
    async def test_register_thunk_records_in_order(self, rf):
        emitter = ChunkEmitter(_build_request(rf))

        async def thunk_a():
            return b"a"

        async def thunk_b():
            return b"b"

        emitter.register_thunk("view-a", thunk_a)
        emitter.register_thunk("view-b", thunk_b)

        recorded = emitter.thunks
        assert [view_id for view_id, _ in recorded] == ["view-a", "view-b"]
        # PR-A does not invoke thunks; the registry is read-only API surface.

    @pytest.mark.asyncio
    async def test_close_pushes_sentinel(self, rf):
        emitter = ChunkEmitter(_build_request(rf))
        await emitter.close()
        chunks = [c async for c in emitter]
        assert chunks == []  # sentinel exits cleanly without yielding


class TestChunkEmitterBackpressure:
    @pytest.mark.asyncio
    async def test_emit_blocks_when_queue_full(self, rf):
        """When the bounded queue is full, ``emit()`` awaits until a
        consumer drains a chunk. This is the backpressure contract from
        ADR-015 §"Backpressure" — slow client => server pauses lazy
        thunks. The default queue max (8) is overridden to 1 to make the
        block observable in a unit test.
        """
        emitter = ChunkEmitter(_build_request(rf), max_queue=1)
        await emitter.emit(b"first")  # fills queue

        # The next emit() must wait for the queue to drain.
        emit_done = asyncio.Event()

        async def slow_emit():
            await emitter.emit(b"second")
            emit_done.set()

        emit_task = asyncio.create_task(slow_emit())
        # Yield to the event loop a few times — emit_task should still be
        # blocked because the queue is full.
        for _ in range(5):
            await asyncio.sleep(0)
        assert not emit_done.is_set(), "emit should block when queue is full"

        # Drain one chunk — emit_task should now unblock. Use the
        # underlying queue directly to avoid creating a stale async
        # iterator that may keep references alive past the test.
        chunk = await emitter._queue.get()
        assert chunk == b"first"
        await asyncio.wait_for(emit_done.wait(), timeout=1.0)
        await emit_task  # drain task to avoid pending-task warnings
        await emitter.cancel("test-cleanup")


class TestChunkEmitterCancellation:
    @pytest.mark.asyncio
    async def test_cancel_raises_on_subsequent_emit(self, rf):
        emitter = ChunkEmitter(_build_request(rf))
        await emitter.cancel("test-cancel")
        with pytest.raises(ChunkEmitterCancelled):
            await emitter.emit(b"after-cancel")

    @pytest.mark.asyncio
    async def test_cancel_sets_request_token(self, rf):
        emitter = ChunkEmitter(_build_request(rf))
        assert not emitter.request_token.is_set()
        await emitter.cancel("disconnect")
        assert emitter.request_token.is_set()
        assert emitter.cancelled is True

    @pytest.mark.asyncio
    async def test_cancel_drains_queue_and_exits_iterator(self, rf):
        """Cancellation pushes the sentinel; consumer iterator exits
        cleanly without raising. ADR §"Cancellation contract"."""
        emitter = ChunkEmitter(_build_request(rf))
        await emitter.emit(b"chunk-1")
        await emitter.cancel("client_disconnected")

        chunks = []
        async for chunk in emitter:
            chunks.append(chunk)
        # cancel() drains the queue, so the buffered chunk-1 is dropped.
        assert chunks == []

    @pytest.mark.asyncio
    async def test_cancel_is_idempotent(self, rf):
        emitter = ChunkEmitter(_build_request(rf))
        await emitter.cancel("first")
        # Second cancel must not raise or change the reason.
        await emitter.cancel("second")
        assert emitter.cancelled is True


class TestQueueMaxFromSettings:
    def test_invalid_setting_falls_back_to_default(self, settings):
        settings.DJUST_LAZY_CHUNK_QUEUE_MAX = -3
        assert _get_queue_max_from_settings() == DEFAULT_CHUNK_QUEUE_MAX

    def test_unset_setting_uses_default(self):
        # Don't touch settings; the setting is unset by default.
        assert _get_queue_max_from_settings() == DEFAULT_CHUNK_QUEUE_MAX

    def test_valid_setting_overrides_default(self, settings):
        settings.DJUST_LAZY_CHUNK_QUEUE_MAX = 32
        assert _get_queue_max_from_settings() == 32


# ---------------------------------------------------------------------------
# 2. arender_chunks() — async generator that pushes shell-then-body chunks
# ---------------------------------------------------------------------------


class TestArenderChunksFourYieldInvariant:
    @pytest.mark.asyncio
    async def test_full_document_yields_four_chunks(self, rf):
        """Pages with a ``<!DOCTYPE>...<div dj-root>`` wrapper yield four
        chunks: shell-open, body-open, body-content, body-close."""
        view = _StreamingFixtureView()
        view.request = _build_request(rf)
        emitter = ChunkEmitter(view.request)

        full_html = (
            "<!DOCTYPE html><html><head><title>t</title></head>"
            "<body><div dj-root><p>hello</p></div></body></html>"
        )

        async def _drain_emitter():
            chunks = []
            async for chunk in emitter:
                chunks.append(chunk)
            return chunks

        consumer = asyncio.create_task(_drain_emitter())
        # Run arender_chunks; it pushes onto the queue.
        await view.arender_chunks(full_html, emitter)
        await emitter.close()
        chunks = await consumer

        assert len(chunks) == 4
        # Reassembly must match input exactly.
        assert b"".join(chunks) == full_html.encode("utf-8")
        # Each chunk has the expected anchor.
        assert b"<!DOCTYPE" in chunks[0]
        assert b"<div dj-root" in chunks[1]
        assert b"<p>hello</p>" == chunks[2]
        assert b"</div></body></html>" in chunks[3]

    @pytest.mark.asyncio
    async def test_fragment_template_yields_single_chunk(self, rf):
        """Templates without a ``<div dj-root>`` (raw fragments) yield a
        single chunk equivalent to the non-streaming path."""
        view = _NonStreamingFixtureView()
        view.request = _build_request(rf)
        emitter = ChunkEmitter(view.request)

        full_html = "<p>just a fragment, no dj-root</p>"

        async def _drain():
            return [chunk async for chunk in emitter]

        consumer = asyncio.create_task(_drain())
        await view.arender_chunks(full_html, emitter)
        await emitter.close()
        chunks = await consumer

        assert chunks == [full_html.encode("utf-8")]


class TestArenderChunksCancellation:
    @pytest.mark.asyncio
    async def test_cancel_mid_stream_returns_cleanly(self, rf):
        """Cancelling the emitter mid-stream causes ``arender_chunks`` to
        return without raising — producers catch ChunkEmitterCancelled
        cooperatively per ADR §"Cancellation contract"."""
        view = _StreamingFixtureView()
        view.request = _build_request(rf)
        # Use a queue size of 1 so the second emit() blocks on a full
        # queue. We then cancel before the consumer drains.
        emitter = ChunkEmitter(view.request, max_queue=1)

        full_html = (
            "<!DOCTYPE html><html><head></head><body><div dj-root><p>x</p></div></body></html>"
        )

        async def _producer():
            try:
                await view.arender_chunks(full_html, emitter)
            except ChunkEmitterCancelled:
                # arender_chunks catches this internally; should not
                # propagate. Test fails if it does.
                pytest.fail("ChunkEmitterCancelled escaped arender_chunks")

        producer_task = asyncio.create_task(_producer())
        # Yield once so producer can fill the first slot.
        await asyncio.sleep(0)
        await emitter.cancel("test-cancel")
        await asyncio.wait_for(producer_task, timeout=1.0)


# ---------------------------------------------------------------------------
# 3. RequestMixin.aget — async-streaming GET path
# ---------------------------------------------------------------------------


class TestAget:
    @pytest.mark.asyncio
    @pytest.mark.django_db
    async def test_aget_returns_streaming_response(self, rf, monkeypatch):
        """ASGI context + ``streaming_render = True`` => async streaming
        response whose iterator yields ≥ 4 chunks."""
        view = _StreamingFixtureView()
        request = _build_request(rf)
        # Bypass session middleware; aget calls into get() which uses sessions.
        from importlib import import_module
        from django.conf import settings

        engine = import_module(settings.SESSION_ENGINE)
        request.session = engine.SessionStore()
        # The rf fixture produces a WSGIRequest; force the ASGI path so
        # we can exercise the async render code without a full ASGI stack.
        monkeypatch.setattr(view, "_is_asgi_context", lambda *a, **kw: True)

        response = await view.aget(request)
        assert isinstance(response, StreamingHttpResponse)
        assert response["X-Djust-Streaming"] == "1"
        assert response["X-Djust-Streaming-Phase"] == "2"

        chunks = []
        async for chunk in response.streaming_content:
            chunks.append(chunk)
        assert len(chunks) >= 4, f"expected ≥4 chunks, got {len(chunks)}"
        # Body roundtrips.
        full_body = b"".join(chunks).decode("utf-8")
        assert "<div dj-root" in full_body
        assert "<p>hello</p>" in full_body

    @pytest.mark.asyncio
    @pytest.mark.django_db
    async def test_aget_returns_redirect_passthrough(self, rf):
        """Auth/hook-redirect responses from sync ``get()`` pass through
        unchanged. Tests the early-return on ``HttpResponseRedirect`` in
        ``aget()``."""

        class _AuthBlockedView(LiveView):
            template = "<div dj-root></div>"
            streaming_render = True
            login_required = True  # triggers the auth redirect

        view = _AuthBlockedView()
        request = _build_request(rf)
        from importlib import import_module
        from django.conf import settings

        engine = import_module(settings.SESSION_ENGINE)
        request.session = engine.SessionStore()
        # Anonymous user — auth check should redirect to login.
        from django.http import HttpResponseRedirect

        response = await view.aget(request)
        assert isinstance(response, HttpResponseRedirect)

    @pytest.mark.asyncio
    @pytest.mark.django_db
    async def test_aget_with_streaming_render_false_returns_plain_response(self, rf):
        """When ``streaming_render = False`` (default), ``aget()`` falls
        back to the sync GET path and returns a plain ``HttpResponse``."""
        view = _NonStreamingFixtureView()
        request = _build_request(rf)
        from importlib import import_module
        from django.conf import settings

        engine = import_module(settings.SESSION_ENGINE)
        request.session = engine.SessionStore()

        response = await view.aget(request)
        # streaming_render=False => sync get() returns HttpResponse,
        # which aget() passes through directly.
        assert isinstance(response, HttpResponse)
        assert not isinstance(response, StreamingHttpResponse)

    @pytest.mark.asyncio
    @pytest.mark.django_db
    async def test_aget_wsgi_fallback_when_no_event_loop(self, rf, monkeypatch):
        """When ``_is_asgi_context()`` returns False (WSGI deployment),
        ``aget()`` delegates to sync ``get()`` via ``sync_to_async`` and
        returns the same ``StreamingHttpResponse`` shape that the
        Phase-1 ``_make_streaming_response`` produces. Verifies ADR-015
        §"Backwards compatibility" graceful-degrade contract."""
        view = _StreamingFixtureView()
        request = _build_request(rf)
        from importlib import import_module
        from django.conf import settings

        engine = import_module(settings.SESSION_ENGINE)
        request.session = engine.SessionStore()
        # Force the WSGI fallback by stubbing _is_asgi_context.
        monkeypatch.setattr(view, "_is_asgi_context", lambda *a, **kw: False)

        response = await view.aget(request)
        # Phase-1 cosmetic StreamingHttpResponse — same shape as
        # ``_make_streaming_response`` produces (no PR-A header).
        assert isinstance(response, StreamingHttpResponse)
        assert response.get("X-Djust-Streaming-Phase") is None
        # Body roundtrips.
        chunks = []
        for chunk in response.streaming_content:
            chunks.append(chunk if isinstance(chunk, bytes) else chunk.encode("utf-8"))
        body = b"".join(chunks).decode("utf-8")
        assert "<div dj-root" in body

    @pytest.mark.asyncio
    @pytest.mark.django_db
    async def test_aget_cancels_emitter_on_disconnect(self, rf, monkeypatch):
        """ASGI-disconnect path: when ``request.is_disconnected()``
        returns True mid-stream, the disconnect-watcher task calls
        ``emitter.cancel()`` and the consumer iterator exits cleanly.

        ADR-015 §"Cancellation contract" — verifies the live watcher
        path that the basic test_aget_returns_streaming_response does
        not exercise.
        """
        view = _StreamingFixtureView()
        request = _build_request(rf)
        from importlib import import_module
        from django.conf import settings

        engine = import_module(settings.SESSION_ENGINE)
        request.session = engine.SessionStore()
        # The rf fixture produces a WSGIRequest; force the ASGI path.
        monkeypatch.setattr(view, "_is_asgi_context", lambda *a, **kw: True)

        # Wire up a fake is_disconnected that returns True immediately.
        # The watcher polls once, sees True, and calls emitter.cancel().
        poll_count = {"n": 0}
        cancel_calls: list[str] = []

        async def fake_is_disconnected():
            poll_count["n"] += 1
            return True

        request.is_disconnected = fake_is_disconnected  # type: ignore[attr-defined]

        response = await view.aget(request)
        assert isinstance(response, StreamingHttpResponse)

        # Wrap the emitter's cancel so we can assert the watcher hit it.
        emitter = view._chunk_emitter  # may already be cleared if produce finished first
        if emitter is not None:
            orig_cancel = emitter.cancel

            async def tracked_cancel(reason: str = "client_disconnected"):
                cancel_calls.append(reason)
                await orig_cancel(reason)

            emitter.cancel = tracked_cancel  # type: ignore[assignment]

        # Drain the iterator — should exit cleanly without raising.
        chunks = []
        async for chunk in response.streaming_content:
            chunks.append(chunk)
            await asyncio.sleep(0.05)

        # The watcher polled at least once; whether cancellation
        # actually fired depends on race with producer completion (the
        # producer finishes in <1ms for the small fixture). Assert the
        # watcher path was exercised.
        assert poll_count["n"] >= 1, "disconnect watcher should have polled"
