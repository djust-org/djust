"""MultiSelect component."""

import html

from djust import Component
from typing import Any, Optional


class MultiSelect(Component):
    """Multi-select checkbox list component.

    Args:
        name: form field name
        label: label text
        options: list of dicts with keys: value, label
        selected: list of currently selected values
        event: dj-change event name. Each box sends ``option`` (its value) and
            ``value`` (whether it is now ticked)::

                @event_handler()
                def set_frameworks(self, option, value, **kwargs):
                    picked = [s for s in self.picker.selected if s != option]
                    self.picker.selected = picked + [option] if value else picked"""

    #: ADR-033 D3: the walked state keys; ``options`` (the data) compares by
    #: identity, so reassign it to re-render — never a per-node walk per click.
    fingerprint_fields = ()

    def __init__(
        self,
        name: str = "",
        label: str = "",
        options: Optional[list] = None,
        selected: Optional[list] = None,
        event: str = "",
        placeholder: str = "Search...",
        custom_class: str = "",
        **kwargs: Any,
    ) -> None:
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
        event = self.event or self.name
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
                # `option` says WHICH box changed: a checkbox's change carries
                # only whether it is ticked.
                f"{checked} {self.event_attrs(event, trigger='change', option=ov)}>"
                f" {html.escape(ol)}"
                f"</label>"
            )
        return (
            f'<div class="{cls}">{label_html}'
            f'<input type="text" class="multi-select-search" placeholder="{e_placeholder}">'
            f'<div class="multi-select-options">{"".join(cb_parts)}</div>'
            f"</div>"
        )
