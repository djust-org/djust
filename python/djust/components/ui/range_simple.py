"""
Range (Slider) component for djust.

Simple stateless range/slider input field (Bootstrap 5).

One renderer (#4002): ``_render_custom`` draws the markup the retired Rust ``RustRange`` drew,
pinned byte for byte by ``python/tests/test_ui_simple_golden_4002.py``, with one fix: an
explicit ``id`` is the input's ``id`` (the Rust instance never received it and used ``name``).
"""

from typing import Optional

from ..base import Component

from djust.components.utils import attr_text

from ._number import number_text


class Range(Component):
    """
    Range slider input component (Bootstrap 5).

    Args:
        name: Range input name attribute
        id: Range ID (defaults to name if not provided)
        label: Label text
        value: Initial value (default: 50)
        min_value: Minimum value (default: 0)
        max_value: Maximum value (default: 100)
        step: Step increment (default: 1)
        show_value: Whether to show current value (default: False)
        help_text: Help text shown below input
        disabled: Whether field is disabled

    Example:
        >>> volume_slider = Range(name="volume", label="Volume", value=75, step=5, show_value=True)
        >>> volume_slider.render()
        '<div class="mb-3">...'
    """

    def __init__(
        self,
        name: str,
        id: Optional[str] = None,
        label: Optional[str] = None,
        value: float = 50,
        min_value: float = 0,
        max_value: float = 100,
        step: float = 1,
        show_value: bool = False,
        help_text: Optional[str] = None,
        disabled: bool = False,
    ):
        # Use name as ID if not provided
        range_id = id or name

        super().__init__(
            name=name,
            id=range_id,
            label=label,
            value=value,
            min_value=min_value,
            max_value=max_value,
            step=step,
            show_value=show_value,
            help_text=help_text,
            disabled=disabled,
        )

        # Declared for type checkers; the base class already set each from the kwargs.
        self.name = name
        self.label = label
        self.value = value
        self.min_value = min_value
        self.max_value = max_value
        self.step = step
        self.show_value = show_value
        self.help_text = help_text
        self.disabled = disabled
        self.range_id = range_id

    def _render_custom(self) -> str:
        range_id = attr_text(self.id)
        html = '<div class="mb-3">'
        if self.label is not None:
            html += f'\n    <label for="{range_id}" class="form-label">{attr_text(self.label)}'
            if self.show_value:
                html += f' <span class="badge bg-secondary">{number_text(self.value)}</span>'
            html += "</label>"
        html += (
            '\n    <input type="range"'
            '\n           class="form-range"'
            f'\n           id="{range_id}"'
            f'\n           name="{attr_text(self.name)}"'
            f'\n           value="{number_text(self.value)}"'
            f'\n           min="{number_text(self.min_value)}"'
            f'\n           max="{number_text(self.max_value)}"'
            f'\n           step="{number_text(self.step)}"'
        )
        if self.disabled:
            html += " disabled"
        html += ">"
        if self.help_text is not None:
            html += f'\n    <div class="form-text">{attr_text(self.help_text)}</div>'
        return html + "\n</div>"
