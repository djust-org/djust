"""
Switch component for djust.

Simple stateless toggle switch: a checkbox styled as a Bootstrap 5 form-switch.

One renderer (#4002): ``_render_custom`` draws the markup the retired Rust ``RustSwitch``
drew, pinned byte for byte by ``python/tests/test_ui_simple_golden_4002.py``, with one fix: an
explicit ``id`` is the input's ``id`` (the Rust instance never received it and used ``name``).
"""

from typing import Optional

from ..base import Component

from djust.components.utils import attr_text


class Switch(Component):
    """
    Switch toggle component (Bootstrap 5).

    Args:
        name: Switch name attribute
        label: Label text
        id: Switch ID (defaults to name if not provided)
        checked: Whether switch is checked/on
        disabled: Whether switch is disabled
        help_text: Help text shown below switch
        value: Switch value (default: "on")
        inline: Render inline (for use in groups)

    Example:
        >>> switch = Switch(name="notifications", label="Enable notifications", checked=True)
        >>> switch.render()
        '<div class="mb-3">...'
    """

    def __init__(
        self,
        name: str,
        label: str,
        id: Optional[str] = None,
        checked: bool = False,
        disabled: bool = False,
        help_text: Optional[str] = None,
        value: str = "on",
        inline: bool = False,
    ):
        super().__init__(
            name=name,
            label=label,
            id=id or name,  # Use name as default if id not provided
            checked=checked,
            disabled=disabled,
            help_text=help_text,
            value=value,
            inline=inline,
        )

        # Declared for type checkers; the base class already set each from the kwargs.
        self.name = name
        self.label = label
        self.checked = checked
        self.disabled = disabled
        self.help_text = help_text
        self.value = value
        self.inline = inline

    def _render_custom(self) -> str:
        switch_id = attr_text(self.id)
        inline = " form-check-inline" if self.inline else ""
        state = (" checked" if self.checked else "") + (" disabled" if self.disabled else "")
        html = (
            '<div class="mb-3">'
            f'\n    <div class="form-check form-switch{inline}">'
            f'\n        <input class="form-check-input" type="checkbox" role="switch" id="{switch_id}"'
            f' name="{attr_text(self.name)}" value="{attr_text(self.value)}"{state}>'
            f'\n        <label class="form-check-label" for="{switch_id}">'
            f"\n            {attr_text(self.label)}"
            "\n        </label>"
            "\n    </div>"
        )
        if self.help_text is not None:
            html += f'\n    <div class="form-text">{attr_text(self.help_text)}</div>'
        return html + "\n</div>"
