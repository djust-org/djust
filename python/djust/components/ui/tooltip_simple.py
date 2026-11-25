"""
Simple Tooltip component for djust - stateless.

Provides tooltips for displaying contextual information on hover/click/focus.

One renderer (#4002): ``_render_custom`` draws the Bootstrap 5 markup the retired Rust
``RustTooltip`` drew, pinned byte for byte by ``python/tests/test_ui_simple_golden_4002.py``,
with one fix: the space before ``title=`` that the Rust markup dropped
(``data-bs-arrow="true"title="..."``).
"""

from ..base import Component

from django.utils.html import conditional_escape
from djust.components.utils import attr_text

_PLACEMENTS = ("top", "bottom", "left", "right")
_TRIGGERS = ("hover", "click", "focus")


class Tooltip(Component):
    """
    Simple, stateless tooltip component (Bootstrap 5).

    Args:
        content: Main content (the element that triggers the tooltip; markup: escaped unless
            marked safe)
        text: Tooltip text content (escaped)
        placement: Tooltip position (top, bottom, left, right; anything else is top)
        trigger: Activation method (hover, click, focus; anything else is hover)
        arrow: Show arrow/pointer (True/False)

    Examples:
        tooltip = Tooltip("Hover me", text="This is helpful info")
        html = tooltip.render()

        bottom = Tooltip("Bottom", text="Tooltip on bottom", placement="bottom")
        click = Tooltip("Click", text="Show on click", trigger="click")
        icon_tip = Tooltip(mark_safe('<i class="bi bi-info-circle"></i>'), text="Help")
    """

    # #3932: ``content`` is escaped by the constructor (unless marked safe) and printed as given.
    markup_slots = frozenset({"content"})

    def __init__(
        self,
        content: str,
        text: str,
        placement: str = "top",
        trigger: str = "hover",
        arrow: bool = True,
    ):
        """
        Initialize tooltip component.

        Args:
            content: Main content (element that triggers tooltip)
            text: Tooltip text content
            placement: Position (top, bottom, left, right)
            trigger: Activation method (hover, click, focus)
            arrow: Show arrow/pointer
        """
        # ``content`` is markup: HTML-escaped here unless it was marked safe.
        content = conditional_escape(content)

        super().__init__(
            content=content, text=text, placement=placement, trigger=trigger, arrow=arrow
        )

        # Declared for type checkers; the base class already set each from the kwargs.
        self.content = content
        self.text = text
        self.placement = placement
        self.trigger = trigger
        self.arrow = arrow

    def _render_custom(self) -> str:
        placement = next((p for p in _PLACEMENTS if p == self.placement), "top")
        trigger = next((t for t in _TRIGGERS if t == self.trigger), "hover")
        arrow = ' data-bs-arrow="true"' if self.arrow else ""
        return (
            '<span class="d-inline-block" tabindex="0" data-bs-toggle="tooltip"'
            f' data-bs-placement="{placement}" data-bs-trigger="{trigger}"{arrow}'
            f' title="{attr_text(self.text)}">{self.content}</span>'
        )
