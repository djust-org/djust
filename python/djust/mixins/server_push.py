"""
ServerPushMixin — a push from the view's own code, on any live transport (#3809).

``push_to_view()`` reaches a view from OUTSIDE (a Celery task, a management
command) through the channel layer. A view that follows a long-running job
with its own ``asyncio`` task has no use for a broadcast: it wants to say
"call this handler, then render and send", on the connection it is mounted on::

    class JobView(LiveView):
        def mount(self, request, **kwargs):
            self.step = "queued"
            self.can_follow = self.live_transport is not None  # true in mount()

        @event_handler()
        async def start_job(self, **kwargs):
            self._follower = asyncio.get_running_loop().create_task(self._follow())

        async def _follow(self):
            async for step in job_steps():
                await self.server_push("handle_step", {"step": step})

        def handle_step(self, step="", **kwargs):
            self.step = step

The push runs the same turn on a WebSocket and on an SSE session: the turn
chain (so ``LIVEVIEW_CONFIG["sync_turn_wrapper"]`` and ``reauth_on_event``
apply), the connection's render lock, deferral while a turn is in progress,
one render and one frame. See ``docs/website/advanced/server-push.md``.
"""

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class ServerPushMixin:
    @property
    def live_transport(self) -> Optional[str]:
        """The live transport this view is attached to: ``"websocket"``,
        ``"sse"``, or ``None``.

        ``None`` on the first HTTP render, on the fresh instance an HTTP
        fallback POST builds, and in ``LiveViewTestClient``. It is already
        set when ``mount()`` runs on a WebSocket or an SSE session (the
        transport stamps it before ``mount()``), so ``mount()`` can decide
        whether to start background followers.

        The name is reserved: it is a read-only property, so assigning
        ``self.live_transport = ...`` in a view raises ``AttributeError``. A
        class-level attribute of that name in a subclass shadows it.
        """
        state = self.__dict__
        if state.get("_ws_consumer") is not None:
            return "websocket"
        if state.get("_sse_session") is not None:
            return "sse"
        return None

    async def server_push(self, handler: str, payload: Optional[Dict[str, Any]] = None) -> bool:
        """Call ``handler(**payload)`` on this view, then render and send, as a
        server-originated turn on the transport the view is mounted on.

        The turn is the one ``push_to_view()`` runs on the WebSocket: the
        handler runs under the connection's render lock; a push that finds the
        connection busy (an event, a background result) is queued and replayed
        in order, identical queued pushes coalescing; the page is re-rendered
        once and sent as a ``source="broadcast"`` frame.

        ``handler`` is checked as ``push_to_view()`` checks it: it must start
        with ``handle_``, be decorated ``@event_handler``, or be marked
        ``@push_handler``; any other name is refused with a warning and never
        called. ``payload`` is the handler's keyword arguments. Both come from
        the server's own code: nothing a browser sends reaches this method.

        Call it from the view's event loop (an ``async`` handler, or a task
        created from one).

        Returns ``True`` when the push ran or was queued. It returns ``False``
        and does nothing when the handler is refused (a warning) or missing, and
        (a debug log, no exception) when there is nobody to push to: on the HTTP render and on a fallback POST's fresh instance,
        in ``LiveViewTestClient``, after the view was closed or replaced by a
        ``live_redirect``, and before ``mount()`` has finished (a push issued
        from inside ``mount()`` itself; start a task there, as above).

        Over SSE, ``push_to_view()`` groups, push scopes and ``db_notify``
        stay WebSocket-only; this method does not need them.
        """
        if not isinstance(handler, str) or not handler:
            raise TypeError("server_push() needs a handler name")
        if payload is not None and not isinstance(payload, dict):
            raise TypeError("server_push() payload must be a dict of keyword arguments")
        event: Dict[str, Any] = {"handler": handler, "payload": dict(payload or {})}

        # Refuse here what the turn would refuse, so the caller can tell a
        # refusal (False, and a warning from the check) from a run.
        from ..websocket import LiveViewConsumer

        if LiveViewConsumer._prepare_server_push(self, event) is None:
            logger.debug(
                "[djust] server_push on %s refused — %r is not a push target",
                type(self).__name__,
                handler,
            )
            return False

        state = self.__dict__
        consumer = state.get("_ws_consumer")
        if consumer is not None:
            if getattr(consumer, "view_instance", None) is not self:
                logger.debug(
                    "[djust] server_push on %s dropped — not the mounted view",
                    type(self).__name__,
                )
                return False
            # A view mounted beside the page view runs on a stand-in: the turn
            # is the real consumer's code, with the stand-in as ``self``.
            real = getattr(consumer, "_slot_consumer", consumer)
            await type(real)._server_push_to_view(consumer, event)
            return True

        session = state.get("_sse_session")
        runtime = getattr(session, "runtime", None)
        if runtime is not None:
            if await runtime.server_push(self, event):
                return True
            logger.debug(
                "[djust] server_push on %s dropped — not the mounted view", type(self).__name__
            )
            return False

        logger.debug("[djust] server_push on %s ignored — no live transport", type(self).__name__)
        return False
