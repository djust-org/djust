"""NumberStepper component."""

import html
from djust import Component


class NumberStepper(Component):
    """Numeric +/- stepper input component.

    Args:
        name: form field name
        value: current value
        min_val: minimum value
        max_val: maximum value
        step: increment amount
        event: dj-click event name
        label: label text"""

    def __init__(
        self,
        name: str = "",
        value: int = 0,
        min_val: int = None,
        max_val: int = None,
        step: int = 1,
        event: str = "",
        label: str = "",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            name=name,
            value=value,
            min_val=min_val,
            max_val=max_val,
            step=step,
            event=event,
            label=label,
            custom_class=custom_class,
            **kwargs,
        )
        self.name = name
        self.value = value
        self.min_val = min_val
        self.max_val = max_val
        self.step = step
        self.event = event
        self.label = label
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        """Render the numberstepper HTML."""
        cls = "number-stepper"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"
        e_name = html.escape(self.name)
        e_label = html.escape(self.label)
        dj_event = html.escape(self.event or self.name)
        label_html = (
            f'<label class="form-label" for="{e_name}">{e_label}</label>' if self.label else ""
        )
        min_attr = f' min="{self.min_val}"' if self.min_val is not None else ""
        max_attr = f' max="{self.max_val}"' if self.max_val is not None else ""
        return (
            f'<div class="{cls}">{label_html}'
            f'<div class="number-stepper-controls">'
            f'<button type="button" class="number-stepper-btn number-stepper-dec" '
            f'dj-click="{dj_event}" data-value="dec">&minus;</button>'
            f'<input type="number" class="number-stepper-input" name="{e_name}" '
            f'value="{self.value}" step="{self.step}"{min_attr}{max_attr} dj-change="{dj_event}">'
            f'<button type="button" class="number-stepper-btn number-stepper-inc" '
            f'dj-click="{dj_event}" data-value="inc">&plus;</button>'
            f"</div></div>"
        )
