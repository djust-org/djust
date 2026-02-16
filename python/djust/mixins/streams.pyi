"""
Type stubs for StreamsMixin.

These stubs provide type hints for methods that are used at runtime
but may not be fully discoverable by static analysis tools.
"""

from typing import Any, Callable, Optional

from ..session_utils import Stream

class StreamsMixin:
    """Methods for managing streams: stream, stream_insert, stream_delete, stream_reset."""

    def stream(
        self,
        name: str,
        items: Any,
        dom_id: Optional[Callable[[Any], str]] = None,
        at: int = -1,
        reset: bool = False,
    ) -> Stream:
        """
        Initialize or update a stream with items.

        Streams are memory-efficient collections that are automatically cleared
        after each render. The client preserves existing DOM elements.

        Args:
            name: Stream name (used in template as streams.{name})
            items: Iterable of items to add to the stream
            dom_id: Function to generate DOM id from item (default: lambda x: x.id)
            at: Position to insert (-1 = end, 0 = beginning)
            reset: If True, clear existing items first

        Returns:
            Stream object for chaining
        """
        ...

    def stream_insert(self, name: str, item: Any, at: int = -1) -> None:
        """
        Insert an item into a stream.

        Args:
            name: Stream name
            item: Item to insert
            at: Position to insert (-1 = append, 0 = prepend)
        """
        ...

    def stream_delete(self, name: str, item_or_id: Any) -> None:
        """
        Delete an item from a stream by item or id.

        Args:
            name: Stream name
            item_or_id: Item object with .id/.pk attribute, or the id value
        """
        ...

    def stream_reset(self, name: str, items: Any = None) -> None:
        """
        Reset a stream, clearing all items and optionally adding new ones.

        Args:
            name: Stream name
            items: Optional new items to add after reset
        """
        ...
