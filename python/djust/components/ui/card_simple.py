"""
Card Component - Stateless container with header, body, and footer.

Features:
- Header (optional)
- Body (required)
- Footer (optional)
- Variants (default, outlined, elevated)

One renderer (#4002): ``_render_custom`` draws the Bootstrap 5 markup the retired Rust
``RustCard`` drew, pinned byte for byte by ``python/tests/test_ui_simple_golden_4002.py``.
"""

from typing import Optional

from ..base import Component

from djust.components.utils import attr_text

_VARIANT_CLASS = {"outlined": " border", "elevated": " shadow"}


class Card(Component):
    """
    Simple stateless Card component (Bootstrap 5).

    Args:
        body: Body text (required, escaped)
        header: Optional header text (escaped)
        footer: Optional footer text (escaped)
        variant: Style variant (default, outlined, elevated)

    A header or footer of ``""`` still draws its (empty) section; ``None`` leaves it out.
    """

    def __init__(
        self,
        body: str,
        header: Optional[str] = None,
        footer: Optional[str] = None,
        variant: str = "default",
    ):
        super().__init__(body=body, header=header, footer=footer, variant=variant)

        # Declared for type checkers; the base class already set each from the kwargs.
        self.body = body
        self.header = header
        self.footer = footer
        self.variant = variant

    def _render_custom(self) -> str:
        html = f'<div class="card{_VARIANT_CLASS.get(str(self.variant), "")}">'
        if self.header is not None:
            html += f'\n    <div class="card-header">{attr_text(self.header)}</div>'
        html += f'\n    <div class="card-body">{attr_text(self.body)}</div>'
        if self.footer is not None:
            html += f'\n    <div class="card-footer">{attr_text(self.footer)}</div>'
        return html + "\n</div>"
