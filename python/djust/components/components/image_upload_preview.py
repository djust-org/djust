"""Image Upload Preview component — multi-image upload with thumbnails."""

import html

from djust import Component


class ImageUploadPreview(Component):
    """Multi-image upload with thumbnail preview.

    Renders a file drop zone with thumbnail previews of selected images.
    Supports max count and fires events on upload.

    Usage in a LiveView::

        self.upload = ImageUploadPreview(
            name="photos",
            max=5,
            event="upload",
        )

    In template::

        {{ upload|safe }}

    CSS Custom Properties::

        --dj-img-upload-bg: background (default: #f9fafb)
        --dj-img-upload-border: border color (default: #d1d5db)
        --dj-img-upload-radius: border radius (default: 0.5rem)
        --dj-img-upload-thumb-size: thumbnail size (default: 5rem)

    Args:
        name: Form field name.
        max: Maximum number of images.
        event: Event fired on file selection.
        accept: Accepted MIME types (default: image/*).
        previews: List of existing preview URLs.
        custom_class: Additional CSS classes.
    """

    def __init__(
        self,
        name: str = "images",
        max: int = 5,
        event: str = "upload",
        accept: str = "image/*",
        previews: list = None,
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            name=name,
            max=max,
            event=event,
            accept=accept,
            previews=previews,
            custom_class=custom_class,
            **kwargs,
        )
        self.name = name
        self.max = max
        self.event = event
        self.accept = accept
        self.previews = previews or []
        self.custom_class = custom_class

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

        return (
            f'<div class="{cls}" dj-hook="ImageUploadPreview" '
            f'data-event="{e_event}" data-max="{max_count}">'
            f'<label class="dj-img-upload__dropzone">'
            f"{upload_svg}"
            f'<span class="dj-img-upload__text">Drop images here or click to upload</span>'
            f'<span class="dj-img-upload__hint">Max {max_count} images</span>'
            f'<input type="file" name="{e_name}" accept="{e_accept}" '
            f'multiple class="dj-img-upload__input" aria-label="Upload images">'
            f"</label>"
            f"{thumbs_html}"
            f"</div>"
        )
