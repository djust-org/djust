"""MultiSelect component."""

import html

from djust import Component


class MultiSelect(Component):
    """Multi-select checkbox list component.

    Args:
        name: form field name
        label: label text
        options: list of dicts with keys: value, label
        selected: list of currently selected values
        event: dj-change event name"""

    def __init__(
        self,
        name: str = "",
        label: str = "",
        options: list = None,
        selected: list = None,
        event: str = "",
        placeholder: str = "Search...",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            name=name,
            label=label,
            options=options,
            selected=selected,
            event=event,
            placeholder=placeholder,
            custom_class=custom_class,
            **kwargs,
        )
        self.name = name
        self.label = label
        self.options = options or []
        self.selected = selected or []
        self.event = event
        self.placeholder = placeholder
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        """Render the multiselect HTML."""
        options = self.options or []
        selected = [str(s) for s in (self.selected or [])]
        cls = "multi-select"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"
        e_name = html.escape(self.name)
        e_label = html.escape(self.label)
        dj_event = html.escape(self.event or self.name)
        e_placeholder = html.escape(self.placeholder)
        label_html = f'<label class="form-label">{e_label}</label>' if self.label else ""
        cb_parts = []
        for opt in options:
            if isinstance(opt, dict):
                ov = str(opt.get("value", ""))
                ol = str(opt.get("label", ""))
            else:
                ov = ol = str(opt)
            checked = " checked" if ov in selected else ""
            cb_parts.append(
                f'<label class="multi-select-option">'
                f'<input type="checkbox" name="{e_name}" value="{html.escape(ov)}"'
                f'{checked} dj-change="{dj_event}"> {html.escape(ol)}'
                f"</label>"
            )
        return (
            f'<div class="{cls}">{label_html}'
            f'<input type="text" class="multi-select-search" placeholder="{e_placeholder}">'
            f'<div class="multi-select-options">{"".join(cb_parts)}</div>'
            f"</div>"
        )
