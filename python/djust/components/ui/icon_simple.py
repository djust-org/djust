"""
Simple Icon component for djust - stateless.

Provides icon rendering with popular icon libraries (Bootstrap Icons, Font Awesome, custom).

One renderer (#4002): ``_render_custom`` draws the markup the retired Rust ``RustIcon`` drew,
pinned byte for byte by ``python/tests/test_ui_simple_golden_4002.py``.
"""

from typing import Optional

from ..base import Component

from djust.components.utils import attr_text

_SIZE_CLASS = {"xs": " fs-6", "sm": " fs-5", "md": " fs-4", "lg": " fs-3", "xl": " fs-2"}


class Icon(Component):
    """
    Simple, stateless icon component.

    Args:
        name: Icon name/class (e.g., "star-fill", "fa-heart")
        library: Icon library to use (bootstrap, fontawesome, custom)
        size: Icon size (xs, sm, md, lg, xl), a Bootstrap ``fs-*`` class
        color: Optional color/variant (primary, secondary, success, danger, etc.)
        label: Optional aria-label for accessibility

    Examples:
        icon = Icon(name="star-fill", library="bootstrap", size="lg")
        icon = Icon(name="fa-heart", library="fontawesome", color="danger")
        icon = Icon(name="check-circle", label="Success", color="success")
    """

    def __init__(
        self,
        name: str,
        library: str = "bootstrap",
        size: str = "md",
        color: Optional[str] = None,
        label: Optional[str] = None,
    ):
        """
        Initialize icon component.

        Args:
            name: Icon name/class
            library: Icon library (bootstrap, fontawesome, custom)
            size: Icon size (xs, sm, md, lg, xl)
            color: Optional color variant
            label: Optional aria-label for accessibility
        """
        super().__init__(name=name, library=library, size=size, color=color, label=label)

        # Declared for type checkers; the base class already set each from the kwargs.
        self.name = name
        self.library = library
        self.size = size
        self.color = color
        self.label = label

    def _render_custom(self) -> str:
        name = attr_text(self.name)
        classes = f"bi bi-{name}" if self.library == "bootstrap" else name
        classes += _SIZE_CLASS.get(str(self.size), " fs-4")
        if self.color is not None:
            classes += f" text-{attr_text(self.color)}"
        aria = f' aria-label="{attr_text(self.label)}" role="img"' if self.label is not None else ""
        return f'<i class="{classes}"{aria}></i>'
