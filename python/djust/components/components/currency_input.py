"""Currency Input component with symbol prefix and formatting."""

import html as html_mod
from typing import Optional

from djust import Component

from djust.components.utils import CURRENCY_SYMBOLS


class CurrencyInput(Component):
    """Numeric input with currency symbol prefix and formatting hints.

    Usage in a LiveView::

        self.price = CurrencyInput(
            name="price",
            currency="USD",
            min=0,
            value="29.99",
        )

    In template::

        {{ price|safe }}

    Args:
        name: Form field name.
        currency: Currency code (USD, EUR, GBP, etc.).
        value: Current numeric value.
        label: Optional label text.
        min: Minimum value.
        max: Maximum value.
        step: Step increment (default 0.01).
        placeholder: Placeholder text.
        event: dj-input event name.
        disabled: Whether the input is disabled.
        required: Whether the field is required.
        error: Error message to display.
        custom_class: Additional CSS classes.
    """

    def __init__(
        self,
        name: str = "",
        currency: str = "USD",
        value: str = "",
        label: str = "",
        min: Optional[float] = None,
        max: Optional[float] = None,
        step: str = "0.01",
        placeholder: str = "0.00",
        event: str = "",
        disabled: bool = False,
        required: bool = False,
        error: str = "",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            name=name,
            currency=currency,
            value=value,
            label=label,
            min=min,
            max=max,
            step=step,
            placeholder=placeholder,
            event=event,
            disabled=disabled,
            required=required,
            error=error,
            custom_class=custom_class,
            **kwargs,
        )
        self.name = name
        self.currency = currency.upper()
        self.value = str(value) if value else ""
        self.label = label
        self.min = min
        self.max = max
        self.step = step
        self.placeholder = placeholder
        self.event = event or name
        self.disabled = disabled
        self.required = required
        self.error = error
        self.custom_class = custom_class

    @property
    def symbol(self):
        return CURRENCY_SYMBOLS.get(self.currency, self.currency)

    def _render_custom(self) -> str:
        e_name = html_mod.escape(self.name)
        e_currency = html_mod.escape(self.currency)
        e_symbol = html_mod.escape(self.symbol)
        e_value = html_mod.escape(self.value)
        e_event = html_mod.escape(self.event)

        cls = "dj-currency-input"
        if self.error:
            cls += " dj-currency-input--error"
        if self.custom_class:
            cls += f" {html_mod.escape(self.custom_class)}"

        disabled_attr = " disabled" if self.disabled else ""
        required_attr = " required" if self.required else ""
        min_attr = f' min="{html_mod.escape(str(self.min))}"' if self.min is not None else ""
        max_attr = f' max="{html_mod.escape(str(self.max))}"' if self.max is not None else ""

        label_html = ""
        if self.label:
            req = ' <span class="form-required">*</span>' if self.required else ""
            label_html = (
                f'<label class="form-label" for="{e_name}">'
                f"{html_mod.escape(self.label)}{req}</label>"
            )

        error_html = (
            f'<span class="form-error-message" role="alert">{html_mod.escape(self.error)}</span>'
            if self.error
            else ""
        )

        return (
            f'<div class="{cls}">'
            f"{label_html}"
            f'<div class="dj-currency-input__control">'
            f'<span class="dj-currency-input__symbol">{e_symbol}</span>'
            f'<input type="number" name="{e_name}" id="{e_name}" '
            f'value="{e_value}" placeholder="{html_mod.escape(self.placeholder)}" '
            f'step="{html_mod.escape(str(self.step))}"{min_attr}{max_attr} '
            f'data-currency="{e_currency}" '
            f'dj-input="{e_event}" '
            f'class="dj-currency-input__field"'
            f"{disabled_attr}{required_attr}>"
            f'<span class="dj-currency-input__code">{e_currency}</span>'
            f"</div>"
            f"{error_html}"
            f"</div>"
        )
