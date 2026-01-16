"""
Simple Badge component for djust - stateless, high-performance.

Provides small labels/badges for counts, statuses, and categories.
This is a stateless Component optimized for performance.
Use BadgeComponent for interactive badges with state.
"""

from ..base import Component


# Try to import Rust implementation (will be added later)
try:
    from djust._rust import RustBadge

    _RUST_AVAILABLE = True
except (ImportError, AttributeError):
    _RUST_AVAILABLE = False
    RustBadge = None


class Badge(Component):
    """
    Simple, stateless badge component with automatic Rust optimization.

    This component automatically uses pure Rust implementation if available,
    otherwise falls back to hybrid rendering with Rust template engine.

    Performance:
        - Pure Rust (if available): ~1μs per render
        - Hybrid template: ~5-10μs per render
        - Pure Python fallback: ~50-100μs per render

    Use Cases:
        - Display-only badges (no interaction needed)
        - Status indicators
        - Counts and labels
        - Tags (without dismiss functionality)
        - High-frequency rendering (100+ badges per page)

    For interactive badges with state (dismissible, dynamic), use BadgeComponent (LiveComponent).

    Args:
        text: Badge text content
        variant: Color variant (primary, secondary, success, danger, warning, info, light, dark)
        size: Badge size (sm, md, lg) - optional, defaults to md
        pill: Rounded pill style (True/False) - optional, defaults to False

    Examples:
        # Simple usage (Rust automatically used if available)
        badge = Badge("New", variant="primary")
        html = badge.render()

        # In template
        {{ badge.render }}  # or just {{ badge }}

        # Multiple badges
        badges = [Badge(f"Item {i}") for i in range(100)]
        # Each renders in ~1μs (Rust) vs ~50μs (Python)

        # All variants
        status = Badge("Active", variant="success")
        count = Badge("99+", variant="danger", pill=True)
        tag = Badge("Python", variant="info")
        size_sm = Badge("Small", variant="secondary", size="sm")
        size_lg = Badge("Large", variant="primary", size="lg")
    """

    # Link to Rust implementation if available
    _rust_impl_class = RustBadge if _RUST_AVAILABLE else None

    # Fallback: Hybrid rendering with template
    # Note: This will use Rust template engine if available, Django templates otherwise
    # Avoiding elif due to Rust template engine bug - using separate if blocks instead
    # Bootstrap default badge is 0.75em, so: sm=default, md=fs-6 (1rem), lg=fs-5 (1.25rem)
    template = '<span class="badge bg-{{ variant }}{% if size == "md" %} fs-6{% endif %}{% if size == "lg" %} fs-5{% endif %}{% if pill %} rounded-pill{% endif %}">{{ text }}</span>'

    def __init__(self, text: str, variant: str = "primary", size: str = "md", pill: bool = False):
        """
        Initialize badge component.

        Args will be passed to Rust implementation if available,
        otherwise stored for hybrid rendering.

        Args:
            text: Badge text content
            variant: Color variant (primary, secondary, success, danger, warning, info, light, dark)
            size: Badge size (sm, md, lg)
            pill: Rounded pill style
        """
        super().__init__(text=text, variant=variant, size=size, pill=pill)

        # Store for hybrid rendering (if Rust not used)
        self.text = text
        self.variant = variant
        self.size = size
        self.pill = pill

    def get_context_data(self):
        """Context for hybrid rendering (if Rust not available)"""
        return {
            "text": self.text,
            "variant": self.variant,
            "size": self.size,
            "pill": self.pill,
        }

    def _render_custom(self) -> str:
        """
        Custom Python rendering (fallback if no Rust and template engine fails).

        This provides framework-specific rendering for maximum compatibility.
        """
        from djust.config import config

        framework = config.get("css_framework", "bootstrap5")

        if framework == "bootstrap5":
            return self._render_bootstrap()
        elif framework == "tailwind":
            return self._render_tailwind()
        else:
            return self._render_plain()

    def _render_bootstrap(self) -> str:
        """Render Bootstrap 5 badge"""
        variant_map = {
            "primary": "primary",
            "secondary": "secondary",
            "success": "success",
            "danger": "danger",
            "warning": "warning",
            "info": "info",
            "light": "light",
            "dark": "dark",
        }
        variant = variant_map.get(self.variant, "primary")

        # Use Bootstrap's font-size utilities
        # Bootstrap default badge is 0.75em, scale up from there
        size_map = {
            "sm": "",  # Use default badge size (0.75em)
            "md": " fs-6",  # Medium = 1rem
            "lg": " fs-5",  # Large = 1.25rem
        }
        size_class = size_map.get(self.size, "")

        pill_class = " rounded-pill" if self.pill else ""

        return f'<span class="badge bg-{variant}{size_class}{pill_class}">{self.text}</span>'

    def _render_tailwind(self) -> str:
        """Render Tailwind CSS badge"""
        variant_map = {
            "primary": "bg-blue-100 text-blue-800",
            "secondary": "bg-gray-100 text-gray-800",
            "success": "bg-green-100 text-green-800",
            "danger": "bg-red-100 text-red-800",
            "warning": "bg-yellow-100 text-yellow-800",
            "info": "bg-cyan-100 text-cyan-800",
            "light": "bg-gray-50 text-gray-600",
            "dark": "bg-gray-800 text-white",
        }
        variant_classes = variant_map.get(self.variant, variant_map["primary"])

        size_map = {
            "sm": "px-2 py-0.5 text-xs",
            "md": "px-2.5 py-0.5 text-xs",
            "lg": "px-3 py-1 text-sm",
        }
        size_classes = size_map.get(self.size, size_map["md"])

        pill_classes = "rounded-full" if self.pill else "rounded"

        return f'<span class="inline-flex items-center font-medium {size_classes} {pill_classes} {variant_classes}">{self.text}</span>'

    def _render_plain(self) -> str:
        """Render plain HTML badge"""
        size_class = f" badge-{self.size}" if self.size != "md" else ""
        pill_class = " badge-pill" if self.pill else ""

        return (
            f'<span class="badge badge-{self.variant}{size_class}{pill_class}">{self.text}</span>'
        )
