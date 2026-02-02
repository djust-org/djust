"""
Streaming support for LiveView — real-time partial DOM updates.

Enables token-by-token streaming (e.g., LLM chat responses) by sending
incremental DOM updates over WebSocket without full re-renders.

Usage:
    class ChatView(LiveView):
        template_name = 'chat.html'

        async def mount(self, request, **kwargs):
            self.messages = []

        @event_handler
        async def send_message(self, content, **kwargs):
            self.messages.append({"role": "user", "content": content})
            self.messages.append({"role": "assistant", "content": ""})

            async for token in llm_stream(content):
                self.messages[-1]["content"] += token
                await self.stream_to("messages", target="#message-list")
"""

import asyncio
import logging
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# Minimum interval between stream updates (~60fps)
MIN_STREAM_INTERVAL_S = 1.0 / 60  # ~16ms


class StreamingMixin:
    """
    Mixin that provides streaming capabilities for LiveView.

    Requires the view to have a `_ws_consumer` reference set by the WebSocket
    consumer during mount. This allows the view to send messages directly
    during async handler execution (not just after handler returns).
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._ws_consumer = None  # Set by LiveViewConsumer
        self._stream_batch: Dict[str, List[dict]] = {}  # Pending ops by stream name
        self._last_stream_time: float = 0.0
        self._stream_flush_task: Optional[asyncio.Task] = None

    async def stream_to(
        self,
        stream_name: str,
        target: Optional[str] = None,
        html: Optional[str] = None,
    ) -> None:
        """
        Send a streaming partial update to the client.

        If `html` is provided, sends it directly. Otherwise, re-renders only
        the target element from the current template context.

        This batches rapid updates to ~60fps max.

        Args:
            stream_name: Logical name for the stream (e.g., "messages")
            target: CSS selector for the container to update (e.g., "#message-list")
            html: Optional pre-rendered HTML fragment to send directly
        """
        if not self._ws_consumer:
            logger.warning("stream_to() called but no WebSocket consumer attached")
            return

        now = time.monotonic()
        elapsed = now - self._last_stream_time

        # Build the operation
        op = {
            "op": "replace",
            "target": target or f"[dj-stream='{stream_name}']",
        }

        if html is not None:
            op["html"] = html
        else:
            # Re-render the target fragment from current state
            op["html"] = await self._render_stream_fragment(stream_name, target)

        # Batch or send immediately
        if elapsed >= MIN_STREAM_INTERVAL_S:
            # Send immediately
            await self._send_stream_ops(stream_name, [op])
            self._last_stream_time = now
        else:
            # Batch: queue the op and schedule a flush
            if stream_name not in self._stream_batch:
                self._stream_batch[stream_name] = []
            self._stream_batch[stream_name] = [op]  # Replace: only latest matters

            if not self._stream_flush_task or self._stream_flush_task.done():
                delay = MIN_STREAM_INTERVAL_S - elapsed
                self._stream_flush_task = asyncio.ensure_future(
                    self._flush_stream_batch(delay)
                )

    async def stream_insert(
        self,
        stream_name: str,
        html: str,
        at: str = "append",
        target: Optional[str] = None,
    ) -> None:
        """
        Insert HTML into a stream container.

        Args:
            stream_name: Logical name for the stream
            html: HTML fragment to insert
            at: "append" or "prepend"
            target: CSS selector (defaults to [dj-stream='name'])
        """
        if not self._ws_consumer:
            return

        op = {
            "op": at,  # "append" or "prepend"
            "target": target or f"[dj-stream='{stream_name}']",
            "html": html,
        }
        await self._send_stream_ops(stream_name, [op])

    async def stream_delete(
        self,
        stream_name: str,
        selector: str,
    ) -> None:
        """
        Remove an element from the DOM.

        Args:
            stream_name: Logical name for the stream
            selector: CSS selector of element to remove
        """
        if not self._ws_consumer:
            return

        op = {
            "op": "delete",
            "target": selector,
        }
        await self._send_stream_ops(stream_name, [op])

    async def push_state(self) -> None:
        """
        Send current state to the client immediately (full re-render).

        Useful for long-running async operations that want to show
        intermediate state (e.g., "Analyzing..." → "Done").
        """
        if not self._ws_consumer:
            logger.warning("push_state() called but no WebSocket consumer attached")
            return

        from asgiref.sync import sync_to_async
        import json

        # Re-render the full view
        html, patches, version = await sync_to_async(self.render_with_diff)()

        if patches is not None:
            patch_list = json.loads(patches) if patches else []
            await self._ws_consumer.send_json({
                "type": "patch",
                "patches": patch_list,
                "version": version,
            })
        else:
            html = await sync_to_async(self._strip_comments_and_whitespace)(html)
            html = await sync_to_async(self._extract_liveview_content)(html)
            await self._ws_consumer.send_json({
                "type": "html_update",
                "html": html,
                "version": version,
            })
        await self._ws_consumer._flush_push_events()

    async def _render_stream_fragment(
        self, stream_name: str, target: Optional[str] = None
    ) -> str:
        """
        Render a fragment of the template for the stream target.

        For simplicity, re-renders the full template and extracts the target element.
        A future optimization could use partial template rendering.
        """
        from asgiref.sync import sync_to_async
        from django.template.loader import render_to_string

        context = await sync_to_async(self.get_context_data)()

        if self.template_name:
            html = await sync_to_async(render_to_string)(self.template_name, context)
        elif self.template:
            from django.template import Template, Context
            tmpl = Template(self.template)
            html = await sync_to_async(tmpl.render)(Context(context))
        else:
            return ""

        # Extract the target element's innerHTML
        selector = target or f"[dj-stream='{stream_name}']"
        return self._extract_element_html(html, selector)

    @staticmethod
    def _extract_element_html(html: str, selector: str) -> str:
        """
        Extract innerHTML of an element matching a CSS selector from HTML string.

        Uses a simple approach: find the element by id or attribute and extract content.
        """
        import re

        # Handle #id selectors
        if selector.startswith("#"):
            element_id = selector[1:]
            # Match <tag id="element_id" ...>content</tag>
            pattern = rf'<(\w+)[^>]*\bid=["\']?{re.escape(element_id)}["\']?[^>]*>(.*?)</\1>'
            match = re.search(pattern, html, re.DOTALL)
            if match:
                return match.group(2)

        # Handle [attr='value'] selectors
        attr_match = re.match(r"\[(\w[\w-]*)=['\"]?([^'\"]+)['\"]?\]", selector)
        if attr_match:
            attr_name, attr_value = attr_match.groups()
            pattern = rf'<(\w+)[^>]*\b{re.escape(attr_name)}=["\']?{re.escape(attr_value)}["\']?[^>]*>(.*?)</\1>'
            match = re.search(pattern, html, re.DOTALL)
            if match:
                return match.group(2)

        # Fallback: return full HTML
        return html

    async def _send_stream_ops(self, stream_name: str, ops: List[dict]) -> None:
        """Send stream operations over WebSocket."""
        if not self._ws_consumer:
            return

        await self._ws_consumer.send_json({
            "type": "stream",
            "stream": stream_name,
            "ops": ops,
        })

    async def _flush_stream_batch(self, delay: float) -> None:
        """Flush batched stream operations after a delay."""
        await asyncio.sleep(delay)

        for stream_name, ops in self._stream_batch.items():
            if ops:
                await self._send_stream_ops(stream_name, ops)

        self._stream_batch.clear()
        self._last_stream_time = time.monotonic()
