"""Signature Pad component — canvas-based signature capture."""

import html

from djust import Component
from typing import Any, NamedTuple

from djust.components.signature import DEFAULT_MAX_BYTES


class PadExtras(NamedTuple):
    """The additions to the markup the component always had (see :func:`pad_extras`)."""

    root: str  # attributes of the hook host
    canvas: str  # attributes of the canvas
    typed: str  # the typed-name field, between the hidden value and the actions
    undo: str  # the Undo button
    toggle: str  # the "Type instead" button
    status: str  # the live region after the actions


def pad_extras(
    max_bytes: Any = DEFAULT_MAX_BYTES, label: Any = None, typed: Any = True
) -> PadExtras:
    """The markup the hook needs beyond what the component always rendered.

    Shared by the component class, the Django tag and the Rust tag handler so the
    three cannot drift; everything caller-supplied is escaped here.
    """
    try:
        size = max(1024, int(max_bytes))
    except (ValueError, TypeError, OverflowError):
        size = DEFAULT_MAX_BYTES
    text = "Signature drawing area" if label is None or not str(label).strip() else str(label)
    e_label = html.escape(text)
    typed_html = toggle_html = ""
    offer_typed = typed not in (False, 0, "", "0", "False", "false", None)
    if offer_typed:
        typed_html = (
            '<div class="dj-signature-pad__typed" hidden>'
            '<label class="dj-signature-pad__typed-label">Type your name'
            '<input type="text" class="dj-signature-pad__typed-input" maxlength="80" '
            'autocomplete="off" spellcheck="false"></label></div>'
        )
        toggle_html = (
            '<button class="dj-signature-pad__mode-btn" type="button" '
            'aria-pressed="false">Type instead</button>'
        )
    return PadExtras(
        root=f' data-max-bytes="{size}"' + ("" if offer_typed else ' data-typed="false"'),
        canvas=f' role="img" aria-label="{e_label}"',
        typed=typed_html,
        undo='<button class="dj-signature-pad__undo-btn" type="button">Undo</button>',
        toggle=toggle_html,
        status='<div class="dj-signature-pad__status" role="status" aria-live="polite"></div>',
    )


class SignaturePad(Component):
    """Canvas-based signature capture pad.

    Uses ``dj-hook="SignaturePad"`` for client-side drawing: a native canvas
    drawn with pointer events (mouse, touch and pen, with pen pressure), Clear,
    Undo (last stroke) and Save. Load ``djust_components/signature-pad.js``
    after the djust client.

    Usage in a LiveView::

        self.sig = SignaturePad(
            name="signature",
            save_event="save_signature",
        )

    In template::

        {{ sig|safe }}

    Save sends ``save_event`` with ``{signature}``: a ``data:image/png;base64,``
    URL, at most ``max_bytes`` (default 200 KB) long (the hook re-renders at a
    lower resolution until it fits, and refuses a signature that cannot).
    Saving with nothing drawn sends nothing and says so. The same value is put
    in the hidden form field ``name`` for a regular form post.

    **The value comes from the browser: treat it as untrusted.** Do not store or
    decode it as it is. ``djust.components.signature.decode_signature_data_url``
    checks it without decoding any pixels (prefix, strict base64, size, PNG
    magic bytes, every chunk and CRC, dimensions and pixel count within limits,
    an image stream that inflates to exactly what its header promises, so a
    small file cannot claim to be a gigapixel image or a decompression bomb)
    and returns the validated bytes::

        from djust.components.signature import SignatureError, decode_signature_data_url

        @event_handler()
        def save_signature(self, signature="", **kwargs):
            try:
                image = decode_signature_data_url(signature)
            except SignatureError:
                self.error = "That signature could not be read."  # never echo the value
                return
            self.document.signature.save(f"{uuid4().hex}.png", ContentFile(image.data))

    The module docstring shows a Pillow re-encode (white background, no
    metadata) to run after that check. The default WebSocket message limit
    (``max_message_size``, 64 KiB) is smaller than ``max_bytes``: a typical
    signature is 5-30 KB, and when the server answers "Message too large" the
    hook sends the signature once more at a smaller size; raise
    ``max_message_size`` if you want the full 200 KB.

    Accessibility: the canvas is a labelled image and drawing needs a pointer,
    so there is a keyboard alternative: "Type instead" shows a text field and
    the signature is the typed name drawn in a script font (a typed signature).
    Clear, Undo, Save and the mode button are real buttons; results and
    refusals are announced in a polite status region.

    The canvas follows its container's width (the signature itself is always
    exported at the ``width`` x ``height`` you set, so the PNG does not depend on
    the screen), renders sharply on high-DPI screens, and keeps the drawing when
    the window is resized.

    Args:
        name: form field name for the hidden input
        save_event: djust event fired on save with base64 data
        width: canvas width (default 400)
        height: canvas height (default 200)
        pen_color: stroke color (default "#000000")
        pen_width: stroke width in px (default 2)
        disabled: disable drawing (default False)
        custom_class: additional CSS classes
        max_bytes: largest data URL the hook sends (default 204800)
        label: accessible name of the canvas (default "Signature drawing area")
        typed: offer the typed-name alternative (default True)
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
        max_bytes: int = DEFAULT_MAX_BYTES,
        label: str = "Signature drawing area",
        typed: bool = True,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            name=name,
            save_event=save_event,
            width=width,
            height=height,
            pen_color=pen_color,
            pen_width=pen_width,
            disabled=disabled,
            custom_class=custom_class,
            max_bytes=max_bytes,
            label=label,
            typed=typed,
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
        self.max_bytes = max_bytes
        self.label = label
        self.typed = typed

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
        extras = pad_extras(self.max_bytes, self.label, self.typed)

        return (
            f'<div class="{class_str}" dj-hook="SignaturePad" '
            f'data-save-event="{e_event}" '
            f'data-pen-color="{e_color}" '
            f'data-pen-width="{int(self.pen_width)}"{extras.root}>'
            f'<canvas class="dj-signature-pad__canvas" '
            f'width="{int(self.width)}" height="{int(self.height)}"'
            f"{disabled_attr}{extras.canvas}></canvas>"
            f'<input type="hidden" name="{e_name}" class="dj-signature-pad__value">'
            f"{extras.typed}"
            f'<div class="dj-signature-pad__actions">'
            f'<button class="dj-signature-pad__clear-btn" type="button">Clear</button>'
            f"{extras.undo}{extras.toggle}"
            f'<button class="dj-signature-pad__save-btn" type="button"'
            f"{disabled_attr}>Save</button>"
            f"</div>"
            f"{extras.status}"
            f"</div>"
        )
