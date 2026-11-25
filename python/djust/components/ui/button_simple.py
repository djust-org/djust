"""
Simple Button component for djust - stateless.

Provides buttons for display purposes.
For interactive buttons with event handlers, use them in LiveView event handlers.

One renderer (#4002): ``_render_custom`` returns the markup of ``ButtonComponent``, built from this
class's arguments (the mapping is in the class docstring), so the widget has one renderer.
"""

from typing import Any

from djust.components._alias_deprecation import warn_alias

from ..base import Component
from ._twin import plain_text, render_twin
from .button import ButtonComponent

#: The variants ``ButtonComponent`` draws; it draws any other (``link`` too) as ``primary``.
_VARIANTS = frozenset(
    {"primary", "secondary", "success", "danger", "warning", "info", "light", "dark"}
)


class Button(Component):
    """
    Simple, stateless button component, drawn by ``ButtonComponent``.

    Use Cases:
        - Display-only buttons (rendered but @click handled by parent)
        - Static button collections
        - Button groups

    Note: This is a stateless component for rendering. Event handlers are
    attached in the template using dj-click directives.

    Args:
        text: Button text content (``ButtonComponent``'s ``label``; escaped, whatever it
            is marked as)
        variant: Color variant (primary, secondary, success, danger, warning, info, light,
            dark; any other value, link included, draws as primary and warns)
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

        self._warn_unmapped()

    def _warn_unmapped(self) -> None:
        """Warn for a variant ``ButtonComponent`` has no equivalent for (#4002).

        ``stacklevel=5`` points at the caller of ``__init__`` / ``update``.
        """
        if self.variant not in _VARIANTS:
            warn_alias(
                "Button",
                "variant outside the eight Bootstrap colours",
                "primary, secondary, success, danger, warning, info, light or dark "
                "(any other, link included, draws as primary)",
                stacklevel=5,
            )

    def update(self, **kwargs: Any) -> Component:
        """Update the properties, warning again for an argument the twin cannot draw."""
        super().update(**kwargs)
        self._warn_unmapped()
        return self

    def _render_custom(self) -> str:
        return render_twin(
            ButtonComponent,
            self.id,
            label=plain_text(self.text),
            variant=self.variant,
            size=self.size,
            disabled=bool(self.disabled),
            outline=bool(self.outline),
        )
