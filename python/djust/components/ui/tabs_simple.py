"""
Tabs component for djust.

Simple stateless tabs with automatic Rust optimization.
"""

from typing import List, Dict, Optional
from ..base import Component
import importlib.util

_RUST_AVAILABLE = importlib.util.find_spec("djust._rust") is not None


class Tabs(Component):
    """
    Tabs navigation component (Bootstrap 5).

    Features:
    - Automatic Rust optimization (~1μs rendering)
    - Template fallback if Rust unavailable
    - Python fallback with f-strings

    Args:
        tabs: List of tab dicts with 'title' and 'content' keys
        active_index: Index of initially active tab (default: 0)
        style: Tab style ('tabs' or 'pills')
        id: Tabs ID for JavaScript control (auto-generated if not provided)

    Example:
        >>> tabs = Tabs(tabs=[
        ...     {'title': 'Home', 'content': '<p>Home content</p>'},
        ...     {'title': 'Profile', 'content': '<p>Profile content</p>'},
        ...     {'title': 'Contact', 'content': '<p>Contact form</p>'},
        ... ])
        >>> tabs.render()
        '<nav><div class="nav nav-tabs">...'
    """

    # Note: Not using template because Rust template engine doesn't support forloop.counter0
    # Using Python fallback which is still fast (~50-100μs)

    def __init__(
        self,
        tabs: List[Dict[str, str]],
        id: Optional[str] = None,
        active_index: int = 0,
        style: str = "tabs",
    ):
        # Pass kwargs to parent to create Rust instance (note: not yet implemented in Rust)
        super().__init__(
            tabs=tabs,
            id=id,
            active_index=active_index,
            style=style,
        )

        # Set instance attributes for Python/hybrid rendering
        self.tabs = tabs
        self.active_index = active_index
        self.style = style

    def get_context_data(self):
        """Return context for template rendering."""
        return {
            "tabs": self.tabs,
            "id": self.id,
            "active_index": self.active_index,
            "style": self.style,
        }

    def _render_custom(self) -> str:
        """Pure Python fallback (f-string rendering)."""
        nav_class = f"nav nav-{self.style}"

        # Build tab buttons
        buttons = []
        for i, tab in enumerate(self.tabs):
            active = " active" if i == self.active_index else ""
            aria_selected = "true" if i == self.active_index else "false"

            button = f"""        <button class="nav-link{active}"
                id="{self.id}-tab-{i}"
                data-bs-toggle="tab"
                data-bs-target="#{self.id}-pane-{i}"
                type="button"
                role="tab"
                aria-controls="{self.id}-pane-{i}"
                aria-selected="{aria_selected}">
            {tab["title"]}
        </button>"""
            buttons.append(button)

        # Build tab panes
        panes = []
        for i, tab in enumerate(self.tabs):
            active_class = " show active" if i == self.active_index else ""

            pane = f"""    <div class="tab-pane fade{active_class}"
         id="{self.id}-pane-{i}"
         role="tabpanel"
         aria-labelledby="{self.id}-tab-{i}"
         tabindex="0">
        {tab["content"]}
    </div>"""
            panes.append(pane)

        return f"""<nav>
    <div class="{nav_class}" id="{self.id}" role="tablist">
{chr(10).join(buttons)}
    </div>
</nav>
<div class="tab-content" id="{self.id}-content">
{chr(10).join(panes)}
</div>"""
