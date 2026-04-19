"""Multimodal input component for text, file, and voice input."""

import html

from djust import Component


class MultimodalInput(Component):
    """Text area with optional file attachment and voice input buttons.

    Combines a text area, file upload button, optional voice input button,
    and a send button into a single input bar for chat interfaces.

    Usage in a LiveView::

        self.input = MultimodalInput(
            name="message",
            event="send",
            accept_files=True,
            accept_voice=True,
            placeholder="Type a message...",
        )

    In template::

        {{ input|safe }}

    CSS Custom Properties::

        --dj-mminput-bg: input bar background
        --dj-mminput-border: border color
        --dj-mminput-radius: border radius (default: 0.75rem)
        --dj-mminput-padding: internal padding
        --dj-mminput-btn-color: button icon color

    Args:
        name: Form field name for the text input
        event: djust event fired on send
        placeholder: Placeholder text for the textarea
        accept_files: Whether to show file attachment button
        accept_voice: Whether to show voice input button
        file_accept: MIME types for file input (default: "*/*")
        disabled: Whether the input is disabled
        custom_class: Additional CSS classes
    """

    def __init__(
        self,
        name: str = "message",
        event: str = "send",
        placeholder: str = "Type a message...",
        accept_files: bool = False,
        accept_voice: bool = False,
        file_accept: str = "*/*",
        disabled: bool = False,
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            name=name,
            event=event,
            placeholder=placeholder,
            accept_files=accept_files,
            accept_voice=accept_voice,
            file_accept=file_accept,
            disabled=disabled,
            custom_class=custom_class,
            **kwargs,
        )
        self.name = name
        self.event = event
        self.placeholder = placeholder
        self.accept_files = accept_files
        self.accept_voice = accept_voice
        self.file_accept = file_accept
        self.disabled = disabled
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        cls = "dj-mminput"
        if self.disabled:
            cls += " dj-mminput--disabled"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"

        e_name = html.escape(self.name)
        e_event = html.escape(self.event)
        e_placeholder = html.escape(self.placeholder)
        e_accept = html.escape(self.file_accept)
        disabled_attr = " disabled" if self.disabled else ""

        textarea = (
            f'<textarea class="dj-mminput__text" name="{e_name}" '
            f'placeholder="{e_placeholder}" rows="1"{disabled_attr}></textarea>'
        )

        file_btn = ""
        if self.accept_files:
            file_btn = (
                f'<label class="dj-mminput__btn dj-mminput__file-btn" title="Attach file">'
                f'<input type="file" accept="{e_accept}" hidden{disabled_attr}>'
                f'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
                f'stroke-width="2" width="18" height="18">'
                f'<path d="M21.44 11.05l-9.19 9.19a6 6 0 01-8.49-8.49l9.19-9.19a4 4 0 015.66 5.66l-9.2 9.19a2 2 0 01-2.83-2.83l8.49-8.48"/>'
                f"</svg></label>"
            )

        voice_btn = ""
        if self.accept_voice:
            voice_btn = (
                f'<button type="button" class="dj-mminput__btn dj-mminput__voice-btn" '
                f'title="Voice input"{disabled_attr}>'
                f'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
                f'stroke-width="2" width="18" height="18">'
                f'<path d="M12 1a3 3 0 00-3 3v8a3 3 0 006 0V4a3 3 0 00-3-3z"/>'
                f'<path d="M19 10v2a7 7 0 01-14 0v-2"/>'
                f'<line x1="12" y1="19" x2="12" y2="23"/>'
                f'<line x1="8" y1="23" x2="16" y2="23"/>'
                f"</svg></button>"
            )

        send_btn = (
            f'<button type="button" class="dj-mminput__btn dj-mminput__send-btn" '
            f'dj-click="{e_event}" title="Send"{disabled_attr}>'
            f'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
            f'stroke-width="2" width="18" height="18">'
            f'<line x1="22" y1="2" x2="11" y2="13"/>'
            f'<polygon points="22 2 15 22 11 13 2 9 22 2"/>'
            f"</svg></button>"
        )

        return f'<div class="{cls}">{file_btn}{voice_btn}{textarea}{send_btn}</div>'
