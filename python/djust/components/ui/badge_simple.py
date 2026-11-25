"""
Simple Badge component for djust - stateless.

Provides small labels/badges for counts, statuses, and categories.
Use BadgeComponent for interactive badges with state.

One renderer (#4002): ``_render_custom`` returns the markup of ``BadgeComponent``, built from this
class's arguments (the mapping is in the class docstring), so the widget has one renderer.
"""

from djust.components._alias_deprecation import warn_alias

from ..base import Component
from ._twin import plain_text, render_twin
from .badge import BadgeComponent


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
        variant: Color variant (primary, secondary, success, danger, warning, info, light, dark)
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

        if size != "md":
            warn_alias("Badge", "size", "a font-size class on the container (it draws no size)")

    def _render_custom(self) -> str:
        return render_twin(
            BadgeComponent,
            self.id,
            text=plain_text(self.text),
            variant=self.variant,
            pill=self.pill,
        )
