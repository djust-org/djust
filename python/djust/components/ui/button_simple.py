"""
Simple Button component for djust - stateless.

Provides buttons for display purposes.
For interactive buttons with event handlers, use them in LiveView event handlers.

One renderer (#4002): ``_render_custom`` draws the Bootstrap 5 markup the retired Rust
``RustButton`` drew, pinned byte for byte by ``python/tests/test_ui_simple_golden_4002.py``.
"""

from ..base import Component

from djust.components.utils import attr_text

_SIZE_CLASS = {"sm": " btn-sm", "lg": " btn-lg"}


class Button(Component):
    """
    Simple, stateless button component (Bootstrap 5).

    Use Cases:
        - Display-only buttons (rendered but @click handled by parent)
        - Static button collections
        - Button groups

    Note: This is a stateless component for rendering. Event handlers are
    attached in the template using dj-click directives.

    Args:
        text: Button text content (escaped, whatever it is marked as)
        variant: Color variant (primary, secondary, success, danger, warning, info, light, dark, link)
        size: Button size (sm, md, lg)
        disabled: Whether button is disabled
        outline: Use outline style instead of filled

    Examples:
        btn = Button("Click me", variant="primary")
        html = btn.render()

        small = Button("Small", size="sm")
        outline = Button("Outline", variant="primary", outline=True)
        disabled = Button("Disabled", disabled=True)
    """

    def __init__(
        self,
        text: str,
        variant: str = "primary",
        size: str = "md",
        disabled: bool = False,
        outline: bool = False,
    ):
        """
        Initialize button component.

        Args:
            text: Button text content
            variant: Color variant
            size: Button size (sm, md, lg)
            disabled: Whether button is disabled
            outline: Use outline style
        """
        super().__init__(text=text, variant=variant, size=size, disabled=disabled, outline=outline)

        # Declared for type checkers; the base class already set each from the kwargs.
        self.text = text
        self.variant = variant
        self.size = size
        self.disabled = disabled
        self.outline = outline

    def _render_custom(self) -> str:
        kind = "btn-outline-" if self.outline else "btn-"
        classes = f"btn {kind}{attr_text(self.variant)}{_SIZE_CLASS.get(str(self.size), '')}"
        disabled = " disabled" if self.disabled else ""
        return f'<button type="button" class="{classes}"{disabled}>{attr_text(self.text)}</button>'
