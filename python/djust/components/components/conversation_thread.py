"""Conversation thread component for chat-style message display."""

import html
from typing import Dict, List, Optional

from djust import Component


class ConversationThread(Component):
    """Chat-style message thread with sender avatars, timestamps, and grouping.

    Displays a scrollable list of messages with avatar initials, sender names,
    timestamps, and a streaming response indicator for in-progress AI replies.

    Usage in a LiveView::

        self.thread = ConversationThread(
            messages=[
                {"sender": "user", "name": "Alice", "text": "Hello!", "time": "10:01"},
                {"sender": "ai", "name": "Assistant", "text": "Hi there!", "time": "10:02"},
            ],
            stream_event="new_message",
        )

    In template::

        {{ thread|safe }}

    CSS Custom Properties::

        --dj-chat-bg: thread background
        --dj-chat-bubble-user-bg: user message bubble background
        --dj-chat-bubble-ai-bg: AI message bubble background
        --dj-chat-avatar-size: avatar circle size (default: 2rem)
        --dj-chat-gap: gap between messages (default: 0.75rem)

    Args:
        messages: List of message dicts with keys: sender, name, text, time
        stream_event: WebSocket event name for incoming messages
        streaming: Whether the AI is currently streaming a response
        custom_class: Additional CSS classes
    """

    def __init__(
        self,
        messages: Optional[List[Dict]] = None,
        stream_event: str = "new_message",
        streaming: bool = False,
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            messages=messages,
            stream_event=stream_event,
            streaming=streaming,
            custom_class=custom_class,
            **kwargs,
        )
        self.messages = messages or []
        self.stream_event = stream_event
        self.streaming = streaming
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        cls = "dj-chat-thread"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"

        msgs_html = []
        prev_sender = None
        for msg in self.messages:
            sender = msg.get("sender", "user")
            name = html.escape(str(msg.get("name", "")))
            text = html.escape(str(msg.get("text", "")))
            time = html.escape(str(msg.get("time", "")))

            grouped = "dj-chat-msg--grouped" if sender == prev_sender else ""
            side = "dj-chat-msg--ai" if sender == "ai" else "dj-chat-msg--user"

            initials = name[:1].upper() if name else "?"
            avatar = (
                f'<span class="dj-chat-avatar">{initials}</span>'
                if sender != prev_sender
                else '<span class="dj-chat-avatar dj-chat-avatar--hidden"></span>'
            )

            header = ""
            if sender != prev_sender:
                header = (
                    f'<div class="dj-chat-msg__header">'
                    f'<span class="dj-chat-msg__name">{name}</span>'
                    f'<span class="dj-chat-msg__time">{time}</span>'
                    f"</div>"
                )

            msgs_html.append(
                f'<div class="dj-chat-msg {side} {grouped}">'
                f"{avatar}"
                f'<div class="dj-chat-bubble">'
                f"{header}"
                f'<div class="dj-chat-msg__text">{text}</div>'
                f"</div>"
                f"</div>"
            )
            prev_sender = sender

        streaming_html = ""
        if self.streaming:
            streaming_html = (
                '<div class="dj-chat-msg dj-chat-msg--ai">'
                '<span class="dj-chat-avatar">&#8943;</span>'
                '<div class="dj-chat-bubble">'
                '<div class="dj-chat-typing">'
                '<span class="dj-chat-typing__dot"></span>'
                '<span class="dj-chat-typing__dot"></span>'
                '<span class="dj-chat-typing__dot"></span>'
                "</div></div></div>"
            )

        e_stream = html.escape(self.stream_event)

        return (
            f'<div class="{cls}" data-stream-event="{e_stream}">'
            f"{''.join(msgs_html)}"
            f"{streaming_html}"
            f"</div>"
        )
