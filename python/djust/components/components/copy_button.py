"""CopyButton component."""

import html
from djust import Component


class CopyButton(Component):
    """Copy-to-clipboard button component.

    Args:
        text: text to copy
        label: button label
        copied_label: label shown after copying
        variant: button style variant
        size: button size"""

    def __init__(
        self,
        text: str = "",
        label: str = "Copy",
        copied_label: str = "Copied!",
        variant: str = "outline",
        size: str = "sm",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            text=text,
            label=label,
            copied_label=copied_label,
            variant=variant,
            size=size,
            custom_class=custom_class,
            **kwargs,
        )
        self.text = text
        self.label = label
        self.copied_label = copied_label
        self.variant = variant
        self.size = size
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        """Render the copybutton HTML."""
        e_text = html.escape(self.text)
        e_label = html.escape(self.label)
        e_variant = html.escape(self.variant)
        e_size = html.escape(self.size)
        cls = f"btn btn-{e_variant} btn-{e_size} copy-btn"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"
        return f'<button class="{cls}" data-copy-text="{e_text}">{e_label}</button>'
