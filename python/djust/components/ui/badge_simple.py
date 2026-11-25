"""
Simple Badge component for djust - stateless.

Provides small labels/badges for counts, statuses, and categories.
Use BadgeComponent for interactive badges with state.

One renderer (#4002): ``_render_custom`` returns the markup of ``BadgeComponent``, built from this
class's arguments (the mapping is in the class docstring), so the widget has one renderer.
"""

from typing import Any

from djust.components._alias_deprecation import warn_alias

from ..base import Component
from ._twin import plain_text, render_twin
from .badge import BadgeComponent

#: The variants ``BadgeComponent`` draws; it draws any other as ``primary``.
_VARIANTS = frozenset(
    {"primary", "secondary", "success", "danger", "warning", "info", "light", "dark"}
)


class Badge(Component):
    """
    Simple, stateless badge component, drawn by ``BadgeComponent``.

    Use Cases:
        - Display-only badges (no interaction needed)
        - Status indicators
        - Counts and labels
        - Tags (without dismiss functionality)

    For interactive badges with state (dismissible, dynamic), use BadgeComponent (LiveComponent).

    Args:
        text: Badge text content (escaped, whatever it is marked as)
        variant: Color variant (primary, secondary, success, danger, warning, info, light, dark;
            any other draws as primary and warns)
        size: Deprecated (#4002): ``BadgeComponent`` has no size, so any value draws the
            default badge; a value other than ``md`` warns. Size it with a ``fs-*`` class
            on the container instead.
        pill: Rounded pill style (True/False) - optional, defaults to False

    Examples:
        badge = Badge("New", variant="primary")
        html = badge.render()

        # In template
        {{ badge.render }}  # or just {{ badge }}

        count = Badge("99+", variant="danger", pill=True)
    """

    def __init__(self, text: str, variant: str = "primary", size: str = "md", pill: bool = False):
        """
        Initialize badge component.

        Args:
            text: Badge text content
            variant: Color variant (primary, secondary, success, danger, warning, info, light, dark)
            size: Badge size (sm, md, lg)
            pill: Rounded pill style
        """
        super().__init__(text=text, variant=variant, size=size, pill=pill)

        # Declared for type checkers; the base class already set each from the kwargs.
        self.text = text
        self.variant = variant
        self.size = size
        self.pill = pill

        self._warn_unmapped()

    def _warn_unmapped(self) -> None:
        """Warn for an argument value the twin has no equivalent for (#4002).

        ``stacklevel=5`` points at the caller of ``__init__`` / ``update``.
        """
        if self.size != "md":
            warn_alias(
                "Badge",
                "size",
                "a font-size class on the container (it draws no size)",
                stacklevel=5,
            )
        if self.variant not in _VARIANTS:
            warn_alias(
                "Badge",
                "variant outside the eight Bootstrap colours",
                "primary, secondary, success, danger, warning, info, light or dark "
                "(any other draws as primary)",
                stacklevel=5,
            )

    def update(self, **kwargs: Any) -> Component:
        """Update the properties, warning again for an argument the twin cannot draw."""
        super().update(**kwargs)
        self._warn_unmapped()
        return self

    def _render_custom(self) -> str:
        return render_twin(
            BadgeComponent,
            self.id,
            text=plain_text(self.text),
            variant=self.variant,
            pill=self.pill,
        )
