"""
StreamsMixin - Memory-efficient collection management for LiveView.
"""

from typing import Any, Callable, Dict, Optional

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

        def default_dom_id(x):
            return getattr(x, "id", None) or getattr(x, "pk", None) or id(x)

        if dom_id is None:
            dom_id = default_dom_id

        if name not in self._streams or reset:
            self._streams[name] = Stream(name, dom_id)
            if reset:
                self._stream_operations.append(
                    {
                        "type": "stream_reset",
                        "stream": name,
                    }
                )

        stream_obj = self._streams[name]

        # Convert items to list if needed
        if hasattr(items, "__iter__") and not isinstance(items, (str, bytes)):
            items_list = list(items)
        else:
            items_list = [items] if items is not None else []

        for item in items_list:
            stream_obj.insert(item, at=at)
            self._stream_operations.append(
                {
                    "type": "stream_insert",
                    "stream": name,
                    "dom_id": f"{name}-{dom_id(item)}",
                    "at": at,
                }
            )

        return stream_obj

    def stream_insert(self, name: str, item: Any, at: int = -1) -> None:
        """Insert an item into a stream (-1 = append, 0 = prepend)."""
        if name not in self._streams:
            raise ValueError(f"Stream '{name}' not initialized. Call stream() first.")

        stream_obj = self._streams[name]
        dom_id = stream_obj.dom_id_fn

        stream_obj.insert(item, at=at)
        self._stream_operations.append(
            {
                "type": "stream_insert",
                "stream": name,
                "dom_id": f"{name}-{dom_id(item)}",
                "at": at,
            }
        )

    def stream_delete(self, name: str, item_or_id: Any) -> None:
        """Delete an item from a stream by item or id."""
        if name not in self._streams:
            raise ValueError(f"Stream '{name}' not initialized. Call stream() first.")

        stream_obj = self._streams[name]

        # Get the DOM id
        if hasattr(item_or_id, "id"):
            dom_id_val = f"{name}-{item_or_id.id}"
        elif hasattr(item_or_id, "pk"):
            dom_id_val = f"{name}-{item_or_id.pk}"
        else:
            dom_id_val = f"{name}-{item_or_id}"

        stream_obj.delete(item_or_id)
        self._stream_operations.append(
            {
                "type": "stream_delete",
                "stream": name,
                "dom_id": dom_id_val,
            }
        )

    def stream_reset(self, name: str, items: Any = None) -> None:
        """Reset a stream, clearing all items and optionally adding new ones."""
        if name in self._streams:
            self._streams[name].clear()

        self._stream_operations.append(
            {
                "type": "stream_reset",
                "stream": name,
            }
        )

        if items is not None:
            self.stream(name, items, reset=False)

    def _get_streams_context(self) -> Dict[str, list]:
        """Get streams data for template context."""
        return {name: stream_obj.items for name, stream_obj in self._streams.items()}

    def _get_stream_operations(self) -> list:
        """Get and clear pending stream operations."""
        ops = self._stream_operations.copy()
        self._stream_operations.clear()
        return ops

    def _reset_streams(self) -> None:
        """Reset all streams after render to free memory."""
        for stream_obj in self._streams.values():
            stream_obj.clear()
