"""
Avatar Component - User profile images with size variants.

A stateless avatar component for displaying user profile images.

Features:
- Size variants (xs, sm, md, lg, xl)
- Initials fallback (if no image)
- Round or square shapes
- Status indicator (online, offline, busy, away)

Example:
    from djust.components.ui import Avatar

    # Image avatar
    avatar = Avatar(src="/static/img/user.jpg", alt="John Doe")

    # Initials avatar
    avatar = Avatar(initials="JD", size="lg")

    # With status indicator
    avatar = Avatar(src="/static/img/user.jpg", status="online")

One renderer (#4002): ``_render_custom`` draws the Bootstrap 5 markup the retired Rust
``RustAvatar`` drew, pinned byte for byte by ``python/tests/test_ui_simple_golden_4002.py``.
"""

from typing import Any, Optional

from ..base import Component

from djust.components.utils import attr_text, csp_strict, data_url_attr

_SIZE = {"xs": "1.5rem", "sm": "2rem", "lg": "4rem", "xl": "6rem"}
_STATUS_CLASS = {
    "online": "bg-success",
    "offline": "bg-secondary",
    "busy": "bg-danger",
    "away": "bg-warning",
}


def _image_src(value: Any) -> str:
    """The ``<img src>`` value: trimmed, a non-navigation scheme as ``#``, escaped (#3748)."""
    text = str(value).strip()
    return data_url_attr(text, image=True) if text else ""


class Avatar(Component):
    """
    Simple stateless Avatar component (Bootstrap 5).

    Args:
        src: Image URL (optional if initials provided); a non-navigation scheme becomes ``#``
        alt: Alt text for image
        initials: Initials to display if no image (max 2 chars)
        size: Size variant (xs, sm, md, lg, xl)
        shape: Shape (circle, square)
        status: Status indicator (online, offline, busy, away)

    With ``LIVEVIEW_CONFIG["csp_strict"]`` the size is written as ``data-dj-style-*``
    attributes instead of an inline ``style`` (#3979).
    """

    def __init__(
        self,
        src: Optional[str] = None,
        alt: str = "",
        initials: Optional[str] = None,
        size: str = "md",
        shape: str = "circle",
        status: Optional[str] = None,
    ):
        super().__init__(
            src=src,
            alt=alt,
            initials=initials,
            size=size,
            shape=shape,
            status=status,
        )

        # Declared for type checkers; the base class already set each from the kwargs.
        self.src = src
        self.alt = alt
        self.size = size
        self.shape = shape
        self.status = status
        self.initials = initials[:2].upper() if initials else None

    def _render_custom(self) -> str:
        size = _SIZE.get(str(self.size), "3rem")
        shape_class = "rounded-circle" if self.shape == "circle" else "rounded"
        if csp_strict():
            size_attrs = (
                f' data-dj-style data-dj-style-width="{size}" data-dj-style-height="{size}"'
            )
        else:
            size_attrs = f' style="width: {size}; height: {size};"'
        html = f'<div class="position-relative d-inline-block"{size_attrs}>'
        if self.src is not None:
            html += (
                f'\n    <img src="{_image_src(self.src)}" alt="{attr_text(self.alt)}"'
                f' class="w-100 h-100 object-fit-cover {shape_class}">'
            )
        elif self.initials is not None:
            html += (
                '\n    <div class="w-100 h-100 bg-primary text-white d-flex align-items-center'
                f' justify-content-center {shape_class}">'
                f'\n        <span class="fw-bold">{attr_text(self.initials)}</span>'
                "\n    </div>"
            )
        if self.status is not None:
            status_class = _STATUS_CLASS.get(str(self.status), "bg-secondary")
            html += (
                f'\n    <span class="position-absolute bottom-0 end-0 p-1 {status_class}'
                ' border border-white rounded-circle"></span>'
            )
        return html + "\n</div>"
