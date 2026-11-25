"""
Spinner Component - Loading indicators.

A stateless spinner component for showing loading states.

Features:
- Size variants (sm, md)
- Color variants (primary, secondary, success, danger, etc.)
- Animation type (border, grow)
- Accessible (includes visually-hidden text)

Example:
    from djust.components.ui import Spinner

    spinner = Spinner()
    spinner = Spinner(variant="primary", size="sm")
    spinner = Spinner(animation="grow", variant="success")

One renderer (#4002): ``_render_custom`` draws the Bootstrap 5 markup the retired Rust
``RustSpinner`` drew, pinned byte for byte by ``python/tests/test_ui_simple_golden_4002.py``.
"""

from ..base import Component

from djust.components.utils import attr_text


class Spinner(Component):
    """
    Simple stateless Spinner component (Bootstrap 5).

    Args:
        variant: Color variant (primary, secondary, success, danger, warning, info, light, dark)
        size: Size variant (sm draws the small spinner; any other value the default one)
        animation: Animation type (border, grow)
        sr_text: Screen reader text for accessibility
    """

    def __init__(
        self,
        variant: str = "primary",
        size: str = "md",
        animation: str = "border",
        sr_text: str = "Loading...",
    ):
        super().__init__(
            variant=variant,
            size=size,
            animation=animation,
            sr_text=sr_text,
        )

        # Declared for type checkers; the base class already set each from the kwargs.
        self.variant = variant
        self.size = size
        self.animation = animation
        self.sr_text = sr_text

    def _render_custom(self) -> str:
        animation = attr_text(self.animation)
        size_class = f" spinner-{animation}-sm" if self.size == "sm" else ""
        return (
            f'<div class="spinner-{animation} text-{attr_text(self.variant)}{size_class}"'
            ' role="status">'
            f'\n    <span class="visually-hidden">{attr_text(self.sr_text)}</span>'
            "\n</div>"
        )
