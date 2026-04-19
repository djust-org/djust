"""Prompt Template Editor component for editing templates with {{variable}} highlighting."""

import html
import re
from typing import Dict, Optional

from djust import Component


class PromptEditor(Component):
    """Template editing with {{variable}} highlighting.

    Renders a textarea-based editor that highlights {{variable}} placeholders
    and displays a list of detected variables.

    Usage in a LiveView::

        self.editor = PromptEditor(
            template="Hello {{name}}, welcome to {{service}}!",
            variables={"name": "Alice", "service": "djust"},
            event="save_prompt",
        )

    In template::

        {{ editor|safe }}

    CSS Custom Properties::

        --dj-prompt-editor-bg: editor background (default: #1e1e2e)
        --dj-prompt-editor-fg: text color (default: #cdd6f4)
        --dj-prompt-editor-var-bg: variable highlight background
        --dj-prompt-editor-var-fg: variable highlight color
        --dj-prompt-editor-radius: border radius (default: 0.5rem)

    Args:
        template: The template string with {{variable}} placeholders.
        variables: Dict of variable name -> value for preview.
        event: Event name for save action.
        placeholder: Placeholder text for the editor.
        rows: Number of rows for the textarea.
        custom_class: Additional CSS classes.
    """

    def __init__(
        self,
        template: str = "",
        variables: Optional[Dict[str, str]] = None,
        event: str = "save_prompt",
        placeholder: str = "Enter your prompt template...",
        rows: int = 6,
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            template=template,
            variables=variables,
            event=event,
            placeholder=placeholder,
            rows=rows,
            custom_class=custom_class,
            **kwargs,
        )
        self.template = template
        self.variables = variables or {}
        self.event = event
        self.placeholder = placeholder
        self.rows = rows
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        cls = "dj-prompt-editor"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"

        e_event = html.escape(self.event)
        e_placeholder = html.escape(self.placeholder)
        e_template = html.escape(self.template)

        try:
            rows = int(self.rows)
        except (ValueError, TypeError):
            rows = 6

        # Extract variable names from template
        var_names = re.findall(r"\{\{(\w+)\}\}", self.template)
        unique_vars = list(dict.fromkeys(var_names))  # preserve order, dedupe

        var_chips = []
        for v in unique_vars:
            e_v = html.escape(v)
            val = self.variables.get(v, "")
            e_val = html.escape(str(val))
            var_chips.append(
                f'<span class="dj-prompt-editor__var" data-var="{e_v}">'
                f"<code>{{{{{e_v}}}}}</code>"
                f"{f' = {e_val}' if val else ''}"
                f"</span>"
            )

        vars_html = ""
        if var_chips:
            vars_html = f'<div class="dj-prompt-editor__vars">{"".join(var_chips)}</div>'

        # Build highlighted preview — escape first, then substitute
        preview_text = html.escape(self.template)
        for v in unique_vars:
            val = self.variables.get(v, f"{{{{{v}}}}}")
            preview_text = preview_text.replace(
                "{{" + v + "}}",
                f'<mark class="dj-prompt-editor__highlight">{html.escape(str(val))}</mark>',
            )

        event_attr = ""
        if e_event:
            event_attr = f' dj-click="{e_event}"'

        return (
            f'<div class="{cls}">'
            f'<textarea class="dj-prompt-editor__textarea" '
            f'name="template" rows="{rows}" '
            f'placeholder="{e_placeholder}">{e_template}</textarea>'
            f"{vars_html}"
            f'<div class="dj-prompt-editor__preview">{preview_text}</div>'
            f'<button type="button" class="dj-prompt-editor__save"'
            f"{event_attr}>Save</button>"
            f"</div>"
        )
