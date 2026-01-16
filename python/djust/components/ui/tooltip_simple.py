"""
Simple Tooltip component for djust - stateless, high-performance.

Provides tooltips for displaying contextual information on hover/click/focus.
This is a stateless Component optimized for performance.
"""

from ..base import Component


# Try to import Rust implementation
try:
    from djust._rust import RustTooltip

    _RUST_AVAILABLE = True
except (ImportError, AttributeError):
    _RUST_AVAILABLE = False
    RustTooltip = None


class Tooltip(Component):
    """
    Simple, stateless tooltip component with automatic Rust optimization.

    This component automatically uses pure Rust implementation if available,
    otherwise falls back to hybrid rendering with Rust template engine.

    Performance:
        - Pure Rust (if available): ~1μs per render
        - Hybrid template: ~5-10μs per render
        - Pure Python fallback: ~50-100μs per render

    Use Cases:
        - Contextual help text on hover
        - Additional information without cluttering UI
        - Icon tooltips
        - Interactive elements with explanatory text

    Args:
        content: Main content (the element that triggers the tooltip)
        text: Tooltip text content
        placement: Tooltip position (top, bottom, left, right)
        trigger: Activation method (hover, click, focus)
        arrow: Show arrow/pointer (True/False)

    Examples:
        # Simple usage
        tooltip = Tooltip("Hover me", text="This is helpful info")
        html = tooltip.render()

        # In template
        {{ tooltip.render|safe }}

        # All placements
        top = Tooltip("Top", text="Tooltip on top", placement="top")
        bottom = Tooltip("Bottom", text="Tooltip on bottom", placement="bottom")
        left = Tooltip("Left", text="Tooltip on left", placement="left")
        right = Tooltip("Right", text="Tooltip on right", placement="right")

        # Different triggers
        hover = Tooltip("Hover", text="Show on hover", trigger="hover")
        click = Tooltip("Click", text="Show on click", trigger="click")
        focus = Tooltip("Focus", text="Show on focus", trigger="focus")

        # With/without arrow
        with_arrow = Tooltip("Arrow", text="Has arrow", arrow=True)
        no_arrow = Tooltip("No arrow", text="No arrow", arrow=False)

        # Icon tooltip
        icon_tip = Tooltip('<i class="bi bi-info-circle"></i>', text="Help information")
    """

    # Link to Rust implementation if available
    _rust_impl_class = RustTooltip if _RUST_AVAILABLE else None

    # Fallback: Hybrid rendering with template
    # Using Bootstrap 5 tooltip structure with data attributes
    template = """<span class="d-inline-block" tabindex="0" data-bs-toggle="tooltip" data-bs-placement="{{ placement }}" data-bs-trigger="{{ trigger }}"{% if arrow %} data-bs-arrow="true"{% endif %} title="{{ text }}">{{ content }}</span>"""

    def __init__(
        self,
        content: str,
        text: str,
        placement: str = "top",
        trigger: str = "hover",
        arrow: bool = True,
    ):
        """
        Initialize tooltip component.

        Args:
            content: Main content (element that triggers tooltip)
            text: Tooltip text content
            placement: Position (top, bottom, left, right)
            trigger: Activation method (hover, click, focus)
            arrow: Show arrow/pointer
        """
        super().__init__(
            content=content, text=text, placement=placement, trigger=trigger, arrow=arrow
        )

        # Store for hybrid rendering
        self.content = content
        self.text = text
        self.placement = placement
        self.trigger = trigger
        self.arrow = arrow

    def get_context_data(self):
        """Context for hybrid rendering"""
        return {
            "content": self.content,
            "text": self.text,
            "placement": self.placement,
            "trigger": self.trigger,
            "arrow": self.arrow,
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
        """Render Bootstrap 5 tooltip"""
        # Validate placement
        valid_placements = ["top", "bottom", "left", "right"]
        placement = self.placement if self.placement in valid_placements else "top"

        # Validate trigger
        valid_triggers = ["hover", "click", "focus"]
        trigger = self.trigger if self.trigger in valid_triggers else "hover"

        # Build data attributes
        arrow_attr = ' data-bs-arrow="true"' if self.arrow else ""

        # Escape text for HTML attribute
        escaped_text = self._escape_html_attr(self.text)

        return (
            f'<span class="d-inline-block" tabindex="0" '
            f'data-bs-toggle="tooltip" '
            f'data-bs-placement="{placement}" '
            f'data-bs-trigger="{trigger}"{arrow_attr} '
            f'title="{escaped_text}">'
            f"{self.content}"
            f"</span>"
        )

    def _render_tailwind(self) -> str:
        """Render Tailwind CSS tooltip (using custom implementation)"""
        # Tailwind doesn't have built-in tooltips, so we use a custom approach
        # with relative positioning and pseudo-elements

        placement_map = {
            "top": "bottom-full left-1/2 -translate-x-1/2 mb-2",
            "bottom": "top-full left-1/2 -translate-x-1/2 mt-2",
            "left": "right-full top-1/2 -translate-y-1/2 mr-2",
            "right": "left-full top-1/2 -translate-y-1/2 ml-2",
        }
        position_classes = placement_map.get(self.placement, placement_map["top"])

        arrow_classes = ""
        if self.arrow:
            arrow_map = {
                "top": 'after:content-[""] after:absolute after:top-full after:left-1/2 after:-translate-x-1/2 after:border-4 after:border-transparent after:border-t-gray-900',
                "bottom": 'after:content-[""] after:absolute after:bottom-full after:left-1/2 after:-translate-x-1/2 after:border-4 after:border-transparent after:border-b-gray-900',
                "left": 'after:content-[""] after:absolute after:left-full after:top-1/2 after:-translate-y-1/2 after:border-4 after:border-transparent after:border-l-gray-900',
                "right": 'after:content-[""] after:absolute after:right-full after:top-1/2 after:-translate-y-1/2 after:border-4 after:border-transparent after:border-r-gray-900',
            }
            arrow_classes = f" {arrow_map.get(self.placement, arrow_map['top'])}"

        escaped_text = self._escape_html_attr(self.text)

        return (
            f'<span class="relative inline-block group">'
            f"{self.content}"
            f'<span class="absolute {position_classes} px-2 py-1 text-xs text-white bg-gray-900 rounded '
            f'opacity-0 group-hover:opacity-100 transition-opacity duration-200 whitespace-nowrap pointer-events-none{arrow_classes}">'
            f"{escaped_text}"
            f"</span>"
            f"</span>"
        )

    def _render_plain(self) -> str:
        """Render plain HTML tooltip (basic implementation)"""
        escaped_text = self._escape_html_attr(self.text)

        return (
            f'<span class="tooltip-wrapper" data-placement="{self.placement}" '
            f'data-trigger="{self.trigger}" title="{escaped_text}">'
            f"{self.content}"
            f"</span>"
        )

    @staticmethod
    def _escape_html_attr(text: str) -> str:
        """Escape text for use in HTML attributes"""
        return (
            text.replace("&", "&amp;")
            .replace('"', "&quot;")
            .replace("'", "&#x27;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
