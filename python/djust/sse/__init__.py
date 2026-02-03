"""
SSE (Server-Sent Events) module for djust.

SSE provides a simpler alternative to WebSocket for server-to-client streaming.
It's ideal for:
- Dashboards with live updates
- Notification feeds
- Progress indicators
- Apps where the client rarely sends data

Unlike WebSocket, SSE:
- Works over standard HTTP (no special server setup)
- Doesn't require Django Channels
- Has built-in reconnection and Last-Event-ID support
- Is unidirectional (server → client only)

Usage:
    from djust.sse import SSEView, SSEMixin

    class DashboardSSE(SSEView):
        async def stream(self, request):
            while True:
                data = await self.get_updates()
                yield self.event('update', data)
                await asyncio.sleep(1)
"""

from .views import SSEView, SSEMixin, sse_event, sse_comment

__all__ = [
    "SSEView",
    "SSEMixin",
    "sse_event",
    "sse_comment",
]
