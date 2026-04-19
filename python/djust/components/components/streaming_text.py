"""StreamingText component for incremental text rendering via WebSocket."""

import html

from djust import Component


class StreamingText(Component):
    """Renders text arriving incrementally via WebSocket with typing cursor.

    Auto-scrolls and optionally supports markdown rendering.

    Usage in a LiveView::

        self.stream = StreamingText(stream_event="stream_chunk")

        # With initial text
        self.stream = StreamingText(
            text="Hello...",
            stream_event="ai_response",
            markdown=True,
        )

    In template::

        {{ stream|safe }}

    CSS Custom Properties::

        --dj-streaming-text-bg: background color
        --dj-streaming-text-fg: text color
        --dj-streaming-text-cursor-color: cursor blink color

    Args:
        stream_event: WebSocket event name for incoming text chunks
        text: Initial text content
        markdown: Whether to render text as markdown
        auto_scroll: Whether to auto-scroll to bottom on new content
        cursor: Whether to show a typing cursor animation
        custom_class: Additional CSS classes
    """

    def __init__(
        self,
        stream_event: str = "stream_chunk",
        text: str = "",
        markdown: bool = False,
        auto_scroll: bool = True,
        cursor: bool = True,
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            stream_event=stream_event,
            text=text,
            markdown=markdown,
            auto_scroll=auto_scroll,
            cursor=cursor,
            custom_class=custom_class,
            **kwargs,
        )
        self.stream_event = stream_event
        self.text = text
        self.markdown = markdown
        self.auto_scroll = auto_scroll
        self.cursor = cursor
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        cls = "dj-streaming-text"
        if self.cursor:
            cls += " dj-streaming-text--cursor"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"

        attrs = [
            f'class="{cls}"',
            f'data-stream-event="{html.escape(self.stream_event)}"',
        ]
        if self.auto_scroll:
            attrs.append('data-auto-scroll="true"')
        if self.markdown:
            attrs.append('data-markdown="true"')

        attrs_str = " ".join(attrs)
        e_text = html.escape(self.text)
        return f'<div {attrs_str}><div class="dj-streaming-text__content">{e_text}</div></div>'
