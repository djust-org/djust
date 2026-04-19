"""Signature Pad component — canvas-based signature capture."""

import html

from djust import Component


class SignaturePad(Component):
    """Canvas-based signature capture pad.

    Uses ``dj-hook="SignaturePad"`` for client-side drawing.
    Fires a server event with the signature data (base64 PNG) on save.

    Usage in a LiveView::

        self.sig = SignaturePad(
            name="signature",
            save_event="save_signature",
        )

    In template::

        {{ sig|safe }}

    Args:
        name: form field name for the hidden input
        save_event: djust event fired on save with base64 data
        width: canvas width (default 400)
        height: canvas height (default 200)
        pen_color: stroke color (default "#000000")
        pen_width: stroke width in px (default 2)
        disabled: disable drawing (default False)
        custom_class: additional CSS classes
    """

    def __init__(
        self,
        name: str = "signature",
        save_event: str = "save_signature",
        width: int = 400,
        height: int = 200,
        pen_color: str = "#000000",
        pen_width: int = 2,
        disabled: bool = False,
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            name=name,
            save_event=save_event,
            width=width,
            height=height,
            pen_color=pen_color,
            pen_width=pen_width,
            disabled=disabled,
            custom_class=custom_class,
            **kwargs,
        )
        self.name = name
        self.save_event = save_event
        self.width = width
        self.height = height
        self.pen_color = pen_color
        self.pen_width = pen_width
        self.disabled = disabled
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        classes = ["dj-signature-pad"]
        if self.disabled:
            classes.append("dj-signature-pad--disabled")
        if self.custom_class:
            classes.append(html.escape(self.custom_class))
        class_str = " ".join(classes)

        e_name = html.escape(self.name)
        e_event = html.escape(self.save_event)
        e_color = html.escape(self.pen_color)

        disabled_attr = " disabled" if self.disabled else ""

        return (
            f'<div class="{class_str}" dj-hook="SignaturePad" '
            f'data-save-event="{e_event}" '
            f'data-pen-color="{e_color}" '
            f'data-pen-width="{int(self.pen_width)}">'
            f'<canvas class="dj-signature-pad__canvas" '
            f'width="{int(self.width)}" height="{int(self.height)}"'
            f"{disabled_attr}></canvas>"
            f'<input type="hidden" name="{e_name}" class="dj-signature-pad__value">'
            f'<div class="dj-signature-pad__actions">'
            f'<button class="dj-signature-pad__clear-btn" type="button">Clear</button>'
            f'<button class="dj-signature-pad__save-btn" type="button"'
            f"{disabled_attr}>Save</button>"
            f"</div>"
            f"</div>"
        )
