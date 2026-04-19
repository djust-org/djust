"""OtpInput component."""

import html
from djust import Component


class OtpInput(Component):
    """One-time code input component.

    Args:
        name: form field name
        digits: number of digit boxes
        event: dj-change event name
        label: label text"""

    def __init__(
        self,
        name: str = "",
        digits: int = 6,
        event: str = "",
        label: str = "",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            name=name,
            digits=digits,
            event=event,
            label=label,
            custom_class=custom_class,
            **kwargs,
        )
        self.name = name
        self.digits = digits
        self.event = event
        self.label = label
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        """Render the otpinput HTML."""
        digits = max(1, min(12, self.digits))
        cls = "otp-input"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"
        e_name = html.escape(self.name)
        e_label = html.escape(self.label)
        dj_event = html.escape(self.event or self.name)
        label_html = f'<label class="form-label">{e_label}</label>' if self.label else ""
        boxes = "".join(
            f'<input type="text" class="otp-digit" maxlength="1" data-index="{i}">'
            for i in range(digits)
        )
        return (
            f'<div class="{cls}">{label_html}'
            f'<div class="otp-boxes">{boxes}</div>'
            f'<input type="hidden" name="{e_name}" class="otp-hidden" dj-change="{dj_event}">'
            f"</div>"
        )
