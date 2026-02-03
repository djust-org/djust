"""
SSE (Server-Sent Events) Views for djust.

Provides SSEView base class and SSEMixin for streaming server-to-client events
without requiring WebSocket or Django Channels.
"""

import asyncio
import json
import logging
import time
from typing import Any, AsyncGenerator, Dict, Optional, Union

from django.http import HttpRequest, StreamingHttpResponse
from django.views import View

logger = logging.getLogger(__name__)


def sse_event(
    data: Any,
    event: Optional[str] = None,
    id: Optional[str] = None,
    retry: Optional[int] = None,
) -> str:
    """
    Format data as an SSE event string.

    Args:
        data: Event data (will be JSON encoded if not a string)
        event: Event type/name (optional)
        id: Event ID for Last-Event-ID tracking (optional)
        retry: Reconnection time in milliseconds (optional)

    Returns:
        Formatted SSE event string
    """
    lines = []

    if id is not None:
        lines.append(f"id: {id}")

    if event is not None:
        lines.append(f"event: {event}")

    if retry is not None:
        lines.append(f"retry: {retry}")

    # Encode data as JSON if not already a string
    if not isinstance(data, str):
        data = json.dumps(data, default=str)

    # SSE data field - split multiline data
    for line in data.split("\n"):
        lines.append(f"data: {line}")

    # Double newline terminates the event
    return "\n".join(lines) + "\n\n"


def sse_comment(comment: str) -> str:
    """
    Format a comment (keep-alive) message.

    Args:
        comment: Comment text

    Returns:
        Formatted SSE comment string
    """
    lines = [f": {line}" for line in comment.split("\n")]
    return "\n".join(lines) + "\n\n"


