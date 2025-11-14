"""
Simple Icon component for djust - stateless, high-performance.

Provides icon rendering with popular icon libraries (Bootstrap Icons, Font Awesome, custom SVG).
This is a stateless Component optimized for performance.
"""

from typing import Optional
from ..base import Component


# Try to import Rust implementation
try:
    from djust._rust import RustIcon

    _RUST_AVAILABLE = True
except (ImportError, AttributeError):
    _RUST_AVAILABLE = False
    RustIcon = None


class Icon(Component):
    """
    Simple, stateless icon component with automatic Rust optimization.

    This component automatically uses pure Rust implementation if available,
    otherwise falls back to hybrid rendering with Rust template engine.

    Performance:
        - Pure Rust (if available): ~1μs per render
        - Hybrid template: ~5-10μs per render
        - Pure Python fallback: ~50-100μs per render

    Use Cases:
        - Display-only icons (no interaction needed)
        - Status indicators with icons
        - Navigation and UI elements
        - High-frequency rendering (100+ icons per page)

    Args:
        name: Icon name/class (e.g., "star-fill", "fa-heart")
        library: Icon library to use (bootstrap, fontawesome, custom)
        size: Icon size (xs, sm, md, lg, xl)
        color: Optional color/variant (primary, secondary, success, danger, etc.)
        label: Optional aria-label for accessibility

    Examples:
        # Bootstrap Icon (default)
        icon = Icon(name="star-fill", library="bootstrap", size="lg")
        html = icon.render()

        # Font Awesome
        icon = Icon(name="fa-heart", library="fontawesome", color="danger")

        # Custom SVG class
        icon = Icon(name="custom-logo", library="custom")

        # With accessibility label
        icon = Icon(name="check-circle", label="Success", color="success")

        # All sizes
        xs = Icon("star", size="xs")
        sm = Icon("star", size="sm")
        md = Icon("star", size="md")  # Default
        lg = Icon("star", size="lg")
        xl = Icon("star", size="xl")
    """

    # Link to Rust implementation if available
    _rust_impl_class = RustIcon if _RUST_AVAILABLE else None

    # Fallback: Hybrid rendering with template
    # Note: Using separate if blocks to avoid elif bug in Rust template engine
    template = """<i class="{% if library == "bootstrap" %}bi bi-{{ name }}{% endif %}{% if library == "fontawesome" %}{{ name }}{% endif %}{% if library == "custom" %}{{ name }}{% endif %}{% if size == "xs" %} icon-xs{% endif %}{% if size == "sm" %} icon-sm{% endif %}{% if size == "lg" %} icon-lg{% endif %}{% if size == "xl" %} icon-xl{% endif %}{% if color %} text-{{ color }}{% endif %}"{% if label %} aria-label="{{ label }}" role="img"{% endif %}></i>"""

    def __init__(
        self,
        name: str,
        library: str = "bootstrap",
        size: str = "md",
        color: Optional[str] = None,
        label: Optional[str] = None,
    ):
        """
        Initialize icon component.

        Args will be passed to Rust implementation if available,
        otherwise stored for hybrid rendering.

        Args:
            name: Icon name/class
            library: Icon library (bootstrap, fontawesome, custom)
            size: Icon size (xs, sm, md, lg, xl)
            color: Optional color variant
            label: Optional aria-label for accessibility
        """
        super().__init__(name=name, library=library, size=size, color=color, label=label)

        # Store for hybrid rendering (if Rust not used)
        self.name = name
        self.library = library
        self.size = size
        self.color = color
        self.label = label

    def get_context_data(self):
        """Context for hybrid rendering (if Rust not available)"""
        return {
            "name": self.name,
            "library": self.library,
            "size": self.size,
            "color": self.color,
            "label": self.label,
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
        """Render Bootstrap 5 icon"""
        # Build icon class based on library
        if self.library == "bootstrap":
            icon_class = f"bi bi-{self.name}"
        elif self.library == "fontawesome":
            icon_class = self.name
        else:  # custom
            icon_class = self.name

        # Size classes (Bootstrap Icons uses font-size)
        size_map = {
            "xs": " fs-6",  # 1rem
            "sm": " fs-5",  # 1.25rem
            "md": " fs-4",  # 1.5rem
            "lg": " fs-3",  # 1.75rem
            "xl": " fs-2",  # 2rem
        }
        size_class = size_map.get(self.size, " fs-4")

        # Color class
        color_class = f" text-{self.color}" if self.color else ""

        # Accessibility
        aria_label = f' aria-label="{self.label}" role="img"' if self.label else ""

        return f'<i class="{icon_class}{size_class}{color_class}"{aria_label}></i>'

    def _render_tailwind(self) -> str:
        """Render Tailwind CSS icon"""
        # Build icon class based on library
        if self.library == "bootstrap":
            icon_class = f"bi bi-{self.name}"
        elif self.library == "fontawesome":
            icon_class = self.name
        else:  # custom
            icon_class = self.name

        # Size classes
        size_map = {
            "xs": " text-base",  # 1rem
            "sm": " text-lg",  # 1.125rem
            "md": " text-xl",  # 1.25rem
            "lg": " text-2xl",  # 1.5rem
            "xl": " text-3xl",  # 1.875rem
        }
        size_class = size_map.get(self.size, " text-xl")

        # Color classes
        color_map = {
            "primary": " text-blue-600",
            "secondary": " text-gray-600",
            "success": " text-green-600",
            "danger": " text-red-600",
            "warning": " text-yellow-500",
            "info": " text-cyan-500",
            "light": " text-gray-300",
            "dark": " text-gray-900",
            "muted": " text-gray-500",
        }
        color_class = color_map.get(self.color, "") if self.color else ""

        # Accessibility
        aria_label = f' aria-label="{self.label}" role="img"' if self.label else ""

        return f'<i class="{icon_class}{size_class}{color_class}"{aria_label}></i>'

    def _render_plain(self) -> str:
        """Render plain HTML icon"""
        # Build icon class based on library
        if self.library == "bootstrap":
            icon_class = f"bi bi-{self.name}"
        elif self.library == "fontawesome":
            icon_class = self.name
        else:  # custom
            icon_class = self.name

        size_class = f" icon-{self.size}" if self.size != "md" else ""
        color_class = f" icon-{self.color}" if self.color else ""

        # Accessibility
        aria_label = f' aria-label="{self.label}" role="img"' if self.label else ""

        return f'<i class="{icon_class}{size_class}{color_class}"{aria_label}></i>'
