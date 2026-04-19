"""Rich Select component for programmatic use in LiveViews."""

import html
from typing import Optional

from djust import Component


class RichSelect(Component):
    """Select dropdown where each option can include icons, images, descriptions,
    or badges alongside the label.

    Usage in a LiveView::

        self.assignee = RichSelect(
            name="assignee",
            options=[
                {"value": "alice", "label": "Alice", "icon": "A", "description": "Engineering"},
                {"value": "bob", "label": "Bob", "badge": "Admin"},
            ],
            value="alice",
            event="select_assignee",
        )

    In template::

        {{ assignee|safe }}

    Args:
        name: form field name
        options: list of dicts with keys: value, label, and optional icon, image,
                 description, badge
        value: currently selected value
        event: dj-click event name for selection
        placeholder: text shown when nothing is selected
        disabled: disables the control
        searchable: adds search input to filter options
        label: optional label text
    """

    def __init__(
        self,
        name: str = "",
        options: Optional[list] = None,
        value: str = "",
        event: str = "",
        placeholder: str = "Select...",
        disabled: bool = False,
        searchable: bool = False,
        label: str = "",
        **kwargs,
    ):
        super().__init__(
            name=name,
            options=options,
            value=value,
            event=event,
            placeholder=placeholder,
            disabled=disabled,
            searchable=searchable,
            label=label,
            **kwargs,
        )
        self.name = name
        self.options = options or []
        self.value = str(value) if value else ""
        self.event = event
        self.placeholder = placeholder
        self.disabled = disabled
        self.searchable = searchable
        self.label = label

    def _render_custom(self) -> str:
        """Render the rich select HTML."""
        e_name = html.escape(self.name)
        e_placeholder = html.escape(self.placeholder)
        dj_event = html.escape(self.event or self.name)
        disabled_attr = " disabled" if self.disabled else ""
        disabled_cls = " rich-select--disabled" if self.disabled else ""

        # Find selected option
        selected_opt = None
        for opt in self.options:
            if isinstance(opt, dict) and str(opt.get("value", "")) == self.value:
                selected_opt = opt
                break

        if selected_opt:
            selected_html = self._option_html(selected_opt)
        else:
            selected_html = f'<span class="rich-select-placeholder">{e_placeholder}</span>'

        # Build option list
        opt_parts = []
        for opt in self.options:
            if not isinstance(opt, dict):
                continue
            ov = str(opt.get("value", ""))
            active_cls = " rich-select-option--active" if ov == self.value else ""
            opt_html = self._option_html(opt)
            opt_parts.append(
                f'<div class="rich-select-option{active_cls}" '
                f'data-value="{html.escape(ov)}" '
                f'dj-click="{dj_event}" '
                f'role="option" aria-selected="{"true" if ov == self.value else "false"}">'
                f"{opt_html}"
                f"</div>"
            )

        label_html = (
            f'<label class="form-label">{html.escape(self.label)}</label>' if self.label else ""
        )

        return (
            f'<div class="rich-select{disabled_cls}">'
            f"{label_html}"
            f'<input type="hidden" name="{e_name}" value="{html.escape(self.value)}">'
            f'<div class="rich-select-trigger" tabindex="0" role="combobox" '
            f'aria-expanded="false" aria-haspopup="listbox"{disabled_attr}>'
            f"{selected_html}"
            f'<span class="rich-select-chevron">&#9662;</span>'
            f"</div>"
            f'<div class="rich-select-dropdown" role="listbox">'
            f"{''.join(opt_parts)}"
            f"</div>"
            f"</div>"
        )

    @staticmethod
    def _option_html(opt):
        """Render inner HTML for an option."""
        parts = []
        icon = opt.get("icon", "")
        image = opt.get("image", "")
        label = html.escape(str(opt.get("label", "")))
        description = opt.get("description", "")
        badge_text = opt.get("badge", "")

        if image:
            parts.append(
                f'<img class="rich-select-option-image" src="{html.escape(str(image))}" alt="">'
            )
        elif icon:
            parts.append(f'<span class="rich-select-option-icon">{html.escape(str(icon))}</span>')

        text_parts = [f'<span class="rich-select-option-label">{label}</span>']
        if description:
            text_parts.append(
                f'<span class="rich-select-option-desc">{html.escape(str(description))}</span>'
            )

        parts.append(f'<span class="rich-select-option-text">{"".join(text_parts)}</span>')

        if badge_text:
            parts.append(
                f'<span class="rich-select-option-badge">{html.escape(str(badge_text))}</span>'
            )

        return "".join(parts)
