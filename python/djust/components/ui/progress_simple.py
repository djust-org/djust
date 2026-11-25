"""
Progress component for djust.

Simple stateless progress bar (Bootstrap 5).

One renderer (#4002): ``_render_custom`` returns the markup of ``ProgressComponent``, built from
this class's arguments (the mapping is in the class docstring), so the widget has one renderer.
"""

from typing import Optional

from ..base import Component

from djust.components.utils import safe_css_length

from ._twin import plain_text, render_twin
from .progress import ProgressComponent


class Progress(Component):
    """
    Progress bar component, drawn by ``ProgressComponent``.

    Args:
        value: Progress value (clamped to ``min_value``..``max_value``)
        variant: Color variant (primary, success, info, warning, danger)
        striped: Whether to show striped pattern
        animated: Whether to animate stripes (requires striped=True)
        show_label: Whether to show percentage label
        label: Custom label text (``ProgressComponent``'s ``custom_label``; overrides the
            percentage; escaped, whatever it is marked as). An empty label counts as none.
        height: Custom height (e.g., "20px", "2rem"); anything but a CSS length is dropped
        min_value: Minimum value (default: 0)
        max_value: Maximum value (default: 100)

    The fill is a ``dj-progress-w-N`` class plus, unless ``LIVEVIEW_CONFIG["csp_strict"]``
    is on, its inline width; the height is written the same way (#3979).

    Example:
        >>> progress = Progress(value=75, variant="success", show_label=True)
        >>> progress.render()
        '<div class="progress" id="progress" ...><div class="progress-bar bg-success dj-progress-w-75" ...'
    """

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
        # ``height`` is written into a style attribute, so only a CSS length.
        height = safe_css_length(height, "") or None
        clamped_value = max(min_value, min(value, max_value))

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

        # Declared for type checkers; the base class already set each from the kwargs.
        self.value = clamped_value
        self.variant = variant
        self.striped = striped
        self.animated = animated
        self.show_label = show_label
        self.label = label
        self.height = height
        self.min_value = min_value
        self.max_value = max_value

        range_val = max_value - min_value
        self.percentage = ((self.value - min_value) / range_val * 100) if range_val > 0 else 0

        if label:
            self.label_text = label
        elif show_label:
            self.label_text = f"{int(self.percentage)}%"
        else:
            self.label_text = ""

    def _render_custom(self) -> str:
        return render_twin(
            ProgressComponent,
            self.id,
            value=self.value,
            min_value=self.min_value,
            max_value=self.max_value,
            variant=self.variant,
            striped=bool(self.striped),
            animated=bool(self.animated),
            show_label=bool(self.show_label),
            custom_label=plain_text(self.label) if self.label is not None else None,
            height=self.height,
        )