class SSEMixin:
    """
    Mixin to add SSE capabilities to any Django view.

    Provides helper methods for creating SSE responses and streaming events.
    Can be used alongside regular HTTP handlers for actions.

    Usage:
        class DashboardView(SSEMixin, View):
            def get(self, request):
                # Regular HTTP response
                return render(request, 'dashboard.html')

            def get_sse(self, request):
                # SSE endpoint - call via separate URL
                return self.sse_response(self.stream_updates(request))

            async def stream_updates(self, request):
                while True:
                    data = await self.get_dashboard_data()
                    yield self.event('update', data)
                    await asyncio.sleep(5)
    """

    # Default retry interval in milliseconds (client will reconnect after this)
    sse_retry_ms: int = 3000

    # Keepalive interval in seconds (send comment to prevent timeout)
    sse_keepalive_interval: int = 15

    # Whether to include CORS headers
    sse_cors: bool = False

    # Allowed origins for CORS (None = allow all)
    sse_cors_origins: Optional[list] = None

    def event(
        self,
        event_type: str,
        data: Any,
        id: Optional[str] = None,
    ) -> str:
        """
        Create an SSE event.

        Args:
            event_type: Event type/name
            data: Event data (will be JSON encoded)
            id: Optional event ID for resumption

        Returns:
            Formatted SSE event string
        """
        return sse_event(data, event=event_type, id=id)

    def data_event(self, data: Any, id: Optional[str] = None) -> str:
        """
        Create an SSE data event (no event type).

        Args:
            data: Event data (will be JSON encoded)
            id: Optional event ID

        Returns:
            Formatted SSE event string
        """
        return sse_event(data, id=id)

    def keepalive(self) -> str:
        """
        Create a keepalive comment.

        Returns:
            SSE comment string
        """
        return sse_comment("keepalive")

    def sse_response(
        self,
        generator: AsyncGenerator[str, None],
        last_event_id: Optional[str] = None,
    ) -> StreamingHttpResponse:
        """
        Create an SSE StreamingHttpResponse.

        Args:
            generator: Async generator yielding SSE event strings
            last_event_id: Last-Event-ID from client (for resumption)

        Returns:
            StreamingHttpResponse configured for SSE
        """
        response = StreamingHttpResponse(
            self._wrap_generator(generator),
            content_type="text/event-stream",
        )

        # SSE-specific headers
        response["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response["X-Accel-Buffering"] = "no"  # Disable nginx buffering
        response["Connection"] = "keep-alive"

        # CORS headers if enabled
        if self.sse_cors:
            origin = "*"
            if self.sse_cors_origins:
                request_origin = getattr(self, "request", None)
                if request_origin:
                    req_origin = request_origin.headers.get("Origin", "")
                    if req_origin in self.sse_cors_origins:
                        origin = req_origin
            response["Access-Control-Allow-Origin"] = origin
            response["Access-Control-Allow-Credentials"] = "true"

        return response

    async def _wrap_generator(
        self,
        generator: AsyncGenerator[str, None],
    ) -> AsyncGenerator[bytes, None]:
        """
        Wrap the event generator with retry header and keepalive.

        Yields bytes for StreamingHttpResponse.
        """
        # Send initial retry interval
        yield sse_event({"connected": True}, event="connect", retry=self.sse_retry_ms).encode("utf-8")

        last_event_time = time.time()

        try:
            async for event_str in generator:
                yield event_str.encode("utf-8")
                last_event_time = time.time()

                # Check if we need keepalive (handled by subclass usually)
        except asyncio.CancelledError:
            logger.debug("[SSE] Connection cancelled by client")
            raise
        except GeneratorExit:
            logger.debug("[SSE] Generator closed")
            raise
        except Exception as e:
            logger.error(f"[SSE] Error in event generator: {e}")
            # Send error event before closing
            yield sse_event({"error": str(e)}, event="error").encode("utf-8")
            raise

    def get_last_event_id(self, request: HttpRequest) -> Optional[str]:
        """
        Get the Last-Event-ID from the request.

        Checks both the header and query parameter.

        Args:
            request: HTTP request

        Returns:
            Last event ID or None
        """
        # Check header first (standard)
        last_id = request.headers.get("Last-Event-ID")
        if last_id:
            return last_id

        # Fallback to query parameter (for EventSource polyfills)
        return request.GET.get("lastEventId")


class SSEView(SSEMixin, View):
    """
    Base class for SSE-only views.

    Override the `stream` method to yield events.

    Usage:
        class NotificationSSE(SSEView):
            async def stream(self, request):
                user = request.user
                last_id = self.get_last_event_id(request)

                # Resume from last event if reconnecting
                if last_id:
                    missed = await self.get_missed_notifications(user, last_id)
                    for notif in missed:
                        yield self.event('notification', notif, id=str(notif.id))

                # Stream new notifications
                async for notif in self.listen_notifications(user):
                    yield self.event('notification', notif, id=str(notif.id))

            async def get_missed_notifications(self, user, since_id):
                return await sync_to_async(list)(
                    Notification.objects.filter(
                        user=user, id__gt=since_id
                    ).order_by('id')
                )

            async def listen_notifications(self, user):
                # Your notification listening logic
                ...
    """

    # Auto-generate event IDs (incrementing counter)
    auto_id: bool = True

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._event_counter = 0

    async def get(self, request: HttpRequest, *args, **kwargs) -> StreamingHttpResponse:
        """
        Handle GET request - start SSE stream.
        """
        self.request = request
        self.args = args
        self.kwargs = kwargs

        last_event_id = self.get_last_event_id(request)

        # Call mount hook
        await self.mount(request, last_event_id, *args, **kwargs)

        return self.sse_response(
            self._stream_with_keepalive(request, last_event_id),
            last_event_id=last_event_id,
        )

    async def mount(
        self,
        request: HttpRequest,
        last_event_id: Optional[str],
        *args,
        **kwargs,
    ) -> None:
        """
        Called before streaming begins. Override to initialize state.

        Args:
            request: HTTP request
            last_event_id: Last-Event-ID for resumption (may be None)
        """
        pass

    async def stream(
        self,
        request: HttpRequest,
    ) -> AsyncGenerator[str, None]:
        """
        Override this method to yield SSE events.

        Args:
            request: HTTP request

        Yields:
            SSE event strings (use self.event() to create them)
        """
        # Default implementation - override in subclass
        yield self.event("ping", {"message": "Override stream() method"})

    async def _stream_with_keepalive(
        self,
        request: HttpRequest,
        last_event_id: Optional[str],
    ) -> AsyncGenerator[str, None]:
        """
        Wrap stream() with automatic keepalive messages.
        """
        last_event_time = time.time()

        async def keepalive_generator():
            nonlocal last_event_time
            while True:
                await asyncio.sleep(self.sse_keepalive_interval)
                if time.time() - last_event_time >= self.sse_keepalive_interval:
                    yield self.keepalive()
                    last_event_time = time.time()

        # Create keepalive task
        keepalive_task = None

        try:
            async for event_str in self.stream(request):
                last_event_time = time.time()
                yield event_str
        finally:
            if keepalive_task:
                keepalive_task.cancel()

    def next_id(self) -> str:
        """
        Generate the next auto-incrementing event ID.

        Returns:
            Event ID string
        """
        self._event_counter += 1
        return str(self._event_counter)

    def event(
        self,
        event_type: str,
        data: Any,
        id: Optional[str] = None,
    ) -> str:
        """
        Create an SSE event with optional auto-ID.

        Args:
            event_type: Event type/name
            data: Event data
            id: Event ID (auto-generated if auto_id=True and not provided)

        Returns:
            Formatted SSE event string
        """
        if id is None and self.auto_id:
            id = self.next_id()
        return sse_event(data, event=event_type, id=id)

    # =========================================================================
    # LiveView-style helpers
    # =========================================================================

    def push_patch(self, patches: list) -> str:
        """
        Send DOM patches (compatible with djust DOM patching).

        Args:
            patches: List of patch operations

        Returns:
            SSE event string
        """
        return self.event("patch", {"patches": patches})

    def push_html(self, selector: str, html: str, mode: str = "replace") -> str:
        """
        Send HTML update for a specific selector.

        Args:
            selector: CSS selector for target element
            html: HTML content
            mode: Update mode ('replace', 'append', 'prepend')

        Returns:
            SSE event string
        """
        return self.event("html", {
            "selector": selector,
            "html": html,
            "mode": mode,
        })

    def push_text(self, selector: str, text: str, mode: str = "replace") -> str:
        """
        Send text update for a specific selector.

        Args:
            selector: CSS selector for target element
            text: Text content
            mode: Update mode ('replace', 'append', 'prepend')

        Returns:
            SSE event string
        """
        return self.event("text", {
            "selector": selector,
            "text": text,
            "mode": mode,
        })

    def push_redirect(self, url: str) -> str:
        """
        Tell client to navigate to a URL.

        Args:
            url: URL to navigate to

        Returns:
            SSE event string
        """
        return self.event("redirect", {"url": url})

    def push_reload(self) -> str:
        """
        Tell client to reload the page.

        Returns:
            SSE event string
        """
        return self.event("reload", {})
