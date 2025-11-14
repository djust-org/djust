"""
Modal component for djust.

Simple stateless modal dialog with automatic Rust optimization.
"""

from typing import Optional
from ..base import Component

try:
    from djust._rust import RustModal

    _RUST_AVAILABLE = True
except ImportError:
    _RUST_AVAILABLE = False


class Modal(Component):
    """
    Modal dialog component (Bootstrap 5).

    Features:
    - Automatic Rust optimization (~1μs rendering)
    - Template fallback if Rust unavailable
    - Python fallback with f-strings

    Args:
        title: Modal title text
        body: Modal body content (HTML allowed)
        footer: Optional footer content (HTML allowed)
        size: Modal size (sm, md, lg, xl)
        centered: Whether to vertically center the modal
        dismissable: Whether to show close button in header
        id: Modal ID for JavaScript control (auto-generated if not provided)
        show: Whether modal should be shown on page load

    Example:
        >>> modal = Modal(
        ...     title="Confirm Action",
        ...     body="Are you sure you want to continue?",
        ...     footer='<button class="btn btn-secondary" data-bs-dismiss="modal">Cancel</button><button class="btn btn-primary">Confirm</button>'
        ... )
        >>> modal.render()
        '<div class="modal fade">...'
    """

    _rust_impl_class = RustModal if _RUST_AVAILABLE else None

    template = """<div class="modal fade{% if show %} show{% endif %}" id="{{ id }}" tabindex="-1" aria-labelledby="{{ id }}Label" aria-hidden="true">
    <div class="modal-dialog{% if size != "md" %} modal-{{ size }}{% endif %}{% if centered %} modal-dialog-centered{% endif %}">
        <div class="modal-content">{% if title %}
            <div class="modal-header">
                <h5 class="modal-title" id="{{ id }}Label">{{ title }}</h5>{% if dismissable %}
                <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>{% endif %}
            </div>{% endif %}
            <div class="modal-body">
                {{ body }}
            </div>{% if footer %}
            <div class="modal-footer">
                {{ footer }}
            </div>{% endif %}
        </div>
    </div>
</div>"""

    def __init__(
        self,
        body: str,
        id: Optional[str] = None,
        title: Optional[str] = None,
        footer: Optional[str] = None,
        size: str = "md",
        centered: bool = False,
        dismissable: bool = True,
        show: bool = False,
    ):
        # Pass kwargs to parent to create Rust instance
        super().__init__(
            body=body,
            id=id,
            title=title,
            footer=footer,
            size=size,
            centered=centered,
            dismissable=dismissable,
            show=show,
        )

        # Set instance attributes for Python/hybrid rendering
        self.body = body
        self.title = title
        self.footer = footer
        self.size = size
        self.centered = centered
        self.dismissable = dismissable

    def get_context_data(self):
        """Return context for template rendering."""
        return {
            "body": self.body,
            "title": self.title,
            "footer": self.footer,
            "size": self.size,
            "centered": self.centered,
            "dismissable": self.dismissable,
            "id": self.id,
            "show": self.show,
        }

    def _render_custom(self) -> str:
        """Pure Python fallback (f-string rendering)."""
        # Build modal classes
        modal_classes = ["modal", "fade"]
        if self.show:
            modal_classes.append("show")

        # Build dialog classes
        dialog_classes = ["modal-dialog"]
        if self.size != "md":
            dialog_classes.append(f"modal-{self.size}")
        if self.centered:
            dialog_classes.append("modal-dialog-centered")

        parts = [
            f'<div class="{" ".join(modal_classes)}" id="{self.id}" tabindex="-1" aria-labelledby="{self.id}Label" aria-hidden="true">',
            f'    <div class="{" ".join(dialog_classes)}">',
            '        <div class="modal-content">',
        ]

        # Add header if title exists
        if self.title:
            parts.append('            <div class="modal-header">')
            parts.append(
                f'                <h5 class="modal-title" id="{self.id}Label">{self.title}</h5>'
            )
            if self.dismissable:
                parts.append(
                    '                <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>'
                )
            parts.append("            </div>")

        # Body
        parts.append('            <div class="modal-body">')
        parts.append(f"                {self.body}")
        parts.append("            </div>")

        # Add footer if exists
        if self.footer:
            parts.append('            <div class="modal-footer">')
            parts.append(f"                {self.footer}")
            parts.append("            </div>")

        parts.extend(
            [
                "        </div>",
                "    </div>",
                "</div>",
            ]
        )

        return "\n".join(parts)
