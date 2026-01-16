"""
Offcanvas (sidebar drawer) component for djust.

Simple stateless offcanvas drawer with automatic Rust optimization.
"""

from typing import Optional
from ..base import Component

try:
    from djust._rust import RustOffcanvas

    _RUST_AVAILABLE = True
except ImportError:
    _RUST_AVAILABLE = False


class Offcanvas(Component):
    """
    Offcanvas sidebar drawer component (Bootstrap 5).

    A sliding drawer that appears from the edge of the screen, perfect for
    navigation menus, filters, or additional content.

    Features:
    - Automatic Rust optimization (~1μs rendering)
    - Template fallback if Rust unavailable
    - Python fallback with f-strings
    - Slide-in from any side (start, end, top, bottom)
    - Backdrop overlay support
    - Dismiss button

    Args:
        title: Offcanvas title text
        body: Offcanvas body content (HTML allowed)
        placement: Side to slide from (start, end, top, bottom)
        backdrop: Whether to show backdrop overlay
        dismissable: Whether to show close button in header
        id: Offcanvas ID for JavaScript control (auto-generated if not provided)
        show: Whether offcanvas should be shown on page load

    Example:
        >>> offcanvas = Offcanvas(
        ...     title="Navigation Menu",
        ...     body="<nav><ul><li><a href='/'>Home</a></li></ul></nav>",
        ...     placement="start"
        ... )
        >>> offcanvas.render()
        '<div class="offcanvas offcanvas-start">...'

        >>> # Right side with no backdrop
        >>> filters = Offcanvas(
        ...     title="Filters",
        ...     body="<form>...</form>",
        ...     placement="end",
        ...     backdrop=False
        ... )
    """

    _rust_impl_class = RustOffcanvas if _RUST_AVAILABLE else None

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

        # Pass kwargs to parent to create Rust instance
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

    def get_context_data(self):
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

    def _render_custom(self) -> str:
        """Pure Python fallback (f-string rendering)."""
        # Build offcanvas classes
        offcanvas_classes = ["offcanvas", f"offcanvas-{self.placement}"]
        if self.show:
            offcanvas_classes.append("show")

        # Build backdrop data attribute
        backdrop_attr = "" if self.backdrop else ' data-bs-backdrop="false"'

        parts = [
            f'<div class="{" ".join(offcanvas_classes)}" tabindex="-1" id="{self.id}" aria-labelledby="{self.id}Label"{backdrop_attr}>',
        ]

        # Add header if title exists
        if self.title:
            parts.append('    <div class="offcanvas-header">')
            parts.append(
                f'        <h5 class="offcanvas-title" id="{self.id}Label">{self.title}</h5>'
            )
            if self.dismissable:
                parts.append(
                    '        <button type="button" class="btn-close" data-bs-dismiss="offcanvas" aria-label="Close"></button>'
                )
            parts.append("    </div>")

        # Body
        parts.append('    <div class="offcanvas-body">')
        parts.append(f"        {self.body}")
        parts.append("    </div>")

        parts.append("</div>")

        # Add backdrop if needed and shown
        if self.backdrop and self.show:
            parts.append('<div class="offcanvas-backdrop fade show"></div>')

        return "\n".join(parts)
