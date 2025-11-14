"""
Simple Breadcrumb component for djust - stateless, high-performance.

Provides navigation breadcrumbs showing current page location in site hierarchy.
This is a stateless Component optimized for performance.
"""

from typing import List, Dict, Optional
from ..base import Component


# Try to import Rust implementation (will be added later)
try:
    from djust._rust import RustBreadcrumb

    _RUST_AVAILABLE = True
except (ImportError, AttributeError):
    _RUST_AVAILABLE = False
    RustBreadcrumb = None


class Breadcrumb(Component):
    """
    Simple, stateless breadcrumb component with automatic Rust optimization.

    This component automatically uses pure Rust implementation if available,
    otherwise falls back to Python rendering with loops.

    Performance:
        - Pure Rust (if available): ~1μs per render
        - Pure Python fallback: ~50-100μs per render

    Use Cases:
        - Page navigation hierarchies
        - Site structure display
        - Current location indicator
        - Breadcrumb trails

    Args:
        items: List of breadcrumb item dicts with 'label' and optional 'url'
        separator: Separator character between items ('/', '>', custom text)
        show_home: Whether to show home icon for first item

    Item format:
        {
            'label': 'Page Name',
            'url': '/path/to/page',  # Optional - omit for current page
        }

    Examples:
        # Simple usage
        breadcrumb = Breadcrumb(items=[
            {'label': 'Home', 'url': '/'},
            {'label': 'Products', 'url': '/products'},
            {'label': 'Laptop', 'url': None},  # Current page
        ])
        html = breadcrumb.render()

        # In template
        {{ breadcrumb.render|safe }}

        # Custom separator
        breadcrumb = Breadcrumb(
            items=[
                {'label': 'Home', 'url': '/'},
                {'label': 'Category', 'url': '/category'},
                {'label': 'Item'},
            ],
            separator='>'
        )

        # With home icon
        breadcrumb = Breadcrumb(
            items=[
                {'label': 'Home', 'url': '/'},
                {'label': 'Dashboard', 'url': '/dashboard'},
                {'label': 'Settings'},
            ],
            show_home=True
        )
    """

    # Link to Rust implementation if available
    _rust_impl_class = RustBreadcrumb if _RUST_AVAILABLE else None

    # Note: Not using template because Rust template engine doesn't support
    # forloop.counter0 and forloop.last reliably
    # Using Python fallback which is still fast (~50-100μs)

    def __init__(
        self, items: List[Dict[str, Optional[str]]], separator: str = "/", show_home: bool = False
    ):
        """
        Initialize breadcrumb component.

        Args:
            items: List of breadcrumb item dicts with 'label' and optional 'url'
            separator: Separator character between items
            show_home: Whether to show home icon for first item
        """
        super().__init__(items=items, separator=separator, show_home=show_home)

        # Store for Python rendering
        self.items = items
        self.separator = separator
        self.show_home = show_home

    def get_context_data(self):
        """Context for hybrid rendering (if needed later)"""
        return {
            "items": self.items,
            "separator": self.separator,
            "show_home": self.show_home,
        }

    def _render_custom(self) -> str:
        """
        Custom Python rendering using loops.

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
        """Render Bootstrap 5 breadcrumb"""
        if not self.items:
            return '<nav aria-label="breadcrumb"><ol class="breadcrumb"></ol></nav>'

        breadcrumb_items = []
        last_index = len(self.items) - 1

        for i, item in enumerate(self.items):
            label = item.get("label", "")
            url = item.get("url")
            is_active = (i == last_index) or (url is None)

            # First item with home icon
            if i == 0 and self.show_home:
                label = (
                    f'<i class="bi bi-house-door"></i> {label}'
                    if label
                    else '<i class="bi bi-house-door"></i>'
                )

            if is_active:
                # Current page (active) - no link
                breadcrumb_items.append(
                    f'  <li class="breadcrumb-item active" aria-current="page">{label}</li>'
                )
            else:
                # Link to page
                breadcrumb_items.append(
                    f'  <li class="breadcrumb-item"><a href="{url}">{label}</a></li>'
                )

        breadcrumb_html = "\n".join(breadcrumb_items)

        return f"""<nav aria-label="breadcrumb">
  <ol class="breadcrumb">
{breadcrumb_html}
  </ol>
