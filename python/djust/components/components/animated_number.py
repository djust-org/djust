"""Animated Number component — counting animation for numeric values."""

import html

from djust import Component


class AnimatedNumber(Component):
    """Animated counting number display.

    Renders a number with a CSS/JS counting animation from 0 (or previous
    value) to the target value. Uses dj-hook for client-side animation.

    Usage in a LiveView::

        self.revenue = AnimatedNumber(
            value=12345,
            prefix="$",
            duration=800,
        )

    In template::

        {{ revenue|safe }}

    CSS Custom Properties::

        --dj-anim-number-size: font size (default: 2rem)
        --dj-anim-number-weight: font weight (default: 700)
        --dj-anim-number-color: text color (default: inherit)

    Args:
        value: Target numeric value.
        prefix: Text before the number (e.g. "$").
        suffix: Text after the number (e.g. "%").
        duration: Animation duration in ms (default: 800).
        decimals: Number of decimal places (default: 0).
        separator: Thousands separator (default: ",").
        custom_class: Additional CSS classes.
    """

    def __init__(
        self,
        value: float = 0,
        prefix: str = "",
        suffix: str = "",
        duration: int = 800,
        decimals: int = 0,
        separator: str = ",",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            value=value,
            prefix=prefix,
            suffix=suffix,
            duration=duration,
            decimals=decimals,
            separator=separator,
            custom_class=custom_class,
            **kwargs,
        )
        self.value = value
        self.prefix = prefix
        self.suffix = suffix
        self.duration = duration
        self.decimals = decimals
        self.separator = separator
        self.custom_class = custom_class

    def _format_number(self, val):
        """Format a number with thousands separator and decimals."""
        try:
            val = float(val)
        except (ValueError, TypeError):
            val = 0
        if self.decimals > 0:
            formatted = f"{val:,.{self.decimals}f}"
        else:
            formatted = f"{int(val):,}"
        if self.separator != ",":
            formatted = formatted.replace(",", self.separator)
        return formatted

    def _render_custom(self) -> str:
        cls = "dj-animated-number"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"

        e_prefix = html.escape(str(self.prefix))
        e_suffix = html.escape(str(self.suffix))

        try:
            val = float(self.value)
        except (ValueError, TypeError):
            val = 0
        try:
            duration = int(self.duration)
        except (ValueError, TypeError):
            duration = 800
        try:
            decimals = int(self.decimals)
        except (ValueError, TypeError):
            decimals = 0

        formatted = html.escape(self._format_number(val))
        e_sep = html.escape(self.separator)

        prefix_html = ""
        if e_prefix:
            prefix_html = f'<span class="dj-animated-number__prefix">{e_prefix}</span>'
        suffix_html = ""
        if e_suffix:
            suffix_html = f'<span class="dj-animated-number__suffix">{e_suffix}</span>'

        return (
            f'<span class="{cls}" dj-hook="AnimatedNumber" '
            f'data-value="{val}" data-duration="{duration}" '
            f'data-decimals="{decimals}" data-separator="{e_sep}">'
            f"{prefix_html}"
            f'<span class="dj-animated-number__value">{formatted}</span>'
            f"{suffix_html}"
            f"</span>"
        )
