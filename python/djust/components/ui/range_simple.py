"""
Range (Slider) component for djust.

Simple stateless range/slider input field with automatic Rust optimization.
"""

from typing import Optional
from ..base import Component

try:
    from djust._rust import RustRange

    _RUST_AVAILABLE = True
except ImportError:
    _RUST_AVAILABLE = False


class Range(Component):
    """
    Range slider input component (Bootstrap 5).

    Features:
    - Automatic Rust optimization (~1μs rendering)
    - Template fallback if Rust unavailable
    - Python fallback with f-strings
    - Label, help text support
    - Optional value display
    - Configurable min, max, step

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
        >>> volume_slider = Range(
        ...     name="volume",
        ...     label="Volume",
        ...     value=75,
        ...     min_value=0,
        ...     max_value=100,
        ...     step=5,
        ...     show_value=True
        ... )
        >>> volume_slider.render()
        '<div class="mb-3">...'
    """

    _rust_impl_class = RustRange if _RUST_AVAILABLE else None

    template = """<div class="mb-3">{% if label %}
    <label for="{{ range_id }}" class="form-label">{{ label }}{% if show_value %} <span class="badge bg-secondary">{{ value }}</span>{% endif %}</label>{% endif %}
    <input type="range"
           class="form-range"
           id="{{ range_id }}"
           name="{{ name }}"
           value="{{ value }}"
           min="{{ min_value }}"
           max="{{ max_value }}"
           step="{{ step }}"{% if disabled %} disabled{% endif %}>{% if help_text %}
    <div class="form-text">{{ help_text }}</div>{% endif %}
</div>"""

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

        # Pass kwargs to parent to create Rust instance
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

        # Set instance attributes for Python/hybrid rendering
        self.name = name
        self.range_id = range_id
        self.label = label
        self.value = value
        self.min_value = min_value
        self.max_value = max_value
        self.step = step
        self.show_value = show_value
        self.help_text = help_text
        self.disabled = disabled

    def get_context_data(self):
        """Return context for template rendering."""
        return {
            "name": self.name,
            "range_id": self.range_id,
            "label": self.label,
            "value": self.value,
            "min_value": self.min_value,
            "max_value": self.max_value,
            "step": self.step,
            "show_value": self.show_value,
            "help_text": self.help_text,
            "disabled": self.disabled,
        }

    def _render_custom(self) -> str:
        """Pure Python fallback (f-string rendering)."""
        parts = ['<div class="mb-3">']

        # Label
        if self.label:
            parts.append(f'    <label for="{self.range_id}" class="form-label">{self.label}')
            if self.show_value:
                parts[-1] += f' <span class="badge bg-secondary">{self.value}</span>'
            parts[-1] += "</label>"

        # Build range attributes
        attrs = [
            'type="range"',
            'class="form-range"',
            f'id="{self.range_id}"',
            f'name="{self.name}"',
            f'value="{self.value}"',
            f'min="{self.min_value}"',
            f'max="{self.max_value}"',
            f'step="{self.step}"',
        ]
        if self.disabled:
            attrs.append("disabled")

        parts.append(f"    <input {' '.join(attrs)}>")

        # Help text
        if self.help_text:
            parts.append(f'    <div class="form-text">{self.help_text}</div>')

        parts.append("</div>")

        return "\n".join(parts)
