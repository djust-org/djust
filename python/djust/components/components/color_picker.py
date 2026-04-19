"""ColorPicker component."""

import html

from djust import Component


class ColorPicker(Component):
    """Color picker with swatches component.

    Args:
        name: form field name
        value: current hex color value
        event: dj-click/dj-input event name
        label: label text
        swatches: list of hex color strings"""

    def __init__(
        self,
        name: str = "",
        value: str = "#3B82F6",
        event: str = "",
        label: str = "",
        swatches: list = None,
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            name=name,
            value=value,
            event=event,
            label=label,
            swatches=swatches,
            custom_class=custom_class,
            **kwargs,
        )
        self.name = name
        self.value = value
        self.event = event
        self.label = label
        self.swatches = swatches or []
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        """Render the colorpicker HTML."""
        swatches = self.swatches or [
            "#EF4444",
            "#F97316",
            "#EAB308",
            "#22C55E",
            "#3B82F6",
            "#8B5CF6",
            "#EC4899",
            "#6B7280",
        ]
        cls = "color-picker"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"
        e_name = html.escape(self.name)
        e_value = html.escape(self.value)
        e_event = html.escape(self.event or self.name)
        e_label = html.escape(self.label)
        label_html = f'<label class="form-label">{e_label}</label>' if self.label else ""
        swatch_html = ""
        for sw in swatches:
            e_sw = html.escape(sw)
            active_cls = " color-swatch-active" if sw == self.value else ""
            swatch_html += f'<button class="color-swatch{active_cls}" style="background:{e_sw}" dj-click="{e_event}" data-value="{e_sw}"></button>'
        return (
            f'<div class="form-group">{label_html}'
            f'<div class="{cls}">'
            f'<div class="color-preview" style="background:{e_value}"></div>'
            f'<div class="color-swatches">{swatch_html}</div>'
            f'<input class="color-hex-input form-input" type="text" '
            f'name="{e_name}" value="{e_value}" dj-input="{e_event}">'
            f"</div></div>"
        )
