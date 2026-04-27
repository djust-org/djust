"""HTTP streaming foundation for v0.9.0 Phase 2 (ADR-015).

Owns the :class:`ChunkEmitter` abstraction that
:meth:`TemplateMixin.arender_chunks` and :meth:`RequestMixin.aget` use to
ship shell-then-body chunks over a ``StreamingHttpResponse``. PR-A
foundation; PR-B (``lazy=True``) and PR-C (``asyncio.as_completed``)
build on this.

The emitter provides three responsibilities:

1. **Backpressure** — chunks are pushed onto a bounded
   :class:`asyncio.Queue`. When the queue is full, ``emit()`` awaits
   until the consumer drains a chunk.
2. **Cancellation** — a single ``cancel()`` method propagates a stop
   signal to all in-flight thunks via a per-request token plus a
   ``cancelled`` flag, used by the ASGI disconnect handler in
   :meth:`RequestMixin.aget`.
3. **Lazy thunk registry** — a placeholder API surface
   (:meth:`register_thunk`) used by PR-B's ``{% live_render lazy=True %}``
   tag. PR-A does not invoke any thunks; it only wires the registry so
   the contract is locked in advance.

The emitter is per-request: a fresh instance is constructed inside
:meth:`RequestMixin.aget` and stashed on the LiveView instance as
``self._chunk_emitter`` for the duration of that GET.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, List, Optional, Tuple

logger = logging.getLogger(__name__)


# Default upper bound for the chunk queue. PR-B may register multiple lazy
# thunks per request; queueing more than ~8 unread chunks signals a slow
# client where ``emit()`` should block instead of buffering unbounded.
DEFAULT_CHUNK_QUEUE_MAX = 8


# Sentinel passed through the queue to signal end-of-stream to the consumer.
# Bytes are never None in normal flow, so None is unambiguous.
_STREAM_END = None


def _get_queue_max_from_settings() -> int:
    """Read ``DJUST_LAZY_CHUNK_QUEUE_MAX`` from Django settings.

    Falls back to :data:`DEFAULT_CHUNK_QUEUE_MAX` when Django is not
    configured (test bootstrap edge cases) or the setting is missing.
    """
    try:
        from django.conf import settings

        value = getattr(settings, "DJUST_LAZY_CHUNK_QUEUE_MAX", DEFAULT_CHUNK_QUEUE_MAX)
        if isinstance(value, int) and value > 0:
            return value
        logger.warning(
            "DJUST_LAZY_CHUNK_QUEUE_MAX must be a positive int; got %r — "
            "falling back to default %d",
            value,
            DEFAULT_CHUNK_QUEUE_MAX,
        )
    except Exception:
        # Settings not configured (shouldn't happen in real requests, but
        # guards tests that import this module before Django is ready).
        logger.debug("Django settings not available; using default queue max")
    return DEFAULT_CHUNK_QUEUE_MAX


class ChunkEmitterCancelled(Exception):
    """Raised by :meth:`ChunkEmitter.emit` when the emitter has been
    cancelled (typically due to the client disconnecting).

    Cooperative cancellation: producers (thunks, the shell renderer)
    catch this and return cleanly without writing further chunks.
    """


class ChunkEmitter:
    """Per-request chunk-emission and cancellation coordinator.

    The emitter owns:

    - A bounded :class:`asyncio.Queue` of pre-rendered chunks (bytes).
    - An ordered registry of "thunks" — async callables that PR-B's
      ``{% live_render lazy=True %}`` tag registers for deferred render.
      PR-A does not call thunks; the registry is API surface only.
    - A ``request_token`` :class:`asyncio.Event` that thunks can await to
      detect cancellation cooperatively (PR-C will wire this).
    - A ``cancelled`` boolean flag set by :meth:`cancel`.

    The emitter is constructed per request inside
    :meth:`RequestMixin.aget`. The async iterator interface
    (``async for chunk in emitter``) is consumed by Django's
    ``StreamingHttpResponse`` via the ASGI handler.
    """

    def __init__(self, request, *, max_queue: Optional[int] = None) -> None:
        """Create a chunk emitter bound to a request.

        :param request: The :class:`HttpRequest` for the current GET.
            Stashed for thunks that need request-scoped data
            (auth, session, language).
        :param max_queue: Override the queue size. Defaults to
            :setting:`DJUST_LAZY_CHUNK_QUEUE_MAX` from settings.
        """
        self.request = request
        if max_queue is None:
            max_queue = _get_queue_max_from_settings()
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=max_queue)
        self._thunks: List[Tuple[str, Callable[..., Awaitable[bytes]]]] = []
        # ``request_token`` is ``set()`` on cancel. Thunks await
        # ``token.wait()`` (with timeout) to detect disconnect.
        self.request_token: asyncio.Event = asyncio.Event()
        self.cancelled: bool = False
        self._cancel_reason: Optional[str] = None

    # ------------------------------------------------------------------
    # Producer side — called by arender_chunks() and (in PR-B) thunks.
    # ------------------------------------------------------------------

    async def emit(self, chunk: bytes) -> None:
        """Push a rendered chunk onto the queue.

        Awaits when the queue is full (backpressure). Raises
        :class:`ChunkEmitterCancelled` if the emitter was cancelled
        (e.g. the client disconnected) — producers should catch this
        and return cleanly.

        :param chunk: Pre-rendered HTML bytes to ship to the client.
        """
        if self.cancelled:
            raise ChunkEmitterCancelled(self._cancel_reason or "emitter_cancelled")
        if not isinstance(chunk, (bytes, bytearray)):
            # Defensive: producers occasionally hand strings; normalize so
            # the StreamingHttpResponse never sees a mixed iterator.
            chunk = chunk.encode("utf-8")
        await self._queue.put(chunk)

    def register_thunk(self, view_id: str, thunk_fn: Callable[..., Awaitable[bytes]]) -> None:
        """Register a lazy thunk to be flushed after the parent shell.

        PR-A defines this as API surface only — no caller in the
        framework invokes thunks yet. PR-B's
        ``{% live_render ... lazy=True %}`` tag will register one thunk
        per lazy slot here.

        :param view_id: Stable identifier for the lazy slot
            (matches ``<dj-lazy-slot data-id=...>``).
        :param thunk_fn: Async callable returning the rendered chunk
            bytes for the slot's filled content.
        """
        self._thunks.append((view_id, thunk_fn))

    @property
    def thunks(self) -> List[Tuple[str, Callable[..., Awaitable[bytes]]]]:
        """Read-only view of registered thunks (for testing/PR-B wiring)."""
        return list(self._thunks)

    async def close(self) -> None:
        """Signal end-of-stream to the consumer.

        Called by :meth:`RequestMixin.aget` after the final chunk has
        been pushed. The async iterator exits cleanly when it sees the
        sentinel.
        """
        await self._queue.put(_STREAM_END)

    # ------------------------------------------------------------------
    # Cancellation.
    # ------------------------------------------------------------------

    async def cancel(self, reason: str = "client_disconnected") -> None:
        """Cancel the emitter; subsequent ``emit()`` calls raise.

        Sets the ``request_token`` event so thunks awaiting it wake up
        and can return cleanly. Drains the queue and pushes the
        end-of-stream sentinel so the consumer iterator exits.

        :param reason: Short label for the cancellation cause.
            Logged for debugging; passed through to
            :class:`ChunkEmitterCancelled`.
        """
        if self.cancelled:
            return
        self.cancelled = True
        self._cancel_reason = reason
        # Wake up any thunks awaiting the request token.
        self.request_token.set()
        logger.debug("ChunkEmitter cancelled: %s", reason)
        # Drain pending chunks so the consumer iterator can exit on the
        # sentinel rather than trying to flush stale buffers.
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        # Push the end-of-stream sentinel to unblock a consumer that's
        # currently awaiting on ``self._queue.get()``.
        try:
            self._queue.put_nowait(_STREAM_END)
        except asyncio.QueueFull:  # pragma: no cover — drained above
            pass

    # ------------------------------------------------------------------
    # Consumer side — called by StreamingHttpResponse via ASGI.
    # ------------------------------------------------------------------

    def __aiter__(self):
        return self._aiter_impl()

    async def _aiter_impl(self):
        """Yield queued chunks until the end-of-stream sentinel arrives.

        ASGI's StreamingHttpResponse iterates this generator and ships
        each yielded ``bytes`` to the client. On cancellation the
        producer pushes the sentinel via :meth:`cancel`, which exits
        this loop without raising.
        """
        while True:
            chunk = await self._queue.get()
            if chunk is _STREAM_END:
                return
            yield chunk
