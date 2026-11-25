"""
Simple Divider component for djust - stateless.

Provides horizontal rule (hr) dividers with optional text labels.

One renderer (#4002): ``_render_custom`` draws the Bootstrap 5 markup the retired Rust
``RustDivider`` drew, pinned byte for byte by ``python/tests/test_ui_simple_golden_4002.py``.
"""

from typing import Optional

from ..base import Component

from djust.components.utils import attr_text

_MARGIN_CLASS = {"sm": "my-2", "md": "my-3", "lg": "my-4"}
_STYLE_CLASS = {"dashed": " border-dashed", "dotted": " border-dotted"}


class Divider(Component):
    """
    Simple, stateless divider component (Bootstrap 5).

    Use Cases:
        - Section dividers
        - Dividers with centered text labels (e.g., "OR", "AND")
        - Different line styles (solid, dashed, dotted)

    Args:
        text: Optional text to display in center of divider (escaped, whatever it is marked as)
        style: Line style - "solid", "dashed", or "dotted" (default: "solid")
        margin: Margin size - "sm", "md", or "lg" (default: "md")

    Examples:
        divider = Divider()
        divider = Divider(text="OR")
        divider = Divider(style="dashed", margin="lg")

        # In template
        {{ divider.render }}  # or just {{ divider }}
    """

    def __init__(self, text: Optional[str] = None, style: str = "solid", margin: str = "md"):
        """
        Initialize divider component.

        Args:
            text: Optional text to display in center of divider
            style: Line style - "solid", "dashed", or "dotted"
            margin: Margin size - "sm", "md", or "lg"
        """
        super().__init__(text=text, style=style, margin=margin)

        # Declared for type checkers; the base class already set each from the kwargs.
        self.text = text
        self.style = style
        self.margin = margin

    def _render_custom(self) -> str:
        margin_class = _MARGIN_CLASS.get(str(self.margin), "my-3")
        style_class = _STYLE_CLASS.get(str(self.style), "")
        if self.text is None:
            return f'<hr class="{margin_class}{style_class}">'
        return (
            f'<div class="d-flex align-items-center {margin_class}">'
            f'\n    <hr class="flex-grow-1{style_class}">'
            f'\n    <span class="px-3 text-muted">{attr_text(self.text)}</span>'
            f'\n    <hr class="flex-grow-1{style_class}">'
            "\n</div>"
        )
