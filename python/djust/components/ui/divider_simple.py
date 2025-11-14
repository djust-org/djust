"""
Simple Divider component for djust - stateless, high-performance.

Provides horizontal rule (hr) dividers with optional text labels.
This is a stateless Component optimized for performance.
"""

from typing import Optional
from ..base import Component


# Try to import Rust implementation
try:
    from djust._rust import RustDivider

    _RUST_AVAILABLE = True
except (ImportError, AttributeError):
    _RUST_AVAILABLE = False
    RustDivider = None


class Divider(Component):
    """
    Simple, stateless divider component with automatic Rust optimization.

    This component automatically uses pure Rust implementation if available,
    otherwise falls back to hybrid rendering with Rust template engine.

    Performance:
        - Pure Rust (if available): ~1μs per render
        - Hybrid template: ~5-10μs per render
        - Pure Python fallback: ~50-100μs per render

    Use Cases:
        - Section dividers
        - Visual separation between content
        - Dividers with centered text labels (e.g., "OR", "AND")
        - Different line styles (solid, dashed, dotted)

    Args:
        text: Optional text to display in center of divider
        style: Line style - "solid", "dashed", or "dotted" (default: "solid")
        margin: Margin size - "sm", "md", or "lg" (default: "md")

    Examples:
        # Simple line
        divider = Divider()
        html = divider.render()

        # With centered text
        divider = Divider(text="OR")

        # Dashed style with large margins
        divider = Divider(style="dashed", margin="lg")

        # Dotted divider with text
        divider = Divider(text="AND", style="dotted", margin="sm")

        # In template
        {{ divider.render }}  # or just {{ divider }}
    """

    # Link to Rust implementation if available
    _rust_impl_class = RustDivider if _RUST_AVAILABLE else None

    # Fallback: Hybrid rendering with template
    # Note: This will use Rust template engine if available, Django templates otherwise
    # Django templates auto-escape by default, Rust template engine does not
    template = """{% if text %}<div class="divider-container my-{{ margin_class }}"><hr class="divider-line divider-{{ style }}"><span class="divider-text">{{ text|escape }}</span><hr class="divider-line divider-{{ style }}"></div>{% else %}<hr class="my-{{ margin_class }} divider-{{ style }}">{% endif %}"""

    def __init__(self, text: Optional[str] = None, style: str = "solid", margin: str = "md"):
        """
        Initialize divider component.

        Args will be passed to Rust implementation if available,
        otherwise stored for hybrid rendering.

        Args:
            text: Optional text to display in center of divider
            style: Line style - "solid", "dashed", or "dotted"
            margin: Margin size - "sm", "md", or "lg"
        """
        super().__init__(text=text, style=style, margin=margin)

        # Store for hybrid rendering (if Rust not used)
        self.text = text
        self.style = style
        self.margin = margin

    def get_context_data(self):
        """Context for hybrid rendering (if Rust not available)"""
        # Map margin to Bootstrap spacing classes
        margin_map = {
            "sm": "2",
            "md": "3",
            "lg": "4",
        }
        margin_class = margin_map.get(self.margin, "3")

        return {
            "text": self.text,
            "style": self.style,
            "margin_class": margin_class,
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
        """Render Bootstrap 5 divider"""
        from html import escape

        # Map margin to Bootstrap spacing classes
        margin_map = {
            "sm": "my-2",
            "md": "my-3",
            "lg": "my-4",
        }
        margin_class = margin_map.get(self.margin, "my-3")

        # Map style to border styles
        style_map = {
            "solid": "",
            "dashed": "border-dashed",
            "dotted": "border-dotted",
        }
        style_class = style_map.get(self.style, "")

        if self.text:
            # Divider with text in center
            # Use flexbox for layout
            escaped_text = escape(self.text)
            return f"""<div class="d-flex align-items-center {margin_class}">
    <hr class="flex-grow-1 {style_class}">
    <span class="px-3 text-muted">{escaped_text}</span>
    <hr class="flex-grow-1 {style_class}">
</div>"""
        else:
            # Simple horizontal rule
            return f'<hr class="{margin_class} {style_class}">'

    def _render_tailwind(self) -> str:
        """Render Tailwind CSS divider"""
        from html import escape

        # Map margin to Tailwind spacing classes
        margin_map = {
            "sm": "my-2",
            "md": "my-4",
            "lg": "my-6",
        }
        margin_class = margin_map.get(self.margin, "my-4")

        # Map style to Tailwind border styles
        style_map = {
            "solid": "border-solid",
            "dashed": "border-dashed",
            "dotted": "border-dotted",
        }
        style_class = style_map.get(self.style, "border-solid")

        if self.text:
            # Divider with text in center
            escaped_text = escape(self.text)
            return f"""<div class="flex items-center {margin_class}">
    <hr class="flex-grow border-gray-300 {style_class}">
    <span class="px-3 text-gray-500">{escaped_text}</span>
    <hr class="flex-grow border-gray-300 {style_class}">
</div>"""
        else:
            # Simple horizontal rule
            return f'<hr class="{margin_class} border-gray-300 {style_class}">'

    def _render_plain(self) -> str:
        """Render plain HTML divider"""
        from html import escape

        margin_class = f"divider-{self.margin}" if self.margin != "md" else ""
        style_class = f"divider-{self.style}" if self.style != "solid" else ""

        classes = " ".join(filter(None, [margin_class, style_class]))
        class_attr = f' class="{classes}"' if classes else ""

        if self.text:
            # Divider with text in center
            escaped_text = escape(self.text)
            return f"""<div class="divider-container{" " + margin_class if margin_class else ""}">
    <hr{class_attr}>
    <span class="divider-text">{escaped_text}</span>
    <hr{class_attr}>
</div>"""
        else:
            # Simple horizontal rule
            return f"<hr{class_attr}>"
