"""Markdown Editor component — split-pane editor with live preview."""

import html
from django.utils.html import conditional_escape

from djust import Component
from typing import Any

_OFF = frozenset({"false", "0", "no", "off"})


def menu_flag(value: object, default: bool) -> bool:
    """Read a ``bubble_menu`` / ``floating_menu`` option from any renderer.

    ``None`` and ``""`` (an unset template variable) keep the default; the
    strings ``"false"``, ``"0"``, ``"no"`` and ``"off"`` (any case) are off,
    so a template literal ``bubble_menu="false"`` means what it says.
    """
    if value is None or value == "":
        return default
    if isinstance(value, str):
        return value.strip().lower() not in _OFF
    return bool(value)


def menu_attrs(bubble_menu: object = True, floating_menu: object = False) -> str:
    """The hook's menu switches as host attributes (#3108).

    Both values are fixed literals, never caller text, so nothing here needs
    escaping; the three renderers (component, Django tag, Rust tag handler)
    share this so they cannot drift.
    """
    bubble = "true" if menu_flag(bubble_menu, True) else "false"
    floating = "true" if menu_flag(floating_menu, False) else "false"
    return f' data-bubble-menu="{bubble}" data-floating-menu="{floating}"'


class MarkdownEditor(Component):
    """Split-pane markdown editor with live preview.

    Uses ``dj-hook="MarkdownEditor"`` for selection-aware formatting. Preview
    rendering uses the native sanitized server-side Markdown renderer. Load
    ``djust_components/markdown-editor.js`` after the djust client.
    Bind ``event`` and update ``value`` to refresh the preview through LiveView.

    Usage in a LiveView::

        self.editor = MarkdownEditor(name="content", preview=True)

    In template::

        {{ editor|safe }}

    CSS Custom Properties (``components.css`` and ``markdown-editor.css``)::

        --dj-md-editor-bg: background color
        --dj-md-editor-text: text color (visual mode)
        --dj-md-editor-border: border color
        --dj-md-editor-muted: secondary text (visual mode)
        --dj-md-editor-accent: accent color (visual mode)
        --dj-md-editor-code-bg: code background (visual mode)
        --dj-md-editor-radius: border radius
        --dj-md-editor-toolbar-bg: toolbar background
        --dj-md-editor-min-height: minimum height of the source/preview
            panes container, ``.dj-md-editor__panes`` (default 16rem;
            components.css)
        --dj-md-editor-height: size of the editing surface itself, the
            textarea and the visual-mode surface (default 20rem;
            markdown-editor.css). It sets height, min-height and max-height
            at once, so it is a fixed size, not a minimum: longer content
            scrolls inside it. Set this one to make the editor taller.

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
        mode: initial editing mode; visual requires the optional visual bundle
        bubble_menu: show a formatting menu over a selection in visual mode
            (default True). It never replaces the browser's context menu.
        floating_menu: show a block menu on an empty line in visual mode
            (default False)
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
        mode: str = "markdown",
        bubble_menu: bool = True,
        floating_menu: bool = False,
        **kwargs: Any,
    ) -> None:
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
            mode=mode,
            bubble_menu=bubble_menu,
            floating_menu=floating_menu,
            **kwargs,
        )
        if mode not in {"markdown", "visual"}:
            raise ValueError("mode must be markdown or visual")
        self.mode = mode
        self.name = name
        self.value = value
        self.preview = preview
        self.toolbar = toolbar
        self.placeholder = placeholder
        self.rows = rows
        self.disabled = disabled
        self.event = event
        self.custom_class = custom_class
        self.bubble_menu = menu_flag(bubble_menu, True)
        self.floating_menu = menu_flag(floating_menu, False)

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
        ea = self.event_attrs(self.event, trigger="input")
        event_attr = f" {ea}" if ea else ""

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
            toolbar_html = f'<div class="dj-md-editor__toolbar" data-markdown-ui dj-update="ignore">{"".join(btns)}</div>'

        textarea_html = (
            f'<textarea class="dj-md-editor__textarea" name="{e_name}" data-markdown-editor="{self.mode}" '
            f'placeholder="{e_placeholder}" rows="{conditional_escape(self.rows)}"'
            f"{disabled_attr}{event_attr}>{e_value}</textarea>"
        )

        preview_html = ""
        if self.preview:
            from djust.markdown import render_markdown

            preview_html = (
                '<div class="dj-md-editor__preview dj-prose" aria-label="Preview">'
                + str(render_markdown(self.value, provisional=False, task_lists=True))
                + "</div>"
            )

        panes = f'<div class="dj-md-editor__panes">{textarea_html}{preview_html}</div>'

        menus = menu_attrs(self.bubble_menu, self.floating_menu)
        return f'<div class="{class_str}" dj-hook="MarkdownEditor" data-mode="{self.mode}"{menus}>{toolbar_html}{panes}</div>'
