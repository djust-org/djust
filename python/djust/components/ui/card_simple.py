"""
Card Component - Stateless container with header, body, and footer

A simple, stateless card component following the Badge/Button pattern.
Uses the automatic 3-tier performance waterfall.

Features:
- Header (optional)
- Body (required)
- Footer (optional)
- Variants (default, outlined, elevated)

Example:
    from djust.components.ui import Card

    # Simple card
    card = Card(
        header="User Profile",
        body="<p>John Doe</p>",
        footer='<button class="btn btn-sm btn-primary">Edit</button>'
    )

Performance:
    - Pure Rust: ~0.5 μs (if available)
    - Hybrid: ~3-5 μs (template cached)
    - Python: ~0.3 μs (f-string fallback)
"""

from typing import Optional
from ..base import Component

# Try to import Rust implementation
try:
    from djust._rust import RustCard

    _RUST_AVAILABLE = True
except ImportError:
    _RUST_AVAILABLE = False
    RustCard = None


class Card(Component):
    """
    Simple stateless Card component.

    Automatic performance waterfall:
    1. Pure Rust (RustCard) - if available
    2. Rust template engine - with caching
    3. Python f-strings - fallback

    Args:
        body: Body content (required)
        header: Optional header
        footer: Optional footer
        variant: Style variant (default, outlined, elevated)
    """

    _rust_impl_class = RustCard if _RUST_AVAILABLE else None

    # Template for hybrid rendering
    template = """<div class="card{% if variant == "outlined" %} border{% endif %}{% if variant == "elevated" %} shadow{% endif %}">{% if header %}
    <div class="card-header">{{ header }}</div>{% endif %}
    <div class="card-body">{{ body }}</div>{% if footer %}
    <div class="card-footer">{{ footer }}</div>{% endif %}
</div>"""

    def __init__(
        self,
        body: str,
        header: Optional[str] = None,
        footer: Optional[str] = None,
        variant: str = "default",
    ):
        super().__init__(body=body, header=header, footer=footer, variant=variant)

        self.body = body
        self.header = header
        self.footer = footer
        self.variant = variant

    def get_context_data(self):
        return {
            "body": self.body,
            "header": self.header,
            "footer": self.footer,
            "variant": self.variant,
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
        variant_class = {
            "default": "",
            "outlined": " border",
            "elevated": " shadow",
        }.get(self.variant, "")

        parts = [f'<div class="card{variant_class}">']

        if self.header:
            parts.append(f'    <div class="card-header">{self.header}</div>')

        parts.append(f'    <div class="card-body">{self.body}</div>')

        if self.footer:
            parts.append(f'    <div class="card-footer">{self.footer}</div>')

        parts.append("</div>")
        return "\n".join(parts)

    def _render_tailwind(self) -> str:
        variant_class = {
            "default": "bg-white rounded-lg",
            "outlined": "bg-white rounded-lg border border-gray-200",
            "elevated": "bg-white rounded-lg shadow-lg",
        }.get(self.variant, "bg-white rounded-lg")

        parts = [f'<div class="{variant_class}">']

        if self.header:
            parts.append(f'    <div class="px-6 py-4 border-b font-semibold">{self.header}</div>')

        parts.append(f'    <div class="px-6 py-4">{self.body}</div>')

        if self.footer:
            parts.append(f'    <div class="px-6 py-4 border-t bg-gray-50">{self.footer}</div>')

        parts.append("</div>")
        return "\n".join(parts)

    def _render_plain(self) -> str:
        parts = ['<div class="card">']

        if self.header:
            parts.append(f'    <div class="card-header">{self.header}</div>')

        parts.append(f'    <div class="card-body">{self.body}</div>')

        if self.footer:
            parts.append(f'    <div class="card-footer">{self.footer}</div>')

        parts.append("</div>")
        return "\n".join(parts)
