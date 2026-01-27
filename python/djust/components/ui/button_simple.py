"""
Simple Button component for djust - stateless, high-performance.

Provides buttons for display purposes.
This is a stateless Component optimized for performance.
For interactive buttons with event handlers, use them in LiveView event handlers.
"""

from ..base import Component


# Try to import Rust implementation (will be added later)
try:
    from djust._rust import RustButton

    _RUST_AVAILABLE = True
except (ImportError, AttributeError):
    _RUST_AVAILABLE = False
    RustButton = None


class Button(Component):
    """
    Simple, stateless button component with automatic Rust optimization.

    This component automatically uses pure Rust implementation if available,
    otherwise falls back to hybrid rendering with Rust template engine.

    Performance:
        - Pure Rust (if available): ~1μs per render
        - Hybrid template: ~5-10μs per render
        - Pure Python fallback: ~50-100μs per render

    Use Cases:
        - Display-only buttons (rendered but @click handled by parent)
        - Static button collections
        - Button groups
        - High-frequency rendering

    Note: This is a stateless component for rendering. Event handlers are
    attached in the template using @click directives.

    Args:
        text: Button text content
        variant: Color variant (primary, secondary, success, danger, warning, info, light, dark, link)
        size: Button size (sm, md, lg)
        disabled: Whether button is disabled
        outline: Use outline style instead of filled

    Examples:
        # Simple usage
        btn = Button("Click me", variant="primary")
        html = btn.render()

        # In template with event handler
        <div>
            {{ button.render|safe }}
        </div>
        # Then add @click in template:
        # Or better, use in LiveView with @click:
        # <button dj-click="handle_click">{{ button_text }}</button>

        # All variants
        primary = Button("Primary", variant="primary")
        secondary = Button("Secondary", variant="secondary")
        success = Button("Success", variant="success")
        danger = Button("Danger", variant="danger")

        # Sizes
        small = Button("Small", size="sm")
        large = Button("Large", size="lg")

        # Outline style
        outline = Button("Outline", variant="primary", outline=True)

        # Disabled
        disabled = Button("Disabled", disabled=True)
    """

    # Link to Rust implementation if available
    _rust_impl_class = RustButton if _RUST_AVAILABLE else None

    # Fallback: Hybrid rendering with template
    # Note: Avoiding elif due to Rust template engine bug - using separate if blocks instead
    template = '<button type="button" class="btn {% if outline %}btn-outline-{{ variant }}{% else %}btn-{{ variant }}{% endif %}{% if size == "sm" %} btn-sm{% endif %}{% if size == "lg" %} btn-lg{% endif %}"{% if disabled %} disabled{% endif %}>{{ text }}</button>'

    def __init__(
        self,
        text: str,
        variant: str = "primary",
        size: str = "md",
        disabled: bool = False,
        outline: bool = False,
    ):
        """
        Initialize button component.

        Args:
            text: Button text content
            variant: Color variant
            size: Button size (sm, md, lg)
            disabled: Whether button is disabled
            outline: Use outline style
        """
        super().__init__(text=text, variant=variant, size=size, disabled=disabled, outline=outline)

        # Store for hybrid rendering
        self.text = text
        self.variant = variant
        self.size = size
        self.disabled = disabled
        self.outline = outline

    def get_context_data(self):
        """Context for hybrid rendering"""
        return {
            "text": self.text,
            "variant": self.variant,
            "size": self.size,
            "disabled": self.disabled,
            "outline": self.outline,
        }

    def _render_custom(self) -> str:
        """Custom Python rendering (fallback)"""
        from djust.config import config

        framework = config.get("css_framework", "bootstrap5")

        if framework == "bootstrap5":
            return self._render_bootstrap()
        elif framework == "tailwind":
            return self._render_tailwind()
        else:
            return self._render_plain()

    def _render_bootstrap(self) -> str:
        """Render Bootstrap 5 button"""
        size_map = {
            "sm": " btn-sm",
            "lg": " btn-lg",
            "md": "",
        }
        size_class = size_map.get(self.size, "")

        if self.outline:
            variant_class = f"btn-outline-{self.variant}"
        else:
            variant_class = f"btn-{self.variant}"

        disabled_attr = " disabled" if self.disabled else ""

        return f'<button type="button" class="btn {variant_class}{size_class}"{disabled_attr}>{self.text}</button>'

    def _render_tailwind(self) -> str:
        """Render Tailwind CSS button"""
        variant_map = {
            "primary": "bg-blue-600 hover:bg-blue-700 text-white",
            "secondary": "bg-gray-600 hover:bg-gray-700 text-white",
            "success": "bg-green-600 hover:bg-green-700 text-white",
            "danger": "bg-red-600 hover:bg-red-700 text-white",
            "warning": "bg-yellow-500 hover:bg-yellow-600 text-white",
            "info": "bg-cyan-500 hover:bg-cyan-600 text-white",
            "light": "bg-gray-100 hover:bg-gray-200 text-gray-800",
            "dark": "bg-gray-800 hover:bg-gray-900 text-white",
            "link": "text-blue-600 hover:text-blue-800 hover:underline",
        }

        outline_map = {
            "primary": "border border-blue-600 text-blue-600 hover:bg-blue-50",
            "secondary": "border border-gray-600 text-gray-600 hover:bg-gray-50",
            "success": "border border-green-600 text-green-600 hover:bg-green-50",
            "danger": "border border-red-600 text-red-600 hover:bg-red-50",
            "warning": "border border-yellow-500 text-yellow-600 hover:bg-yellow-50",
            "info": "border border-cyan-500 text-cyan-600 hover:bg-cyan-50",
        }

        size_map = {
            "sm": "px-3 py-1.5 text-sm",
            "md": "px-4 py-2 text-base",
            "lg": "px-6 py-3 text-lg",
        }
        size_classes = size_map.get(self.size, size_map["md"])

        if self.outline:
            variant_classes = outline_map.get(self.variant, outline_map["primary"])
        else:
            variant_classes = variant_map.get(self.variant, variant_map["primary"])

        disabled_classes = " opacity-50 cursor-not-allowed" if self.disabled else ""
        disabled_attr = " disabled" if self.disabled else ""

        return f'<button type="button" class="rounded font-medium {size_classes} {variant_classes}{disabled_classes}"{disabled_attr}>{self.text}</button>'

    def _render_plain(self) -> str:
        """Render plain HTML button"""
        size_class = f" button-{self.size}" if self.size != "md" else ""
        style_prefix = "button-outline-" if self.outline else "button-"
        disabled_attr = " disabled" if self.disabled else ""

        return f'<button type="button" class="button {style_prefix}{self.variant}{size_class}"{disabled_attr}>{self.text}</button>'
