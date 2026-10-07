"""Resizable Panel component — container with drag-to-resize."""

import html

from django.utils.html import conditional_escape

from djust import Component
from typing import Any


class ResizablePanel(Component):
    """Container with a drag-to-resize handle.

    Uses ``dj-hook="ResizablePanel"`` for client-side resize interactions; the
    page must include ``djust_components/resizable-panel.js``. Drag the handle
    (mouse, touch or pen), or Tab to it and use the arrow keys along the
    direction (10 px, Shift 50 px), Home / End for the smallest / largest size,
    and double-click or Enter to return to ``initial_size``. The size is
    clamped to ``min_size`` / ``max_size`` and to the container, and the
    handle is a WAI-ARIA window splitter (``aria-valuenow`` and friends); in a
    right-to-left context, where the handle is on the left edge, dragging and
    the arrow keys are mirrored. The server renders ``aria-orientation`` as the
    panel's direction; the hook corrects it to the separator's own orientation
    (a width-resizing panel has a vertical bar).

    The size is the reader's own and is not sent to the server unless
    ``resize_event`` is set. When a drag or key press ends the panel dispatches
    a bubbling ``dj-resize`` CustomEvent with ``detail: {size, direction}``
    (pixels) that page script may listen to, and, with ``resize_event``, sends
    that event to the server with ``{"size": <whole pixels>}`` after a drag or
    after keyboard resizing settles for 250 ms. Unchanged sizes do not notify.
    Outstanding size echoes preserve newer gestures and the reset target;
    an unrelated server size still takes over. The size comes
    from the browser, so it is untrusted: clamp it before storing it::

        @event_handler()
        def panel_resized(self, size: int = 0, **kwargs):
            self.sidebar_px = max(120, min(int(size), 640))  # remember it for this user

    Render the remembered size back as ``initial_size`` so the panel opens the
    way the reader left it.
    An app's own ``ResizablePanel`` hook, in ``window.djust.hooks`` or
    ``window.DjustHooks``, replaces the shipped one (a hook assigned with
    ``window.DjustHooks = {...}`` after the script also drops it; merge with
    ``Object.assign`` instead).

    Usage in a LiveView::

        self.panel = ResizablePanel(
            direction="horizontal",
            min_size="200px",
            max_size="800px",
            content=mark_safe("<p>Panel content here</p>"),
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
        resize_event: djust event sent with ``{"size": px}`` when a resize ends (optional)
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
        resize_event: str = "",
        **kwargs: Any,
    ) -> None:
        super().__init__(
            content=content,
            direction=direction,
            min_size=min_size,
            max_size=max_size,
            initial_size=initial_size,
            disabled=disabled,
            custom_class=custom_class,
            resize_event=resize_event,
            **kwargs,
        )
        self.content = content
        self.direction = direction
        self.min_size = min_size
        self.max_size = max_size
        self.initial_size = initial_size
        self.disabled = disabled
        self.custom_class = custom_class
        self.resize_event = resize_event

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
        event_attr = (
            f' data-resize-event="{html.escape(str(self.resize_event))}"'
            if self.resize_event
            else ""
        )

        return (
            f'<div class="{class_str}" dj-hook="ResizablePanel" '
            f'data-direction="{direction}" '
            f'data-min-size="{e_min}" data-max-size="{e_max}"{event_attr} '
            f"{style}{disabled_attr}>"
            f'<div class="dj-resizable-panel__content">{conditional_escape(self.content)}</div>'
            f'<div class="dj-resizable-panel__handle" role="separator" '
            f'aria-orientation="{direction}" tabindex="0">'
            f'<span class="dj-resizable-panel__handle-bar"></span>'
            f"</div>"
            f"</div>"
        )
