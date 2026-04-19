"""Copyable Text component — inline click-to-copy with tooltip."""

import html

from djust import Component


class CopyableText(Component):
    """Inline click-to-copy text with "Copied!" tooltip feedback.

    Usage in a LiveView::

        self.api_key = CopyableText(text="sk-abc123xyz")

    In template::

        {{ api_key|safe }}

    CSS Custom Properties::

        --dj-copyable-bg: background color
        --dj-copyable-fg: text color
        --dj-copyable-border: border color
        --dj-copyable-radius: border radius
        --dj-copyable-tooltip-bg: tooltip background
        --dj-copyable-tooltip-fg: tooltip text color

    Args:
        text: The text to display and copy
        copied_label: Label shown after copy (default "Copied!")
        custom_class: Additional CSS classes
    """

    def __init__(
        self,
        text: str = "",
        copied_label: str = "Copied!",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            text=text,
            copied_label=copied_label,
            custom_class=custom_class,
            **kwargs,
        )
        self.text = text
        self.copied_label = copied_label
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        """Render the copyable text HTML."""
        classes = ["dj-copyable-text"]
        if self.custom_class:
            classes.append(html.escape(self.custom_class))
        class_str = " ".join(classes)

        e_text = html.escape(self.text)
        e_label = html.escape(self.copied_label)

        return (
            f'<span class="{class_str}" '
            f'data-copy-text="{e_text}" '
            f'data-copied-label="{e_label}" '
            f'role="button" tabindex="0" '
            f'aria-label="Click to copy">'
            f'<span class="dj-copyable-text__value">{e_text}</span>'
            f'<span class="dj-copyable-text__tooltip" aria-hidden="true">{e_label}</span>'
            f"</span>"
        )
