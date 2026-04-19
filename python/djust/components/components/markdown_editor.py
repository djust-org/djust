"""Markdown Editor component — split-pane editor with live preview."""

import html

from djust import Component


class MarkdownEditor(Component):
    """Split-pane markdown editor with live preview.

    Uses ``dj-hook="MarkdownEditor"`` for client-side preview rendering.

    Usage in a LiveView::

        self.editor = MarkdownEditor(name="content", preview=True)

    In template::

        {{ editor|safe }}

    CSS Custom Properties::

        --dj-md-editor-bg: background color
        --dj-md-editor-border: border color
        --dj-md-editor-radius: border radius
        --dj-md-editor-min-height: minimum height
        --dj-md-editor-toolbar-bg: toolbar background

    Args:
        name: form field name
        value: initial markdown content
        preview: show preview pane (default True)
        toolbar: show formatting toolbar (default True)
        placeholder: textarea placeholder text
        rows: textarea rows
        disabled: disable editing
        event: djust event on change
        custom_class: additional CSS classes
    """

    TOOLBAR_BUTTONS = [
        ("bold", "B", "**", "**"),
        ("italic", "I", "_", "_"),
        ("code", "</>", "`", "`"),
        ("link", "Link", "[", "](url)"),
        ("heading", "H", "## ", ""),
    ]

    def __init__(
        self,
        name: str = "content",
        value: str = "",
        preview: bool = True,
        toolbar: bool = True,
        placeholder: str = "Write markdown...",
        rows: int = 12,
        disabled: bool = False,
        event: str = "",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            name=name,
            value=value,
            preview=preview,
            toolbar=toolbar,
            placeholder=placeholder,
            rows=rows,
            disabled=disabled,
            event=event,
            custom_class=custom_class,
            **kwargs,
        )
        self.name = name
        self.value = value
        self.preview = preview
        self.toolbar = toolbar
        self.placeholder = placeholder
        self.rows = rows
        self.disabled = disabled
        self.event = event
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        classes = ["dj-md-editor"]
        if self.preview:
            classes.append("dj-md-editor--split")
        if self.disabled:
            classes.append("dj-md-editor--disabled")
        if self.custom_class:
            classes.append(html.escape(self.custom_class))
        class_str = " ".join(classes)

        e_name = html.escape(self.name)
        e_value = html.escape(self.value)
        e_placeholder = html.escape(self.placeholder)

        disabled_attr = " disabled" if self.disabled else ""
        event_attr = ""
        if self.event:
            e_event = html.escape(self.event)
            event_attr = f' dj-input="{e_event}"'

        toolbar_html = ""
        if self.toolbar:
            btns = []
            for btn_id, label, prefix, suffix in self.TOOLBAR_BUTTONS:
                btns.append(
                    f'<button type="button" class="dj-md-editor__btn" '
                    f'data-action="{btn_id}" data-prefix="{html.escape(prefix)}" '
                    f'data-suffix="{html.escape(suffix)}" '
                    f'aria-label="{btn_id.title()}">{label}</button>'
                )
            toolbar_html = f'<div class="dj-md-editor__toolbar">{"".join(btns)}</div>'

        textarea_html = (
            f'<textarea class="dj-md-editor__textarea" name="{e_name}" '
            f'placeholder="{e_placeholder}" rows="{self.rows}"'
            f"{disabled_attr}{event_attr}>{e_value}</textarea>"
        )

        preview_html = ""
        if self.preview:
            preview_html = '<div class="dj-md-editor__preview" aria-label="Preview"></div>'

        panes = f'<div class="dj-md-editor__panes">{textarea_html}{preview_html}</div>'

        return f'<div class="{class_str}" dj-hook="MarkdownEditor">{toolbar_html}{panes}</div>'
