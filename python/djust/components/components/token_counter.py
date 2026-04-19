"""Token Counter component for displaying token usage vs limit."""

import html
from typing import Optional

from djust import Component


class TokenCounter(Component):
    """Compact progress display showing token usage versus limit.

    Color transitions from green through yellow to red as the limit approaches.

    Usage in a LiveView::

        self.tokens = TokenCounter(current=1500, max=4096)

        # Near limit
        self.tokens = TokenCounter(current=3800, max=4096)

    In template::

        {{ tokens|safe }}

    CSS Custom Properties::

        --dj-token-bg: track background
        --dj-token-bar-bg: bar color (overridden by threshold classes)
        --dj-token-radius: border radius (default: 9999px)
        --dj-token-height: bar height (default: 0.375rem)
        --dj-token-ok-color: color when below 60% (green)
        --dj-token-warn-color: color when 60-85% (yellow/orange)
        --dj-token-danger-color: color when above 85% (red)

    Args:
        current: Current token count
        max: Maximum token limit
        label: Optional label (default: auto-generated "1,500 / 4,096")
        show_label: Whether to display the label (default: True)
        custom_class: Additional CSS classes
    """

    def __init__(
        self,
        current: int = 0,
        max: int = 4096,
        label: Optional[str] = None,
        show_label: bool = True,
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            current=current,
            max=max,
            label=label,
            show_label=show_label,
            custom_class=custom_class,
            **kwargs,
        )
        self.current = current
        self.max = max
        self.label = label
        self.show_label = show_label
        self.custom_class = custom_class

    @property
    def percentage(self) -> float:
        if self.max <= 0:
            return 0
        return min(100, max(0, (self.current / self.max) * 100))

    @property
    def threshold_class(self) -> str:
        pct = self.percentage
        if pct >= 85:
            return "dj-token--danger"
        elif pct >= 60:
            return "dj-token--warn"
        return "dj-token--ok"

    def _render_custom(self) -> str:
        cls = f"dj-token {self.threshold_class}"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"

        pct = self.percentage

        label_html = ""
        if self.show_label:
            if self.label:
                display_label = html.escape(self.label)
            else:
                display_label = f"{self.current:,} / {self.max:,}"
            label_html = f'<span class="dj-token__label">{display_label}</span>'

        return (
            f'<div class="{cls}" role="meter" '
            f'aria-valuenow="{self.current}" aria-valuemin="0" aria-valuemax="{self.max}" '
            f'aria-label="Token usage">'
            f"{label_html}"
            f'<div class="dj-token__track">'
            f'<div class="dj-token__bar" style="width:{pct:.1f}%"></div>'
            f"</div>"
            f"</div>"
        )
