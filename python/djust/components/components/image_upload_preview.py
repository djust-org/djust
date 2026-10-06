"""Image Upload Preview component — multi-image upload with thumbnails."""

import html

from djust import Component
from typing import Any, Optional


#: The two elements the hook fills (selected images; announcements). Rendered
#: empty by every render path and marked client-owned by the hook.
ITEMS_HTML = (
    '<ul class="dj-img-upload__items" aria-label="Selected images"></ul>'
    '<div class="dj-img-upload__status" role="status" aria-live="polite"></div>'
)


def upload_attrs(
    upload: Any = None, max_size: Any = 0, notify: bool = False
) -> tuple[str, str, str]:
    """``(root, zone, input)`` attributes that wire the component to djust's
    upload pipeline and tell the hook the limits to pre-check.

    Shared by the component class, the Django tag and the Rust tag handler so
    the three cannot drift. Everything is escaped here. ``upload`` is the slot
    name given to ``allow_upload()``: with it the input carries ``dj-upload``
    and the drop zone ``dj-upload-drop`` (djust's own directives, which do the
    transfer); without it the component is a plain file input with previews.
    ``notify`` is True only when the caller named an ``event``: the hook sends
    nothing for the default name, which has no handler unless the app wrote one.
    """
    slot = "" if upload is None else str(upload).strip()
    try:
        size = max(0, int(max_size))
    except (ValueError, TypeError, OverflowError):
        size = 0
    root = ' data-notify="true"' if notify else ""
    if slot:
        root += f' data-upload="{html.escape(slot)}"'
    if size:
        root += f' data-max-size="{size}"'
    if not slot:
        return root, "", ""
    e_slot = html.escape(slot)
    return root, f' dj-upload-drop="{e_slot}"', f' dj-upload="{e_slot}"'