</nav>"""

    def _render_tailwind(self) -> str:
        """Render Tailwind CSS breadcrumb"""
        if not self.items:
            return '<nav class="flex" aria-label="Breadcrumb"><ol class="inline-flex items-center space-x-1 md:space-x-3"></ol></nav>'

        breadcrumb_items = []
        last_index = len(self.items) - 1

        for i, item in enumerate(self.items):
            label = item.get("label", "")
            url = item.get("url")
            is_active = (i == last_index) or (url is None)

            # First item with home icon
            if i == 0 and self.show_home:
                home_svg = """<svg class="w-4 h-4 mr-2" fill="currentColor" viewBox="0 0 20 20" xmlns="http://www.w3.org/2000/svg">
                <path d="M10.707 2.293a1 1 0 00-1.414 0l-7 7a1 1 0 001.414 1.414L4 10.414V17a1 1 0 001 1h2a1 1 0 001-1v-2a1 1 0 011-1h2a1 1 0 011 1v2a1 1 0 001 1h2a1 1 0 001-1v-6.586l.293.293a1 1 0 001.414-1.414l-7-7z"></path>
              </svg>"""
                label = f"{home_svg}{label}" if label else home_svg
            else:
                # Separator SVG for non-first items
                separator_svg = """<svg class="w-6 h-6 text-gray-400" fill="currentColor" viewBox="0 0 20 20" xmlns="http://www.w3.org/2000/svg">
                <path fill-rule="evenodd" d="M7.293 14.707a1 1 0 010-1.414L10.586 10 7.293 6.707a1 1 0 011.414-1.414l4 4a1 1 0 010 1.414l-4 4a1 1 0 01-1.414 0z" clip-rule="evenodd"></path>
              </svg>"""
                label = f"{separator_svg}{label}"

            if is_active:
                # Current page (active) - no link
                breadcrumb_items.append(
                    f'    <li class="inline-flex items-center"><span class="ml-1 text-sm font-medium text-gray-500 md:ml-2">{label}</span></li>'
                )
            else:
                # Link to page
                breadcrumb_items.append(
                    f'    <li class="inline-flex items-center"><a href="{url}" class="inline-flex items-center text-sm font-medium text-gray-700 hover:text-blue-600">{label}</a></li>'
                )

        breadcrumb_html = "\n".join(breadcrumb_items)

        return f"""<nav class="flex" aria-label="Breadcrumb">
  <ol class="inline-flex items-center space-x-1 md:space-x-3">
{breadcrumb_html}
  </ol>
</nav>"""

    def _render_plain(self) -> str:
        """Render plain HTML breadcrumb"""
        if not self.items:
            return '<nav class="breadcrumb"><ol></ol></nav>'

        breadcrumb_items = []
        last_index = len(self.items) - 1

        for i, item in enumerate(self.items):
            label = item.get("label", "")
            url = item.get("url")
            is_active = (i == last_index) or (url is None)

            # First item with home icon
            if i == 0 and self.show_home:
                label = f"🏠 {label}" if label else "🏠"

            # Add separator between items (except first)
            if i > 0:
                breadcrumb_items.append(f'  <li class="breadcrumb-separator">{self.separator}</li>')

            if is_active:
                # Current page (active) - no link
                breadcrumb_items.append(
                    f'  <li class="breadcrumb-item breadcrumb-item-active">{label}</li>'
                )
            else:
                # Link to page
                breadcrumb_items.append(
                    f'  <li class="breadcrumb-item"><a href="{url}">{label}</a></li>'
                )

        breadcrumb_html = "\n".join(breadcrumb_items)

        return f"""<nav class="breadcrumb" aria-label="breadcrumb">
  <ol>
{breadcrumb_html}
  </ol>
</nav>"""
