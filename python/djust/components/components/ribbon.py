"""Ribbon Badge component — corner ribbon overlay."""

import html

from djust import Component


class Ribbon(Component):
    """Corner ribbon overlay badge.

    Renders a positioned ribbon badge (e.g., "Popular", "New", "Sale")
    in a corner of its parent container.

    Usage in a LiveView::

        self.ribbon = Ribbon(
            text="Popular",
            variant="primary",
            position="top-right",
        )

    In template::

        <div style="position:relative">
            {{ ribbon|safe }}
            <p>Card content</p>
        </div>

    CSS Custom Properties::

        --dj-ribbon-bg: background color (default: #3b82f6)
        --dj-ribbon-fg: text color (default: #fff)
        --dj-ribbon-size: ribbon width (default: 8rem)
        --dj-ribbon-font-size: text size (default: 0.75rem)

    Args:
        text: Ribbon label text.
        variant: Color variant (primary, success, warning, danger).
        position: Corner position (top-left, top-right, bottom-left, bottom-right).
        custom_class: Additional CSS classes.
    """

    VARIANT_MAP = {
        "primary": "dj-ribbon--primary",
        "success": "dj-ribbon--success",
        "warning": "dj-ribbon--warning",
        "danger": "dj-ribbon--danger",
    }

    def __init__(
        self,
        text: str = "",
        variant: str = "primary",
        position: str = "top-right",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            text=text,
            variant=variant,
            position=position,
            custom_class=custom_class,
            **kwargs,
        )
        self.text = text
        self.variant = variant
        self.position = position
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        classes = ["dj-ribbon"]

        variant_cls = self.VARIANT_MAP.get(self.variant, "dj-ribbon--primary")
        classes.append(variant_cls)

        pos = (
            self.position
            if self.position in ("top-left", "top-right", "bottom-left", "bottom-right")
            else "top-right"
        )
        classes.append(f"dj-ribbon--{pos}")

        if self.custom_class:
            classes.append(html.escape(self.custom_class))

        cls = " ".join(classes)
        e_text = html.escape(str(self.text))

        return (
            f'<div class="{cls}" aria-label="{e_text}">'
            f'<span class="dj-ribbon__text">{e_text}</span>'
            f"</div>"
        )
