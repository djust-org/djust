"""Resizable Panel component — container with drag-to-resize."""

import html

from djust import Component


class ResizablePanel(Component):
    """Container with a drag-to-resize handle.

    Uses ``dj-hook="ResizablePanel"`` for client-side resize interactions.

    Usage in a LiveView::

        self.panel = ResizablePanel(
            direction="horizontal",
            min_size="200px",
            max_size="800px",
            content="<p>Panel content here</p>",
        )

    In template::

        {{ panel|safe }}

    Args:
        content: HTML content inside the panel
        direction: "horizontal" or "vertical" (default "horizontal")
        min_size: minimum size CSS value (default "100px")
        max_size: maximum size CSS value (default "none")
        initial_size: starting size CSS value (default "50%")
        disabled: disable resize (default False)
        custom_class: additional CSS classes
    """

    def __init__(
        self,
        content: str = "",
        direction: str = "horizontal",
        min_size: str = "100px",
        max_size: str = "none",
        initial_size: str = "50%",
        disabled: bool = False,
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            content=content,
            direction=direction,
            min_size=min_size,
            max_size=max_size,
            initial_size=initial_size,
            disabled=disabled,
            custom_class=custom_class,
            **kwargs,
        )
        self.content = content
        self.direction = direction
        self.min_size = min_size
        self.max_size = max_size
        self.initial_size = initial_size
        self.disabled = disabled
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        classes = ["dj-resizable-panel"]
        direction = self.direction if self.direction in ("horizontal", "vertical") else "horizontal"
        classes.append(f"dj-resizable-panel--{direction}")
        if self.disabled:
            classes.append("dj-resizable-panel--disabled")
        if self.custom_class:
            classes.append(html.escape(self.custom_class))
        class_str = " ".join(classes)

        e_min = html.escape(self.min_size)
        e_max = html.escape(self.max_size)
        e_initial = html.escape(self.initial_size)

        size_prop = "width" if direction == "horizontal" else "height"
        style = f'style="{size_prop}:{e_initial};min-{size_prop}:{e_min}"'
        if self.max_size != "none":
            style = (
                f'style="{size_prop}:{e_initial};min-{size_prop}:{e_min};max-{size_prop}:{e_max}"'
            )

        disabled_attr = ' data-disabled="true"' if self.disabled else ""

        return (
            f'<div class="{class_str}" dj-hook="ResizablePanel" '
            f'data-direction="{direction}" '
            f'data-min-size="{e_min}" data-max-size="{e_max}" '
            f"{style}{disabled_attr}>"
            f'<div class="dj-resizable-panel__content">{self.content}</div>'
            f'<div class="dj-resizable-panel__handle" role="separator" '
            f'aria-orientation="{direction}" tabindex="0">'
            f'<span class="dj-resizable-panel__handle-bar"></span>'
            f"</div>"
            f"</div>"
        )
