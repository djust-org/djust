"""
Card Component - Stateless container with header, body, and footer.

Features:
- Header (optional)
- Body (required)
- Footer (optional)
- Variants (default, outlined, elevated)

One renderer (#4002): ``_render_custom`` returns the markup of ``CardComponent``, built from this
class's arguments (the mapping is in the class docstring), so the widget has one renderer.
"""

from typing import Optional

from djust.components._alias_deprecation import warn_alias

from ..base import Component
from ._twin import plain_text, render_twin
from .card import CardComponent


class Card(Component):
    """
    Simple stateless Card component, drawn by ``CardComponent``.

    Args:
        body: Body text (required, escaped; ``CardComponent``'s ``body``)
        header: Optional header text (escaped). ``CardComponent`` has no header section: it
            draws as the card title (``title``) at the top of the body.
        footer: Optional footer text (escaped; ``footer``)
        variant: Deprecated (#4002): ``CardComponent`` has no variant, so any value draws the
            default card; a value other than ``default`` warns. Add ``border`` / ``shadow``
            on the container instead.

    An empty header or footer draws no section.
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

        if variant != "default":
            warn_alias(
                "Card", "variant", "a border/shadow class on the container (it draws no variant)"
            )

    def _render_custom(self) -> str:
        return render_twin(
            CardComponent,
            self.id,
            title=plain_text(self.header),
            body=plain_text(self.body),
            footer=plain_text(self.footer),
        )
