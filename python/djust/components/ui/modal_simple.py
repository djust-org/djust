"""
Modal component for djust.

Simple stateless modal dialog (Bootstrap 5).

One renderer (#4002): ``_render_custom`` draws the markup this class's inline template drew
(the retired Rust ``RustModal`` was never reached: its required ``id`` is the base class's own
argument, so the constructor always fell back), pinned byte for byte by
``python/tests/test_ui_simple_golden_4002.py``.
"""

from typing import Optional

from ..base import Component

from django.utils.html import conditional_escape
from djust.components.utils import attr_text


class Modal(Component):
    """
    Modal dialog component (Bootstrap 5).

    Args:
        title: Modal title text (escaped)
        body: Modal body content (markup: escaped unless marked safe)
        footer: Optional footer content (markup: escaped unless marked safe)
        size: Modal size (sm, md, lg, xl)
        centered: Whether to vertically center the modal
        dismissable: Whether to show close button in header
        id: Modal ID for JavaScript control (defaults to ``"modal"``)
        show: Whether modal should be shown on page load

    Example:
        >>> modal = Modal(
        ...     title="Confirm Action",
        ...     body="Are you sure you want to continue?",
        ...     footer=mark_safe('<button class="btn btn-primary">Confirm</button>'),
        ... )
        >>> modal.render()
        '<div class="modal fade" ...'
    """

    # #3932: parameters the constructor already ``conditional_escape``s (a plain string is
    # escaped, a marked-safe one is developer markup): printed as given.
    markup_slots = frozenset({"body", "footer"})

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
    ) -> None:
        # ``body`` and ``footer`` are markup: HTML-escaped here unless they were marked safe.
        body = conditional_escape(body)
        if footer is not None:
            footer = conditional_escape(footer)

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

        # Declared for type checkers; the base class already set each from the kwargs.
        self.body = body
        self.title = title
        self.footer = footer
        self.size = size
        self.centered = centered
        self.dismissable = dismissable
        self.show = show

    def _render_custom(self) -> str:
        modal_id = attr_text(self.id)
        size = str(self.size)
        if size == "md":
            dialog = "modal-dialog"
        else:
            dialog = f"modal-dialog modal-{attr_text(size)}"
        if self.centered:
            dialog += " modal-dialog-centered"
        parts = [
            f'<div class="modal fade{" show" if self.show else ""}" id="{modal_id}" tabindex="-1"'
            f' aria-labelledby="{modal_id}Label" aria-hidden="true">',
            f'    <div class="{dialog}">',
            '        <div class="modal-content">',
        ]
        if self.title:
            parts.append('            <div class="modal-header">')
            parts.append(
                f'                <h5 class="modal-title" id="{modal_id}Label">'
                f"{attr_text(self.title)}</h5>"
            )
            if self.dismissable:
                parts.append(
                    '                <button type="button" class="btn-close"'
                    ' data-bs-dismiss="modal" aria-label="Close"></button>'
                )
            parts.append("            </div>")
        parts.append('            <div class="modal-body">')
        parts.append(f"                {self.body}")
        parts.append("            </div>")
        if self.footer:
            parts.append('            <div class="modal-footer">')
            parts.append(f"                {self.footer}")
            parts.append("            </div>")
        parts += ["        </div>", "    </div>", "</div>"]
        return "\n".join(parts)
