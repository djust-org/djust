"""MarkdownTextarea component for textarea with live markdown preview toggle."""

import html

from djust import Component


class MarkdownTextarea(Component):
    """Style-agnostic textarea with markdown preview toggle.

    Provides a textarea input with a "Preview" toggle button that switches
    between editing and previewing rendered markdown. The preview rendering
    is handled client-side via a ``dj-hook``.

    Usage in a LiveView::

        self.editor = MarkdownTextarea(name="content")

        # With initial content and preview enabled
        self.editor = MarkdownTextarea(
            name="body",
            value="# Hello\\nSome **bold** text",
            preview=True,
        )

    In template::

        {{ editor|safe }}

    CSS Custom Properties::

        --dj-md-textarea-bg: textarea background
        --dj-md-textarea-fg: textarea text color
        --dj-md-textarea-border: border color
        --dj-md-textarea-radius: border radius (default: 0.375rem)
        --dj-md-textarea-min-height: minimum height (default: 10rem)
        --dj-md-textarea-preview-bg: preview area background
        --dj-md-textarea-toolbar-bg: toolbar background
        --dj-md-textarea-toolbar-border: toolbar border color

    Args:
        name: Form field name
        value: Current textarea content
        preview: Whether preview mode is active (default: False)
        toggle_event: djust event to toggle preview mode
        placeholder: Placeholder text
        rows: Number of textarea rows (default: 6)
        disabled: Whether the textarea is disabled
        custom_class: Additional CSS classes
    """

    def __init__(
        self,
        name: str = "content",
        value: str = "",
        preview: bool = False,
        toggle_event: str = "toggle_preview",
        placeholder: str = "Write markdown here...",
        rows: int = 6,
        disabled: bool = False,
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            name=name,
            value=value,
            preview=preview,
            toggle_event=toggle_event,
            placeholder=placeholder,
            rows=rows,
            disabled=disabled,
            custom_class=custom_class,
            **kwargs,
        )
        self.name = name
        self.value = value
        self.preview = preview
        self.toggle_event = toggle_event
        self.placeholder = placeholder
        self.rows = rows
        self.disabled = disabled
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        cls = "dj-md-textarea"
        if self.preview:
            cls += " dj-md-textarea--preview"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"

        e_name = html.escape(self.name)
        e_value = html.escape(self.value)
        e_event = html.escape(self.toggle_event)
        e_placeholder = html.escape(self.placeholder)
        disabled_attr = " disabled" if self.disabled else ""
        rows = int(self.rows)

        # Toolbar with Write / Preview tabs
        write_active = "" if self.preview else " dj-md-textarea__tab--active"
        preview_active = " dj-md-textarea__tab--active" if self.preview else ""

        toolbar = (
            f'<div class="dj-md-textarea__toolbar">'
            f'<button type="button" class="dj-md-textarea__tab{write_active}" '
            f'dj-click="{e_event}" data-mode="write">Write</button>'
            f'<button type="button" class="dj-md-textarea__tab{preview_active}" '
            f'dj-click="{e_event}" data-mode="preview">Preview</button>'
            f"</div>"
        )

        if self.preview:
            # Preview pane — content rendered client-side by dj-hook
            body = (
                f'<div class="dj-md-textarea__preview" data-raw="{e_value}">'
                f"{e_value}</div>"
                f'<input type="hidden" name="{e_name}" value="{e_value}">'
            )
        else:
            body = (
                f'<textarea class="dj-md-textarea__input" name="{e_name}" '
                f'rows="{rows}" placeholder="{e_placeholder}"{disabled_attr}>'
                f"{e_value}</textarea>"
            )

        return f'<div class="{cls}" dj-hook="MarkdownTextarea">{toolbar}{body}</div>'
