"""
Spinner Component - Loading indicators

A stateless spinner component for showing loading states.
Uses the automatic 3-tier performance waterfall.

Features:
- Size variants (sm, md, lg)
- Color variants (primary, secondary, success, danger, etc.)
- Animation type (border, grow)
- Accessible (includes sr-only text)

Example:
    from djust.components.ui import Spinner

    # Simple spinner
    spinner = Spinner()

    # Colored spinner
    spinner = Spinner(variant="primary", size="lg")

    # Growing spinner
    spinner = Spinner(animation="grow", variant="success")

Performance:
    - Pure Rust: ~0.3 μs (if available)
    - Hybrid: ~2-3 μs (template cached)
    - Python: ~0.2 μs (f-string fallback)
"""

from ..base import Component

# Try to import Rust implementation
try:
    from djust._rust import RustSpinner

    _RUST_AVAILABLE = True
except ImportError:
    _RUST_AVAILABLE = False
    RustSpinner = None


class Spinner(Component):
    """
    Simple stateless Spinner component.

    Automatic performance waterfall:
    1. Pure Rust (RustSpinner) - if available
    2. Rust template engine - with caching
    3. Python f-strings - fallback

    Args:
        variant: Color variant (primary, secondary, success, danger, warning, info, light, dark)
        size: Size variant (sm, md, lg)
        animation: Animation type (border, grow)
        sr_text: Screen reader text for accessibility
    """

    _rust_impl_class = RustSpinner if _RUST_AVAILABLE else None

    # Template for hybrid rendering
    template = """<div class="spinner-{{ animation }} text-{{ variant }}{% if size == "sm" %} spinner-{{ animation }}-sm{% endif %}" role="status">
    <span class="visually-hidden">{{ sr_text }}</span>
</div>"""

    def __init__(
        self,
        variant: str = "primary",
        size: str = "md",
        animation: str = "border",
        sr_text: str = "Loading...",
    ):
        super().__init__(
            variant=variant,
            size=size,
            animation=animation,
            sr_text=sr_text,
        )

        self.variant = variant
        self.size = size
        self.animation = animation
        self.sr_text = sr_text

    def get_context_data(self):
        return {
            "variant": self.variant,
            "size": self.size,
            "animation": self.animation,
            "sr_text": self.sr_text,
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
        """Render Bootstrap 5 spinner"""
        size_class = f" spinner-{self.animation}-sm" if self.size == "sm" else ""

        return f"""<div class="spinner-{self.animation} text-{self.variant}{size_class}" role="status">
    <span class="visually-hidden">{self.sr_text}</span>
</div>"""

    def _render_tailwind(self) -> str:
        """Render Tailwind CSS spinner"""
        # Size mapping
        size_map = {
            "sm": "w-4 h-4",
            "md": "w-8 h-8",
            "lg": "w-12 h-12",
        }
        size_class = size_map.get(self.size, size_map["md"])

        # Color mapping
        color_map = {
            "primary": "border-blue-600",
            "secondary": "border-gray-600",
            "success": "border-green-600",
            "danger": "border-red-600",
            "warning": "border-yellow-600",
            "info": "border-cyan-600",
            "light": "border-gray-300",
            "dark": "border-gray-900",
        }
        color_class = color_map.get(self.variant, color_map["primary"])

        if self.animation == "grow":
            # Pulse animation for grow
            return f"""<div class="{size_class} rounded-full bg-current {color_class} animate-pulse" role="status">
    <span class="sr-only">{self.sr_text}</span>
</div>"""
        else:
            # Spin animation for border
            return f"""<div class="{size_class} border-4 border-t-transparent {color_class} rounded-full animate-spin" role="status">
    <span class="sr-only">{self.sr_text}</span>
</div>"""

    def _render_plain(self) -> str:
        """Render plain HTML spinner"""
        return f"""<div class="spinner spinner-{self.animation} spinner-{self.size} spinner-{self.variant}" role="status">
    <span class="sr-only">{self.sr_text}</span>
</div>"""
