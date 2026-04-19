"""AI thinking indicator component for animated status display."""

import html

from djust import Component


class ThinkingIndicator(Component):
    """Animated status indicator for AI processing states.

    Shows different animations based on the current AI status:
    thinking (bouncing dots), searching (pulse), generating (cursor blink),
    tool_use (spinner).

    Usage in a LiveView::

        self.indicator = ThinkingIndicator(status="thinking", label="Analyzing data...")

        # Update status
        self.indicator.status = "generating"
        self.indicator.label = "Writing response..."

    In template::

        {{ indicator|safe }}

    CSS Custom Properties::

        --dj-thinking-color: indicator color (default: currentColor)
        --dj-thinking-dot-size: dot diameter (default: 0.5rem)
        --dj-thinking-gap: gap between label and animation (default: 0.5rem)

    Args:
        status: Animation type (thinking, searching, generating, tool_use, idle)
        label: Descriptive text displayed alongside animation
        custom_class: Additional CSS classes
    """

    VALID_STATUSES = {"thinking", "searching", "generating", "tool_use", "idle"}

    def __init__(
        self,
        status: str = "thinking",
        label: str = "",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(status=status, label=label, custom_class=custom_class, **kwargs)
        self.status = status
        self.label = label
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        safe_status = self.status if self.status in self.VALID_STATUSES else "thinking"

        if safe_status == "idle":
            return ""

        cls = f"dj-thinking dj-thinking--{safe_status}"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"

        e_label = html.escape(self.label) if self.label else ""

        if safe_status == "thinking":
            anim = (
                '<span class="dj-thinking__dots">'
                '<span class="dj-thinking__dot"></span>'
                '<span class="dj-thinking__dot"></span>'
                '<span class="dj-thinking__dot"></span>'
                "</span>"
            )
        elif safe_status == "searching":
            anim = '<span class="dj-thinking__pulse"></span>'
        elif safe_status == "generating":
            anim = '<span class="dj-thinking__cursor"></span>'
        else:  # tool_use
            anim = '<span class="dj-thinking__spinner"></span>'

        label_html = f'<span class="dj-thinking__label">{e_label}</span>' if e_label else ""

        return (
            f'<div class="{cls}" role="status" aria-label="{e_label or safe_status}">'
            f"{anim}{label_html}"
            f"</div>"
        )
