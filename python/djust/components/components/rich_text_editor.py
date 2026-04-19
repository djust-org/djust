"""RichTextEditor component."""

import html
from djust import Component


class RichTextEditor(Component):
    """Basic rich text editor component (contenteditable + toolbar).

    Args:
        name: form field name
        value: initial HTML content (pre-rendered, caller's responsibility)
        event: dj-input event name
        placeholder: editor placeholder
        height: CSS min-height
        label: label text"""

    def __init__(
        self,
        name: str = "content",
        value: str = "",
        event: str = "update_content",
        placeholder: str = "Start typing...",
        height: str = "200px",
        label: str = "",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            name=name,
            value=value,
            event=event,
            placeholder=placeholder,
            height=height,
            label=label,
            custom_class=custom_class,
            **kwargs,
        )
        self.name = name
        self.value = value
        self.event = event
        self.placeholder = placeholder
        self.height = height
        self.label = label
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        """Render the richtexteditor HTML."""
        cls = "rte"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"
        e_name = html.escape(self.name)
        e_event = html.escape(self.event)
        e_placeholder = html.escape(self.placeholder)
        e_height = html.escape(self.height)
        e_label = html.escape(self.label)
        label_html = f'<label class="form-label">{e_label}</label>' if self.label else ""
        return (
            f'<div class="form-group">{label_html}'
            f'<div class="{cls}">'
            f'<div class="rte-toolbar">'
            f'<button class="rte-btn" type="button" title="Bold">B</button>'
            f'<button class="rte-btn" type="button" title="Italic">I</button>'
            f'<button class="rte-btn" type="button" title="Underline">U</button>'
            f"</div>"
            f'<div class="rte-editor" contenteditable="true" '
            f'style="min-height:{e_height}" data-placeholder="{e_placeholder}" '
            f'dj-input="{e_event}">{self.value}</div>'
            f'<input type="hidden" name="{e_name}">'
            f"</div></div>"
        )