class ImageUploadPreview(Component):
    """Multi-image upload with thumbnail preview.

    Renders a file drop zone. Chosen or dropped images show at once as
    thumbnails (browser object URLs: nothing is read into memory or sent for
    the preview) with their name and size, a progress bar and a Cancel button
    while they upload, and a Remove button. Load
    ``djust_components/image-upload-preview.js`` after the djust client.

    The files travel through djust's own upload pipeline: pass ``upload`` (the
    slot name given to ``allow_upload``) and the input carries ``dj-upload``
    and the drop zone ``dj-upload-drop``, so chunking, progress events,
    cancellation and every server-side check are exactly those of
    ``UploadMixin``. Without ``upload`` the component is a plain file input
    with previews (a regular form post carries the files).

    Usage in a LiveView::

        class PhotosView(UploadMixin, LiveView):
            def mount(self, request, **kwargs):
                self.allow_upload(
                    "photos",
                    accept="image/*",
                    max_entries=5,
                    max_file_size=5_000_000,
                )
                self.upload = ImageUploadPreview(
                    name="photos",
                    upload="photos",
                    max=5,
                    max_size=5_000_000,
                    event="photos_uploaded",
                )

            @event_handler()
            def photos_uploaded(self, count=0, **kwargs):
                for entry in self.consume_uploaded_entries("photos"):
                    path = default_storage.save(
                        f"photos/{uuid4().hex}/{entry.safe_client_name}", entry.file
                    )

    In template::

        {{ upload|safe }}

    The browser is not trusted: ``accept``, ``max`` and ``max_size`` here only
    spare the reader a wasted upload (a file that fails them is listed with the
    reason and never sent). The server decides: ``allow_upload`` enforces the
    type, size, count and magic bytes, and refuses browser-executable active
    content such as SVG unless ``allow_active_content=True``. Keep the two sets
    of limits in step; a file the server refuses is marked "not accepted".

    ``event`` fires once the files of a selection have finished uploading (the
    reader chose files and none is still in flight), with ``{count}``, the
    number that completed. With no ``upload`` slot it fires when files are
    chosen, with the number accepted. ``count`` comes from the browser: use it
    for display only and read what arrived with ``consume_uploaded_entries``.

    Previews are object URLs, revoked when an image is removed, replaced (when
    ``max`` is 1 a new choice replaces the old image) or when the component
    goes away. When the server re-renders with a different ``previews`` list
    (it saved the files), the finished local thumbnails give way to it.

    Keyboard and screen readers: the file input is a focusable control inside
    the drop zone (Enter or Space opens the picker), the thumbnails are a list
    with a labelled Remove or Cancel button each, and selections, results and
    refusals are announced.

    CSS Custom Properties::

        --dj-img-upload-bg: background (default: #f9fafb)
        --dj-img-upload-border: border color (default: #d1d5db)
        --dj-img-upload-radius: border radius (default: 0.5rem)
        --dj-img-upload-thumb-size: thumbnail size (default: 5rem)

    Args:
        name: Form field name.
        max: Maximum number of images (selected plus ``previews``).
        event: Event fired when a selection has uploaded (see above). Nothing
            is sent unless you name one.
        accept: Accepted MIME types (default: image/*).
        previews: List of existing preview URLs.
        custom_class: Additional CSS classes.
        upload: Upload slot name (``allow_upload``); ``None`` is a plain input.
        max_size: Largest file in bytes to pre-check (``0``: no pre-check).
    """

    def __init__(
        self,
        name: str = "images",
        max: int = 5,
        event: Optional[str] = None,
        accept: str = "image/*",
        previews: Optional[list] = None,
        custom_class: str = "",
        upload: Optional[str] = None,
        max_size: int = 0,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            name=name,
            max=max,
            event=event,
            accept=accept,
            previews=previews,
            custom_class=custom_class,
            upload=upload,
            max_size=max_size,
            **kwargs,
        )
        self.name = name
        self.max = max
        self.event = "upload" if event is None else event
        self._event_named = event is not None
        self.accept = accept
        self.previews = previews or []
        self.custom_class = custom_class
        self.upload = upload
        self.max_size = max_size

    def _render_custom(self) -> str:
        cls = "dj-img-upload"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"

        e_name = html.escape(self.name)
        e_event = html.escape(self.event)
        e_accept = html.escape(self.accept)

        try:
            max_count = int(self.max)
        except (ValueError, TypeError):
            max_count = 5

        thumbs = []
        for url in self.previews:
            e_url = html.escape(str(url))
            thumbs.append(
                f'<div class="dj-img-upload__thumb">'
                f'<img src="{e_url}" alt="Preview" '
                f'class="dj-img-upload__thumb-img">'
                f"</div>"
            )

        thumbs_html = ""
        if thumbs:
            thumbs_html = f'<div class="dj-img-upload__previews">{"".join(thumbs)}</div>'

        upload_svg = (
            '<svg class="dj-img-upload__icon" viewBox="0 0 24 24" width="24" '
            'height="24" fill="none" stroke="currentColor" stroke-width="2">'
            '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>'
            '<polyline points="17 8 12 3 7 8"/>'
            '<line x1="12" y1="3" x2="12" y2="15"/>'
            "</svg>"
        )

        root_attrs, zone_attrs, input_attrs = upload_attrs(
            self.upload, self.max_size, self._event_named
        )

        return (
            f'<div class="{cls}" dj-hook="ImageUploadPreview" '
            f'data-event="{e_event}" data-max="{max_count}"{root_attrs}>'
            f'<label class="dj-img-upload__dropzone"{zone_attrs}>'
            f"{upload_svg}"
            f'<span class="dj-img-upload__text">Drop images here or click to upload</span>'
            f'<span class="dj-img-upload__hint">Max {max_count} images</span>'
            f'<input type="file" name="{e_name}" accept="{e_accept}" '
            f'multiple class="dj-img-upload__input" aria-label="Upload images"{input_attrs}>'
            f"</label>"
            f"{thumbs_html}"
            f"{ITEMS_HTML}"
            f"</div>"
        )
