"""
Avatar Component - User profile images with size variants

A stateless avatar component for displaying user profile images.
Uses the automatic 3-tier performance waterfall.

Features:
- Size variants (xs, sm, md, lg, xl)
- Initials fallback (if no image)
- Round or square shapes
- Status indicator (online, offline, busy)

Example:
    from djust.components.ui import Avatar

    # Image avatar
    avatar = Avatar(src="/static/img/user.jpg", alt="John Doe")

    # Initials avatar
    avatar = Avatar(initials="JD", size="lg")

    # With status indicator
    avatar = Avatar(src="/static/img/user.jpg", status="online")

Performance:
    - Pure Rust: ~0.4 μs (if available)
    - Hybrid: ~2-3 μs (template cached)
    - Python: ~0.2 μs (f-string fallback)
"""

from typing import Optional
from ..base import Component

# Try to import Rust implementation
try:
    from djust._rust import RustAvatar

    _RUST_AVAILABLE = True
except ImportError:
    _RUST_AVAILABLE = False
    RustAvatar = None


class Avatar(Component):
    """
    Simple stateless Avatar component.

    Automatic performance waterfall:
    1. Pure Rust (RustAvatar) - if available
    2. Rust template engine - with caching
    3. Python f-strings - fallback

    Args:
        src: Image URL (optional if initials provided)
        alt: Alt text for image
        initials: Initials to display if no image (max 2 chars)
        size: Size variant (xs, sm, md, lg, xl)
        shape: Shape (circle, square)
        status: Status indicator (online, offline, busy, away)
    """

    _rust_impl_class = RustAvatar if _RUST_AVAILABLE else None

    # Template for hybrid rendering
    template = """<div class="avatar avatar-{{ size }}{% if shape == "square" %} avatar-square{% endif %}{% if status %} avatar-{{ status }}{% endif %}">{% if src %}
    <img src="{{ src }}" alt="{{ alt }}" class="avatar-img">{% endif %}{% if initials %}
    <span class="avatar-initials">{{ initials }}</span>{% endif %}{% if status %}
    <span class="avatar-status"></span>{% endif %}
</div>"""

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

        self.src = src
        self.alt = alt
        self.initials = initials[:2].upper() if initials else None
        self.size = size
        self.shape = shape
        self.status = status

    def get_context_data(self):
        return {
            "src": self.src,
            "alt": self.alt,
            "initials": self.initials,
            "size": self.size,
            "shape": self.shape,
            "status": self.status,
        }

    def _render_custom(self) -> str:
        """Python f-string fallback"""
        from djust.config import config

        framework = config.get("css_framework", "bootstrap5")

        if framework == "bootstrap5":
            return self._render_bootstrap()
        elif framework == "tailwind":
            return self._render_tailwind()
        else:
            return self._render_plain()

    def _render_bootstrap(self) -> str:
        """Render Bootstrap 5 avatar"""
        # Size mapping (Bootstrap uses rem)
        size_map = {
            "xs": "width: 1.5rem; height: 1.5rem;",
            "sm": "width: 2rem; height: 2rem;",
            "md": "width: 3rem; height: 3rem;",
            "lg": "width: 4rem; height: 4rem;",
            "xl": "width: 6rem; height: 6rem;",
        }
        size_style = size_map.get(self.size, size_map["md"])

        # Shape
        shape_class = "rounded-circle" if self.shape == "circle" else "rounded"

        parts = [f'<div class="position-relative d-inline-block" style="{size_style}">']

        if self.src:
            parts.append(
                f'    <img src="{self.src}" alt="{self.alt}" class="w-100 h-100 object-fit-cover {shape_class}">'
            )
        elif self.initials:
            # Initials fallback
            parts.append(
                f'    <div class="w-100 h-100 bg-primary text-white d-flex align-items-center justify-content-center {shape_class}">'
            )
            parts.append(f'        <span class="fw-bold">{self.initials}</span>')
            parts.append("    </div>")

        # Status indicator
        if self.status:
            status_colors = {
                "online": "bg-success",
                "offline": "bg-secondary",
                "busy": "bg-danger",
                "away": "bg-warning",
            }
            status_class = status_colors.get(self.status, "bg-secondary")
            parts.append(
                f'    <span class="position-absolute bottom-0 end-0 p-1 {status_class} border border-white rounded-circle"></span>'
            )

        parts.append("</div>")
        return "\n".join(parts)

    def _render_tailwind(self) -> str:
        """Render Tailwind CSS avatar"""
        # Size mapping
        size_map = {
            "xs": "w-6 h-6",
            "sm": "w-8 h-8",
            "md": "w-12 h-12",
            "lg": "w-16 h-16",
            "xl": "w-24 h-24",
        }
        size_class = size_map.get(self.size, size_map["md"])

        # Shape
        shape_class = "rounded-full" if self.shape == "circle" else "rounded-lg"

        parts = [f'<div class="relative inline-block {size_class}">']

        if self.src:
            parts.append(
                f'    <img src="{self.src}" alt="{self.alt}" class="w-full h-full object-cover {shape_class}">'
            )
        elif self.initials:
            parts.append(
                f'    <div class="w-full h-full bg-blue-600 text-white flex items-center justify-center {shape_class}">'
            )
            parts.append(f'        <span class="font-bold text-sm">{self.initials}</span>')
            parts.append("    </div>")

        # Status indicator
        if self.status:
            status_colors = {
                "online": "bg-green-500",
                "offline": "bg-gray-400",
                "busy": "bg-red-500",
                "away": "bg-yellow-500",
            }
            status_class = status_colors.get(self.status, "bg-gray-400")
            parts.append(
                f'    <span class="absolute bottom-0 right-0 block h-3 w-3 {status_class} ring-2 ring-white rounded-full"></span>'
            )

        parts.append("</div>")
        return "\n".join(parts)

    def _render_plain(self) -> str:
        """Render plain HTML avatar"""
        parts = [f'<div class="avatar avatar-{self.size}">']

        if self.src:
            parts.append(f'    <img src="{self.src}" alt="{self.alt}">')
        elif self.initials:
            parts.append(f'    <span class="avatar-initials">{self.initials}</span>')

        if self.status:
            parts.append(f'    <span class="avatar-status avatar-status-{self.status}"></span>')

        parts.append("</div>")
        return "\n".join(parts)
