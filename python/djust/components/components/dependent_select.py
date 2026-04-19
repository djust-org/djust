"""Dependent Select component for cascading dropdowns."""

import html as html_mod
from typing import Optional

from djust import Component


class DependentSelect(Component):
    """Cascading dropdown that reloads options when a parent field changes.

    Usage in a LiveView::

        self.city_select = DependentSelect(
            name="city",
            parent="country",
            source_event="load_cities",
            options=[
                {"value": "nyc", "label": "New York"},
                {"value": "la", "label": "Los Angeles"},
            ],
        )

    In template::

        {{ city_select|safe }}

    Args:
        name: Form field name.
        parent: Name of the parent field this select depends on.
        source_event: djust event to fire when parent changes.
        label: Optional label text.
        placeholder: Placeholder text when nothing selected.
        value: Currently selected value.
        options: List of dicts with 'value'/'label' keys, or list of strings.
        loading: Show spinner while loading options.
        disabled: Whether the select is disabled.
        required: Whether the field is required.
        error: Error message to display.
        custom_class: Additional CSS classes.
    """

    def __init__(
        self,
        name: str = "",
        parent: str = "",
        source_event: str = "",
        label: str = "",
        placeholder: str = "Select...",
        value: str = "",
        options: Optional[list] = None,
        loading: bool = False,
        disabled: bool = False,
        required: bool = False,
        error: str = "",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            name=name,
            parent=parent,
            source_event=source_event,
            label=label,
            placeholder=placeholder,
            value=value,
            options=options,
            loading=loading,
            disabled=disabled,
            required=required,
            error=error,
            custom_class=custom_class,
            **kwargs,
        )
        self.name = name
        self.parent = parent
        self.source_event = source_event or name
        self.label = label
        self.placeholder = placeholder
        self.value = str(value) if value else ""
        self.options = options or []
        self.loading = loading
        self.disabled = disabled
        self.required = required
        self.error = error
        self.custom_class = custom_class

    def set_options(self, options, value=""):
        """Update options (typically after parent changes)."""
        self.options = options or []
        self.value = str(value) if value else ""
        self.loading = False

    def _render_custom(self) -> str:
        e_name = html_mod.escape(self.name)
        e_parent = html_mod.escape(self.parent)
        e_event = html_mod.escape(self.source_event)

        cls = "dj-dependent-select"
        if self.loading:
            cls += " dj-dependent-select--loading"
        if self.error:
            cls += " dj-dependent-select--error"
        if self.custom_class:
            cls += f" {html_mod.escape(self.custom_class)}"

        disabled_attr = " disabled" if self.disabled else ""
        required_attr = " required" if self.required else ""

        label_html = ""
        if self.label:
            req = ' <span class="form-required">*</span>' if self.required else ""
            label_html = (
                f'<label class="form-label" for="{e_name}">'
                f"{html_mod.escape(self.label)}{req}</label>"
            )

        opt_parts = [f'<option value="">{html_mod.escape(self.placeholder)}</option>']
        for opt in self.options:
            if isinstance(opt, dict):
                ov = html_mod.escape(str(opt.get("value", "")))
                ol = html_mod.escape(str(opt.get("label", ov)))
            else:
                ov = html_mod.escape(str(opt))
                ol = ov
            selected = " selected" if ov == self.value else ""
            opt_parts.append(f'<option value="{ov}"{selected}>{ol}</option>')

        spinner_html = (
            '<span class="dj-dependent-select__spinner" aria-hidden="true"></span>'
            if self.loading
            else ""
        )

        error_html = (
            f'<span class="form-error-message" role="alert">{html_mod.escape(self.error)}</span>'
            if self.error
            else ""
        )

        return (
            f'<div class="{cls}">'
            f"{label_html}"
            f'<div class="dj-dependent-select__control">'
            f'<select name="{e_name}" id="{e_name}" '
            f'data-parent="{e_parent}" '
            f'data-source-event="{e_event}" '
            f'dj-change="{e_event}"'
            f"{disabled_attr}{required_attr}>"
            f"{''.join(opt_parts)}"
            f"</select>"
            f"{spinner_html}"
            f"</div>"
            f"{error_html}"
            f"</div>"
        )
