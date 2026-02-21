"""
Type stubs for StreamingMixin.

These stubs provide type hints for async streaming methods that are used
at runtime but may not be fully discoverable by static analysis tools.
"""

from typing import Optional

class StreamingMixin:
    """Mixin that provides streaming capabilities for LiveView."""

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
        ...

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
        ...

    async def stream_text(
        self,
        stream_name: str,
        text: str,
        mode: str = "append",
        target: Optional[str] = None,
    ) -> None:
        """
        Stream text content to a target element.

        Args:
            stream_name: Logical name for the stream
            text: Text content to stream
            mode: "append", "replace", or "prepend"
            target: CSS selector (defaults to [dj-stream='name'])
        """
        ...

    async def stream_error(
        self,
        stream_name: str,
        error: str,
        target: Optional[str] = None,
    ) -> None:
        """
        Send an error state to a stream target, preserving partial content.

        Args:
            stream_name: Logical name for the stream
            error: Error message to display
            target: CSS selector (defaults to [dj-stream='name'])
        """
        ...

    async def stream_start(
        self,
        stream_name: str,
        target: Optional[str] = None,
    ) -> None:
        """
        Signal the start of a stream to the client.

        Args:
            stream_name: Logical name for the stream
            target: CSS selector (defaults to [dj-stream='name'])
        """
        ...

    async def stream_done(
        self,
        stream_name: str,
        target: Optional[str] = None,
    ) -> None:
        """
        Signal the end of a stream to the client.

        Args:
            stream_name: Logical name for the stream
            target: CSS selector (defaults to [dj-stream='name'])
        """
        ...

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
        ...

    async def push_state(self) -> None:
        """
        Send current state to the client immediately (full re-render).

        Useful for long-running async operations that want to show
        intermediate state (e.g., "Analyzing..." → "Done").
        """
        ...
