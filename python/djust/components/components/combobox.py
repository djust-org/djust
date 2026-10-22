"""Combobox component."""

import html

from djust import Component
from typing import Any, Optional


class Combobox(Component):
    """Searchable select (combobox) component.

    Args:
        name: form field name
        label: label text
        value: currently selected value
        options: list of dicts with keys: value, label
        event: dj-change event name
        search_event: dj-input event for the search text; without one the
            client filters the options
        placeholder: search input placeholder"""

    #: ADR-033 D3: the walked state keys; ``options`` (the data) compares by
    #: identity, so reassign it to re-render — never a per-node walk per click.
    fingerprint_fields = ()

    def __init__(
        self,
        name: str = "",
        label: str = "",
        value: str = "",
        options: Optional[list] = None,
        event: str = "",
        search_event: str = "",
        placeholder: str = "Search...",
        custom_class: str = "",
        describedby: str = "",
        invalid: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            name=name,
            label=label,
            value=value,
            options=options,
            event=event,
            search_event=search_event,
            placeholder=placeholder,
            custom_class=custom_class,
            describedby=describedby,
            invalid=invalid,
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
        self.describedby = describedby
        self.invalid = invalid

    def _render_custom(self) -> str:
        """Render the combobox HTML."""
        from djust.components.templatetags.djust_components import field_aria_attrs

        options = self.options or []
        cls = "combobox"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"
        e_name = html.escape(self.name)
        e_label = html.escape(self.label)
        e_value = html.escape(self.value)
        # The visible text is the selected option's label, not its value.
        shown = self.value
        for opt in options:
            if isinstance(opt, dict) and str(opt.get("value", "")) == str(self.value):
                shown = str(opt.get("label", self.value))
                break
        e_shown = html.escape(shown)
        event = self.event or self.name
        search_event = self.search_event
        filter_attr = "" if search_event else ' data-filter="client"'
        e_placeholder = html.escape(self.placeholder)
        label_html = f'<label class="form-label">{e_label}</label>' if self.label else ""
        aria = field_aria_attrs(e_name, describedby=self.describedby, invalid=self.invalid)
        options_html = ""
        for opt in options:
            if isinstance(opt, dict):
                ov = opt.get("value", "")
                ol = html.escape(str(opt.get("label", "")))
            else:
                ov = opt
                ol = html.escape(str(opt))
            is_sel = "true" if str(ov) == str(self.value) else "false"
            options_html += (
                f'<div class="combobox-option" role="option" aria-selected="{is_sel}" '
                f"{self.event_attrs(event, value=ov)}>{ol}</div>"
            )
        return (
            f'<div class="form-group">{label_html}'
            f'<div class="{cls}"{filter_attr}>'
            # The form submits the selected value from the hidden input; the
            # visible text input has no name.
            f'<input type="hidden" class="combobox-value" name="{e_name}" value="{e_value}">'
            f'<input class="combobox-input form-input" type="text" '
            f'placeholder="{e_placeholder}" value="{e_shown}" role="combobox" '
            f'aria-autocomplete="list" aria-expanded="false" aria-controls="{e_name}-listbox"'
            f"{aria} "
            f"{self.event_attrs(search_event, trigger='input')}>"
            f'<div class="combobox-dropdown" id="{e_name}-listbox" role="listbox">{options_html}</div>'
            f"</div></div>"
        )
