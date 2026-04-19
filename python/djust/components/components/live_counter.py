"""LiveCounter component for real-time animated counter updates via WebSocket."""

import html

from djust import Component


class LiveCounter(Component):
    """Animated counter updating in real-time via WebSocket push.

    Number rolls on change with a CSS transition animation.

    Usage in a LiveView::

        self.users_online = LiveCounter(
            value=42,
            label="online",
            stream_event="counter_update",
        )

    In template::

        {{ users_online|safe }}

    CSS Custom Properties::

        --dj-live-counter-fg: value text color
        --dj-live-counter-label-fg: label text color
        --dj-live-counter-font-size: value font size

    Args:
        value: Current counter value
        label: Text label shown after the number
        stream_event: WebSocket event name for counter updates
        size: Size variant (sm, md, lg)
        custom_class: Additional CSS classes
    """

    def __init__(
        self,
        value: int = 0,
        label: str = "",
        stream_event: str = "counter_update",
        size: str = "md",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            value=value,
            label=label,
            stream_event=stream_event,
            size=size,
            custom_class=custom_class,
            **kwargs,
        )
        self.value = value
        self.label = label
        self.stream_event = stream_event
        self.size = size
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        e_size = html.escape(str(self.size))
        e_custom_class = html.escape(str(self.custom_class))
        e_stream_event = html.escape(str(self.stream_event))
        e_label = html.escape(str(self.label))

        try:
            value = int(self.value)
        except (ValueError, TypeError):
            value = 0

        cls = f"dj-live-counter dj-live-counter--{e_size}"
        if e_custom_class:
            cls += f" {e_custom_class}"

        label_html = ""
        if e_label:
            label_html = f'<span class="dj-live-counter__label">{e_label}</span>'

        return (
            f'<div class="{cls}" data-stream-event="{e_stream_event}">'
            f'<span class="dj-live-counter__value" data-value="{value}">{value}</span>'
            f"{label_html}"
            f"</div>"
        )
