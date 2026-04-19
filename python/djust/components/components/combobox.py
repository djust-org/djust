"""Combobox component."""

import html

from djust import Component


class Combobox(Component):
    """Searchable select (combobox) component.

    Args:
        name: form field name
        label: label text
        value: currently selected value
        options: list of dicts with keys: value, label
        event: dj-change event name
        search_event: dj-input event for search
        placeholder: search input placeholder"""

    def __init__(
        self,
        name: str = "",
        label: str = "",
        value: str = "",
        options: list = None,
        event: str = "",
        search_event: str = "",
        placeholder: str = "Search...",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            name=name,
            label=label,
            value=value,
            options=options,
            event=event,
            search_event=search_event,
            placeholder=placeholder,
            custom_class=custom_class,
            **kwargs,
        )
        self.name = name
        self.label = label
        self.value = value
        self.options = options or []
        self.event = event
        self.search_event = search_event
        self.placeholder = placeholder
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        """Render the combobox HTML."""
        options = self.options or []
        cls = "combobox"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"
        e_name = html.escape(self.name)
        e_label = html.escape(self.label)
        e_value = html.escape(self.value)
        e_event = html.escape(self.event or self.name)
        e_search = html.escape(self.search_event or (self.name + "_search"))
        e_placeholder = html.escape(self.placeholder)
        label_html = f'<label class="form-label">{e_label}</label>' if self.label else ""
        options_html = ""
        for opt in options:
            if isinstance(opt, dict):
                ov = html.escape(str(opt.get("value", "")))
                ol = html.escape(str(opt.get("label", "")))
            else:
                ov = ol = html.escape(str(opt))
            options_html += (
                f'<div class="combobox-option" dj-click="{e_event}" data-value="{ov}">{ol}</div>'
            )
        return (
            f'<div class="form-group">{label_html}'
            f'<div class="{cls}">'
            f'<input class="combobox-input form-input" type="text" name="{e_name}" '
            f'placeholder="{e_placeholder}" value="{e_value}" dj-input="{e_search}">'
            f'<div class="combobox-dropdown">{options_html}</div>'
            f"</div></div>"
        )
