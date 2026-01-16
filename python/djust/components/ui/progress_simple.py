"""
Progress component for djust.

Simple stateless progress bar with automatic Rust optimization.
"""

from typing import Optional
from ..base import Component

try:
    from djust._rust import RustProgress

    _RUST_AVAILABLE = True
except ImportError:
    _RUST_AVAILABLE = False


class Progress(Component):
    """
    Progress bar component (Bootstrap 5).

    Features:
    - Automatic Rust optimization (~1μs rendering)
    - Template fallback if Rust unavailable
    - Python fallback with f-strings

    Args:
        value: Progress value (0-100)
        variant: Color variant (primary, success, info, warning, danger)
        striped: Whether to show striped pattern
        animated: Whether to animate stripes (requires striped=True)
        show_label: Whether to show percentage label
        label: Custom label text (overrides percentage)
        height: Custom height (e.g., "20px", "2rem")
        min_value: Minimum value (default: 0)
        max_value: Maximum value (default: 100)

    Example:
        >>> progress = Progress(value=75, variant="success", show_label=True)
        >>> progress.render()
        '<div class="progress">...'
    """

    _rust_impl_class = RustProgress if _RUST_AVAILABLE else None

    template = """<div class="progress"{% if height %} style="height: {{ height }}"{% endif %}>
    <div class="progress-bar{% if striped %} progress-bar-striped{% endif %}{% if animated %} progress-bar-animated{% endif %} bg-{{ variant }}"
         role="progressbar"
         style="width: {{ percentage }}%"
         aria-valuenow="{{ value }}"
         aria-valuemin="{{ min_value }}"
         aria-valuemax="{{ max_value }}">{% if show_label or label %}{{ label_text }}{% endif %}</div>
</div>"""

    def __init__(
        self,
        value: float,
        variant: str = "primary",
        striped: bool = False,
        animated: bool = False,
        show_label: bool = False,
        label: Optional[str] = None,
        height: Optional[str] = None,
        min_value: float = 0,
        max_value: float = 100,
    ):
        # Clamp value and store parameters
        clamped_value = max(min_value, min(value, max_value))

        # Pass kwargs to parent to create Rust instance
        super().__init__(
            value=clamped_value,
            variant=variant,
            striped=striped,
            animated=animated,
            show_label=show_label,
            label=label,
            height=height,
            min_value=min_value,
            max_value=max_value,
        )

        # Set instance attributes for Python/hybrid rendering
        self.value = clamped_value
        self.variant = variant
        self.striped = striped
        self.animated = animated
        self.show_label = show_label
        self.label = label
        self.height = height
        self.min_value = min_value
        self.max_value = max_value

        # Calculate percentage
        range_val = max_value - min_value
        self.percentage = ((self.value - min_value) / range_val * 100) if range_val > 0 else 0

        # Determine label text
        if label:
            self.label_text = label
        elif show_label:
            self.label_text = f"{int(self.percentage)}%"
        else:
            self.label_text = ""

    def get_context_data(self):
        """Return context for template rendering."""
        return {
            "value": self.value,
            "variant": self.variant,
            "striped": self.striped,
            "animated": self.animated,
            "show_label": self.show_label,
            "label": self.label,
            "height": self.height,
            "min_value": self.min_value,
            "max_value": self.max_value,
            "percentage": self.percentage,
            "label_text": self.label_text,
        }

    def _render_custom(self) -> str:
        """Pure Python fallback (f-string rendering)."""
        # Build progress bar classes
        classes = ["progress-bar"]
        if self.striped:
            classes.append("progress-bar-striped")
        if self.animated:
            classes.append("progress-bar-animated")
        classes.append(f"bg-{self.variant}")

        # Build outer style
        outer_style = f' style="height: {self.height}"' if self.height else ""

        # Build label
        label_html = self.label_text if (self.show_label or self.label) else ""

        return f"""<div class="progress"{outer_style}>
    <div class="{" ".join(classes)}"
         role="progressbar"
         style="width: {self.percentage:.1f}%"
         aria-valuenow="{self.value}"
         aria-valuemin="{self.min_value}"
         aria-valuemax="{self.max_value}">{label_html}</div>
</div>"""
