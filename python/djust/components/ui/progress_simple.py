"""
Progress component for djust.

Simple stateless progress bar (Bootstrap 5).

One renderer (#4002): ``_render_custom`` draws the markup the retired Rust ``RustProgress``
drew, pinned byte for byte by ``python/tests/test_ui_simple_golden_4002.py``.
"""

from typing import Optional

from ..base import Component

from djust.components.utils import attr_text, csp_strict, progress_fill, safe_css_length

from ._number import number_text


class Progress(Component):
    """
    Progress bar component (Bootstrap 5).

    Args:
        value: Progress value (clamped to ``min_value``..``max_value``)
        variant: Color variant (primary, success, info, warning, danger)
        striped: Whether to show striped pattern
        animated: Whether to animate stripes (requires striped=True)
        show_label: Whether to show percentage label
        label: Custom label text (overrides percentage; escaped)
        height: Custom height (e.g., "20px", "2rem"); anything but a CSS length is dropped
        min_value: Minimum value (default: 0)
        max_value: Maximum value (default: 100)

    The fill is a ``dj-progress-w-N`` class. By default the height and the width are also the
    inline style they have always been; with ``LIVEVIEW_CONFIG["csp_strict"]`` (a strict CSP
    refuses ``style=``) the height is a ``data-dj-style-height`` the client applies and the
    width is the class alone (#3979).

    Example:
        >>> progress = Progress(value=75, variant="success", show_label=True)
        >>> progress.render()
        '<div class="progress"><div class="progress-bar bg-success dj-progress-w-75" ...'
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
        minimum, maximum = float(self.min_value), float(self.max_value)
        value = min(max(float(self.value), minimum), maximum)
        span = maximum - minimum
        percentage = (value - minimum) / span * 100.0 if span > 0 else 0.0

        bar = "progress-bar"
        if self.striped:
            bar += " progress-bar-striped"
        if self.animated:
            bar += " progress-bar-animated"

        if self.label is not None:
            label_html = attr_text(self.label)
        elif self.show_label:
            label_html = f"{int(percentage)}%"
        else:
            label_html = ""

        fill_class = progress_fill(percentage)[0]
        if csp_strict():
            outer = (
                f' data-dj-style data-dj-style-height="{attr_text(str(self.height).strip())}"'
                if self.height is not None
                else ""
            )
            width = ""
        else:
            outer = f' style="height: {attr_text(self.height)}"' if self.height is not None else ""
            width = f' style="width: {percentage:.1f}%"'
        return (
            f'<div class="progress"{outer}><div class="{bar} bg-{attr_text(self.variant)} '
            f'{fill_class}" role="progressbar"{width}'
            f' aria-valuenow="{number_text(value)}" aria-valuemin="{number_text(minimum)}"'
            f' aria-valuemax="{number_text(maximum)}">{label_html}</div></div>'
        )
