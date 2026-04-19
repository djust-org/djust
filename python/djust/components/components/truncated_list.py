"""TruncatedList component for showing N items with +X more overflow."""

import html
from typing import List, Optional

from djust import Component


class TruncatedList(Component):
    """Style-agnostic truncated list component.

    Displays the first N items from a list with a "+X more" overflow indicator.
    Clicking the overflow expands to show all items.

    Usage in a LiveView::

        self.assignees = TruncatedList(
            items=["Alice", "Bob", "Charlie", "Dave", "Eve"],
            max=3,
        )

        # With custom overflow label
        self.tags = TruncatedList(
            items=tag_list,
            max=5,
            overflow_label="{count} hidden",
        )

    In template::

        {{ assignees|safe }}

    CSS Custom Properties::

        --dj-truncated-list-gap: gap between items (default: 0.25rem)
        --dj-truncated-list-overflow-color: overflow badge color
        --dj-truncated-list-overflow-bg: overflow badge background
        --dj-truncated-list-overflow-radius: overflow badge radius (default: 9999px)
        --dj-truncated-list-overflow-padding: overflow badge padding

    Args:
        items: List of items (strings or dicts with 'label' key)
        max: Maximum items to show before overflow (default: 3)
        expanded: Whether list is currently expanded (default: False)
        toggle_event: djust event to toggle expanded state
        overflow_label: Custom overflow label; {count} is replaced with hidden count
        custom_class: Additional CSS classes
    """

    def __init__(
        self,
        items: Optional[List] = None,
        max: int = 3,
        expanded: bool = False,
        toggle_event: str = "toggle_list",
        overflow_label: str = "+{count} more",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            items=items,
            max=max,
            expanded=expanded,
            toggle_event=toggle_event,
            overflow_label=overflow_label,
            custom_class=custom_class,
            **kwargs,
        )
        self.items = items or []
        self.max = max
        self.expanded = expanded
        self.toggle_event = toggle_event
        self.overflow_label = overflow_label
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        cls = "dj-truncated-list"
        if self.expanded:
            cls += " dj-truncated-list--expanded"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"

        max_items = int(self.max)
        all_items = self.items
        total = len(all_items)
        visible = all_items if self.expanded else all_items[:max_items]
        hidden_count = max(0, total - max_items)

        items_html = []
        for item in visible:
            if isinstance(item, dict):
                label = html.escape(str(item.get("label", item.get("name", ""))))
            else:
                label = html.escape(str(item))
            items_html.append(f'<span class="dj-truncated-list__item">{label}</span>')

        content = "".join(items_html)

        overflow_html = ""
        if hidden_count > 0:
            e_event = html.escape(self.toggle_event)
            if self.expanded:
                overflow_text = html.escape("Show less")
            else:
                overflow_text = html.escape(
                    self.overflow_label.replace("{count}", str(hidden_count))
                )
            overflow_html = (
                f'<button class="dj-truncated-list__overflow" dj-click="{e_event}">'
                f"{overflow_text}</button>"
            )

        return f'<div class="{cls}" role="list">{content}{overflow_html}</div>'
