"""Agent Step Card component — AI agent tool use card."""

import html

from djust import Component


class AgentStep(Component):
    """AI agent tool-use step card.

    Renders a card showing an AI agent's tool invocation with status,
    tool name, and result content.

    Usage in a LiveView::

        self.step = AgentStep(
            tool="search_db",
            status="complete",
            content="Found 12 results",
        )

    In template::

        {{ step|safe }}

    CSS Custom Properties::

        --dj-agent-step-bg: card background (default: #f9fafb)
        --dj-agent-step-border: border color (default: #e5e7eb)
        --dj-agent-step-radius: border radius (default: 0.5rem)
        --dj-agent-step-icon-size: icon size (default: 1.25rem)

    Args:
        tool: Tool/function name.
        status: Step status (pending, running, complete, error).
        content: Step result content (plain text).
        duration: Execution duration text (e.g. "1.2s").
        custom_class: Additional CSS classes.
    """

    STATUS_ICONS = {
        "pending": "&#9711;",  # circle
        "running": "&#8987;",  # hourglass
        "complete": "&#10003;",  # check
        "error": "&#10007;",  # cross
    }

    def __init__(
        self,
        tool: str = "",
        status: str = "pending",
        content: str = "",
        duration: str = "",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            tool=tool,
            status=status,
            content=content,
            duration=duration,
            custom_class=custom_class,
            **kwargs,
        )
        self.tool = tool
        self.status = status
        self.content = content
        self.duration = duration
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        status = self.status if self.status in self.STATUS_ICONS else "pending"
        classes = ["dj-agent-step", f"dj-agent-step--{status}"]
        if self.custom_class:
            classes.append(html.escape(self.custom_class))
        cls = " ".join(classes)

        e_tool = html.escape(str(self.tool))
        e_content = html.escape(str(self.content))
        e_duration = html.escape(str(self.duration))

        icon = self.STATUS_ICONS.get(status, "&#9711;")

        duration_html = ""
        if e_duration:
            duration_html = f'<span class="dj-agent-step__duration">{e_duration}</span>'

        content_html = ""
        if e_content:
            content_html = f'<div class="dj-agent-step__content">{e_content}</div>'

        return (
            f'<div class="{cls}" role="listitem">'
            f'<div class="dj-agent-step__header">'
            f'<span class="dj-agent-step__icon" aria-hidden="true">{icon}</span>'
            f'<span class="dj-agent-step__tool">{e_tool}</span>'
            f'<span class="dj-agent-step__status">{html.escape(status)}</span>'
            f"{duration_html}"
            f"</div>"
            f"{content_html}"
            f"</div>"
        )
