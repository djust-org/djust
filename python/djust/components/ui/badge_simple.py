"""
Simple Badge component for djust - stateless.

Provides small labels/badges for counts, statuses, and categories.
Use BadgeComponent for interactive badges with state.

One renderer (#4002): ``_render_custom`` draws the Bootstrap 5 markup the retired Rust
``RustBadge`` drew, pinned byte for byte by ``python/tests/test_ui_simple_golden_4002.py``.
"""

from ..base import Component

from djust.components.utils import attr_text

#: Bootstrap's default badge is 0.75em, so: sm = default, md = fs-6 (1rem), lg = fs-5 (1.25rem).
_SIZE_CLASS = {"md": " fs-6", "lg": " fs-5"}


class Badge(Component):
    """
    Simple, stateless badge component (Bootstrap 5).

    Use Cases:
        - Display-only badges (no interaction needed)
        - Status indicators
        - Counts and labels
        - Tags (without dismiss functionality)

    For interactive badges with state (dismissible, dynamic), use BadgeComponent (LiveComponent).

    Args:
        text: Badge text content (escaped, whatever it is marked as)
        variant: Color variant (primary, secondary, success, danger, warning, info, light, dark)
        size: Badge size (sm, md, lg) - optional, defaults to md
        pill: Rounded pill style (True/False) - optional, defaults to False

    Examples:
        badge = Badge("New", variant="primary")
        html = badge.render()

        # In template
        {{ badge.render }}  # or just {{ badge }}

        count = Badge("99+", variant="danger", pill=True)
        size_lg = Badge("Large", variant="primary", size="lg")
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

    def _render_custom(self) -> str:
        classes = f"badge bg-{attr_text(self.variant)}{_SIZE_CLASS.get(str(self.size), '')}"
        if self.pill:
            classes += " rounded-pill"
        return f'<span class="{classes}">{attr_text(self.text)}</span>'
