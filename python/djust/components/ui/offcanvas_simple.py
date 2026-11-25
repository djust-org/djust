"""
Offcanvas (sidebar drawer) component for djust.

Simple stateless offcanvas drawer.
"""

from typing import Any, Optional
from ..base import Component

from django.utils.html import conditional_escape


class Offcanvas(Component):
    """
    Offcanvas sidebar drawer component (Bootstrap 5).

    A sliding drawer that appears from the edge of the screen, perfect for
    navigation menus, filters, or additional content.

    Features:
    - Rendered through its template (there is no native Rust implementation)
    - Python fallback with f-strings
    - Slide-in from any side (start, end, top, bottom)
    - Backdrop overlay support
    - Dismiss button

    Args:
        title: Offcanvas title text
        body: Offcanvas body content. Plain strings are escaped; pass
            ``mark_safe`` or ``format_html`` output for developer-built HTML.
        placement: Side to slide from (start, end, top, bottom)
        backdrop: Whether to show backdrop overlay
        dismissable: Whether to show close button in header
        id: Offcanvas ID for JavaScript control (auto-generated if not provided)
        show: Whether offcanvas should be shown on page load

    Example:
        >>> from django.utils.safestring import mark_safe
        >>> offcanvas = Offcanvas(
        ...     title="Navigation Menu",
        ...     body=mark_safe("<nav><ul><li><a href='/'>Home</a></li></ul></nav>"),
        ...     placement="start"
        ... )
        >>> offcanvas.render()
        '<div class="offcanvas offcanvas-start">...'

        >>> # Right side with no backdrop
        >>> filters = Offcanvas(
        ...     title="Filters",
        ...     body=mark_safe("<form>...</form>"),
        ...     placement="end",
        ...     backdrop=False
        ... )
    """

    # #3932: parameters the constructor already ``conditional_escape``s (a plain string is
    # escaped, a marked-safe one is developer markup): the template prints them as given.
    markup_slots = frozenset({"body"})

    template = """<div class="offcanvas offcanvas-{{ placement }}{% if show %} show{% endif %}" tabindex="-1" id="{{ id }}" aria-labelledby="{{ id }}Label"{% if not backdrop %} data-bs-backdrop="false"{% endif %}>{% if title %}
    <div class="offcanvas-header">
        <h5 class="offcanvas-title" id="{{ id }}Label">{{ title }}</h5>{% if dismissable %}
        <button type="button" class="btn-close" data-bs-dismiss="offcanvas" aria-label="Close"></button>{% endif %}
    </div>{% endif %}
    <div class="offcanvas-body">
        {{ body }}
    </div>
</div>{% if show_backdrop %}
<div class="offcanvas-backdrop fade show"></div>{% endif %}"""

    def __init__(
        self,
        body: str,
        id: Optional[str] = None,
        title: Optional[str] = None,
        placement: str = "start",
        backdrop: bool = True,
        dismissable: bool = True,
        show: bool = False,
    ):
        # Validate placement
        if placement not in ("start", "end", "top", "bottom"):
            raise ValueError(
                f"Invalid placement: {placement}. Must be 'start', 'end', 'top', or 'bottom'"
            )

        # a plain string is escaped here, a marked-safe one is developer markup (as for Modal)
        body = conditional_escape(body)

        # Pass kwargs to the parent Component
        super().__init__(
            body=body,
            id=id,
            title=title,
            placement=placement,
            backdrop=backdrop,
            dismissable=dismissable,
            show=show,
        )

        # Set instance attributes for Python/hybrid rendering
        self.body = body
        self.title = title
        self.placement = placement
        self.backdrop = backdrop
        self.dismissable = dismissable
        self.show = show

    def get_context_data(self) -> dict[str, Any]:
        """Return context for template rendering."""
        return {
            "body": self.body,
            "title": self.title,
            "placement": self.placement,
            "backdrop": self.backdrop,
            "dismissable": self.dismissable,
            "id": self.id,
            "show": self.show,
            "show_backdrop": self.backdrop and self.show,
        }
