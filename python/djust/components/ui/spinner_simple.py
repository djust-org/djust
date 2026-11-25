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

One renderer (#4002): ``_render_custom`` returns the markup of ``SpinnerComponent``, built from this
class's arguments (the mapping is in the class docstring), so the widget has one renderer.
"""

from djust.components._alias_deprecation import warn_alias

from ..base import Component
from ._twin import render_twin
from .spinner import SpinnerComponent


class Spinner(Component):
    """
    Simple stateless Spinner component, drawn by ``SpinnerComponent``.

    Args:
        variant: Color variant (primary, secondary, success, danger, warning, info, light, dark)
        size: Size variant (sm, md, lg, xl)
        animation: Animation type (``SpinnerComponent``'s ``type``): border, or grow for any
            other value
        sr_text: Deprecated (#4002): ``SpinnerComponent`` always reads "Loading..." to a
            screen reader; another value warns and is not drawn.
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

        if sr_text != "Loading...":
            warn_alias("Spinner", "sr_text", "the default screen-reader text (it draws no other)")

    def _render_custom(self) -> str:
        return render_twin(
            SpinnerComponent, self.id, variant=self.variant, size=self.size, type=self.animation
        )
