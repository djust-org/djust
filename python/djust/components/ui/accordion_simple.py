"""
Accordion component for djust.

Simple stateless accordion with automatic Rust optimization.
"""

from typing import Any, Dict, List, Optional
from ..base import Component

from django.utils.html import conditional_escape


class Accordion(Component):
    """
    Accordion component (Bootstrap 5).

    Features:
    - Collapsible panels
    - Can have multiple panels open or single panel (flush style)
    - Python rendering with f-strings

    Args:
        items: List of accordion item dicts with 'title' and 'content' keys
        id: Accordion ID for JavaScript control (auto-generated if not provided)
        active_index: Index of initially open item (default: 0, None for all closed)
        flush: Whether to remove default background and borders
        always_open: Allow multiple items to be open simultaneously

    Example:
        >>> accordion = Accordion(items=[
        ...     {'title': 'Section 1', 'content': '<p>Content for section 1</p>'},
        ...     {'title': 'Section 2', 'content': '<p>Content for section 2</p>'},
        ... ])
        >>> accordion.render()
        '<div class="accordion">...'
    """

    # Note: Using Python fallback for simplicity with dynamic lists

    def __init__(
        self,
        items: List[Dict[str, str]],
        id: Optional[str] = None,
        active_index: Optional[int] = 0,
        flush: bool = False,
        always_open: bool = False,
    ):
        # Generate accordion ID if not provided
        import uuid

        accordion_id = id or f"accordion-{uuid.uuid4().hex[:8]}"

        # Pass kwargs to parent
        super().__init__(
            items=items,
            id=accordion_id,
            active_index=active_index,
            flush=flush,
            always_open=always_open,
        )

        # Set instance attributes
        self.items = items
        self.accordion_id = accordion_id
        self.active_index = active_index
        self.flush = flush
        self.always_open = always_open

    def get_context_data(self) -> dict[str, Any]:
        """Return context for template rendering."""
        return {
            "items": self.items,
            "accordion_id": self.accordion_id,
            "active_index": self.active_index,
            "flush": self.flush,
            "always_open": self.always_open,
        }

    def _render_custom(self) -> str:
        """Pure Python fallback (f-string rendering)."""
        # Accordion classes
        accordion_class = "accordion"
        if self.flush:
            accordion_class += " accordion-flush"

        e_id = conditional_escape(self.accordion_id)

        # Build accordion items
        accordion_items = []
        for i, item in enumerate(self.items):
            # Determine if this item should be shown
            is_open = self.active_index is not None and i == self.active_index

            # Collapse classes
            collapse_class = "accordion-collapse collapse"
            if is_open:
                collapse_class += " show"

            # Button attributes
            button_class = "accordion-button"
            if not is_open:
                button_class += " collapsed"

            button_attrs = [
                'type="button"',
                'data-bs-toggle="collapse"',
                f'data-bs-target="#{e_id}-collapse-{i}"',
                f'aria-expanded="{str(is_open).lower()}"',
                f'aria-controls="{e_id}-collapse-{i}"',
            ]

            # Collapse attributes
            collapse_attrs = []
            if not self.always_open:
                collapse_attrs.append(f'data-bs-parent="#{e_id}"')

            item_html = f"""    <div class="accordion-item">
        <h2 class="accordion-header" id="{e_id}-heading-{i}">
            <button class="{button_class}" {" ".join(button_attrs)}>
                {conditional_escape(item["title"])}
            </button>
        </h2>
        <div id="{e_id}-collapse-{i}"
             class="{collapse_class}"
             aria-labelledby="{e_id}-heading-{i}"
             {" ".join(collapse_attrs)}>
            <div class="accordion-body">
                {conditional_escape(item["content"])}
            </div>
        </div>
    </div>"""

            accordion_items.append(item_html)

        return f"""<div class="{accordion_class}" id="{e_id}">
{chr(10).join(accordion_items)}
</div>"""
