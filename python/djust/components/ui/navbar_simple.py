"""
Simple NavBar component for djust - stateless, high-performance.

Provides responsive navigation bars for display purposes.
This is a stateless Component optimized for performance.
For interactive navbars with event handlers, use them in LiveView event handlers.
"""

from typing import List, Dict, Optional, Union
from ..base import Component


# Try to import Rust implementation (will be added later)
try:
    from djust._rust import RustNavBar

    _RUST_AVAILABLE = True
except (ImportError, AttributeError):
    _RUST_AVAILABLE = False
    RustNavBar = None


class NavBar(Component):
    """
    Simple, stateless navbar component with automatic Rust optimization.

    This component automatically uses pure Rust implementation if available,
    otherwise falls back to Python rendering.

    Performance:
        - Pure Rust (if available): ~1μs per render
        - Python fallback: ~50-100μs per render (uses loops)

    Use Cases:
        - Responsive navigation bars
        - Brand/logo display
        - Navigation menus with dropdowns
        - Fixed/sticky positioning
        - Light/dark variants

    Note: This is a stateless component for rendering. Event handlers are
    attached in the template using @click directives.

    Args:
        brand: Dict with 'text', 'url', 'logo' keys (optional)
        items: List of navigation item dicts with 'label', 'url', 'active', 'dropdown' keys
        variant: Color variant (light, dark)
        sticky: Use sticky positioning (top, bottom, or False)
        container: Container type (container, fluid)
        expand: Breakpoint for expansion (sm, md, lg, xl, xxl)
        id: Navbar ID for JavaScript control (auto-generated if not provided)

    Examples:
        # Simple navbar
        navbar = NavBar(
            brand={'text': 'MyApp', 'url': '/'},
            items=[
                {'label': 'Home', 'url': '/', 'active': True},
                {'label': 'About', 'url': '/about'},
                {'label': 'Contact', 'url': '/contact'},
            ]
        )

        # With logo
        navbar = NavBar(
            brand={'text': 'MyApp', 'url': '/', 'logo': '/static/logo.png'},
            items=[
                {'label': 'Home', 'url': '/'},
                {'label': 'Products', 'url': '/products'},
            ],
            variant="dark"
        )

        # With dropdown
        navbar = NavBar(
            brand={'text': 'MyApp', 'url': '/'},
            items=[
                {'label': 'Home', 'url': '/', 'active': True},
                {
                    'label': 'Services',
                    'dropdown': [
                        {'label': 'Web Development', 'url': '/services/web'},
                        {'label': 'Mobile Apps', 'url': '/services/mobile'},
                        {'divider': True},
                        {'label': 'Consulting', 'url': '/services/consulting'},
                    ]
                },
                {'label': 'About', 'url': '/about'},
            ]
        )

        # Sticky navbar
        navbar = NavBar(
            brand={'text': 'MyApp', 'url': '/'},
            items=[
                {'label': 'Home', 'url': '/'},
                {'label': 'Features', 'url': '/features'},
            ],
            sticky="top"
        )

        # Dark variant with fluid container
        navbar = NavBar(
            brand={'text': 'MyApp', 'url': '/'},
            items=[
                {'label': 'Dashboard', 'url': '/dashboard', 'active': True},
                {'label': 'Settings', 'url': '/settings'},
            ],
            variant="dark",
            container="fluid"
        )
    """

    # Link to Rust implementation if available
    _rust_impl_class = RustNavBar if _RUST_AVAILABLE else None

    # Note: Not using template because loops are needed for items
    # Using Python _render_custom() which is still fast (~50-100μs)

    def __init__(
        self,
        items: List[Dict[str, Union[str, bool, List[Dict[str, Union[str, bool]]]]]],
        brand: Optional[Dict[str, str]] = None,
        variant: str = "light",
        sticky: Union[str, bool] = False,
        container: str = "fluid",
        expand: str = "lg",
        id: Optional[str] = None,
    ):
        """
        Initialize navbar component.

        Args:
            items: List of navigation item dicts
            brand: Brand/logo dict (optional)
            variant: Color variant (light, dark)
            sticky: Sticky positioning (top, bottom, or False)
            container: Container type (container, fluid)
            expand: Breakpoint for expansion (sm, md, lg, xl, xxl)
            id: Navbar ID (auto-generated if not provided)
        """
        super().__init__(
            items=items,
            brand=brand,
            variant=variant,
            sticky=sticky,
            container=container,
            expand=expand,
            id=id,  # Pass to parent for ID management
        )

        # Store for Python rendering
        self.items = items
        self.brand = brand
        self.variant = variant
        self.sticky = sticky
        self.container = container
        self.expand = expand

    def get_context_data(self):
        """Context for hybrid rendering (if Rust template engine supports loops in future)"""
        return {
            "items": self.items,
            "brand": self.brand,
            "variant": self.variant,
            "sticky": self.sticky,
            "container": self.container,
            "expand": self.expand,
            "id": self.id,  # Use base class id property
        }

    def _render_custom(self) -> str:
        """Custom Python rendering (fallback)"""
        from ...config import config

        framework = config.get("css_framework", "bootstrap5")

        if framework == "bootstrap5":
            return self._render_bootstrap()
        elif framework == "tailwind":
            return self._render_tailwind()
        else:
            return self._render_plain()

    def _render_bootstrap(self) -> str:
        """Render Bootstrap 5 navbar"""
        # Determine navbar classes
        sticky_class = ""
        if self.sticky == "top":
            sticky_class = " sticky-top"
        elif self.sticky == "bottom":
            sticky_class = " sticky-bottom"

        bg_variant = "dark" if self.variant == "dark" else "light"
        data_bs_theme = ' data-bs-theme="dark"' if self.variant == "dark" else ""

        container_class = (
            f"container-{self.container}" if self.container == "fluid" else "container"
        )

        parts = [
            f'<nav class="navbar navbar-expand-{self.expand} bg-{bg_variant}{sticky_class}"{data_bs_theme}>'
        ]
        parts.append(f'    <div class="{container_class}">')

        # Brand
        if self.brand:
            brand_text = self.brand.get("text", "")
            brand_url = self.brand.get("url", "#")
            brand_logo = self.brand.get("logo", "")

            parts.append(f'        <a class="navbar-brand" href="{brand_url}">')
            if brand_logo:
                parts.append(
                    f'            <img src="{brand_logo}" alt="{brand_text}" height="30" class="d-inline-block align-text-top">'
                )
            parts.append(f"            {brand_text}")
            parts.append("        </a>")

        # Toggler button
        parts.append(
            f'        <button class="navbar-toggler" type="button" data-bs-toggle="collapse" data-bs-target="#{self.id}-content" aria-controls="{self.id}-content" aria-expanded="false" aria-label="Toggle navigation">'
        )
        parts.append('            <span class="navbar-toggler-icon"></span>')
        parts.append("        </button>")

        # Navbar content
        parts.append(f'        <div class="collapse navbar-collapse" id="{self.id}-content">')
        parts.append('            <ul class="navbar-nav ms-auto mb-2 mb-lg-0">')

        # Render nav items
        for item in self.items:
            if item.get("dropdown"):
                # Dropdown item
                label = item.get("label", "")
                dropdown_items = item.get("dropdown", [])

                parts.append('                <li class="nav-item dropdown">')
                parts.append(
                    '                    <a class="nav-link dropdown-toggle" href="#" role="button" data-bs-toggle="dropdown" aria-expanded="false">'
                )
                parts.append(f"                        {label}")
                parts.append("                    </a>")
                parts.append('                    <ul class="dropdown-menu">')

                for dropdown_item in dropdown_items:
                    if dropdown_item.get("divider"):
                        parts.append(
                            '                        <li><hr class="dropdown-divider"></li>'
                        )
                    else:
                        dropdown_label = dropdown_item.get("label", "")
                        dropdown_url = dropdown_item.get("url", "#")
                        disabled = dropdown_item.get("disabled", False)
                        disabled_class = " disabled" if disabled else ""
                        disabled_attr = ' aria-disabled="true"' if disabled else ""

                        parts.append(
                            f'                        <li><a class="dropdown-item{disabled_class}" href="{dropdown_url}"{disabled_attr}>{dropdown_label}</a></li>'
                        )

                parts.append("                    </ul>")
                parts.append("                </li>")
            else:
                # Regular nav item
                label = item.get("label", "")
                url = item.get("url", "#")
                active = item.get("active", False)
                disabled = item.get("disabled", False)

                active_class = " active" if active else ""
                disabled_class = " disabled" if disabled else ""
                aria_current = ' aria-current="page"' if active else ""
                disabled_attr = ' aria-disabled="true"' if disabled else ""

                parts.append('                <li class="nav-item">')
                parts.append(
                    f'                    <a class="nav-link{active_class}{disabled_class}" href="{url}"{aria_current}{disabled_attr}>{label}</a>'
                )
                parts.append("                </li>")

        parts.append("            </ul>")
        parts.append("        </div>")
        parts.append("    </div>")
        parts.append("</nav>")

        return "\n".join(parts)

    def _render_tailwind(self) -> str:
        """Render Tailwind CSS navbar"""
        # Determine navbar classes
        sticky_class = ""
        if self.sticky == "top":
            sticky_class = " sticky top-0 z-50"
        elif self.sticky == "bottom":
            sticky_class = " sticky bottom-0 z-50"

        if self.variant == "dark":
            bg_class = "bg-gray-800 text-white"
            link_class = "text-gray-300 hover:bg-gray-700 hover:text-white"
            link_active_class = "bg-gray-900 text-white"
            dropdown_bg = "bg-gray-700"
        else:
            bg_class = "bg-white text-gray-900 shadow"
            link_class = "text-gray-700 hover:bg-gray-100 hover:text-gray-900"
            link_active_class = "bg-gray-200 text-gray-900"
            dropdown_bg = "bg-white"

        container_class = (
            "max-w-full px-4" if self.container == "fluid" else "container mx-auto px-4"
        )

        parts = [f'<nav class="{bg_class}{sticky_class}">']
        parts.append(f'    <div class="{container_class}">')
        parts.append('        <div class="flex justify-between items-center h-16">')

        # Brand
        if self.brand:
            brand_text = self.brand.get("text", "")
            brand_url = self.brand.get("url", "#")
            brand_logo = self.brand.get("logo", "")

            parts.append(f'            <a href="{brand_url}" class="flex items-center space-x-3">')
            if brand_logo:
                parts.append(
                    f'                <img src="{brand_logo}" alt="{brand_text}" class="h-8 w-auto">'
                )
            parts.append(f'                <span class="text-xl font-bold">{brand_text}</span>')
            parts.append("            </a>")

        # Desktop nav items
        parts.append('            <div class="hidden md:flex space-x-1">')

        for item in self.items:
            if item.get("dropdown"):
                # Dropdown item
                label = item.get("label", "")
                dropdown_items = item.get("dropdown", [])

                parts.append('                <div class="relative group">')
                parts.append(
                    f'                    <button class="{link_class} px-3 py-2 rounded-md text-sm font-medium inline-flex items-center">'
                )
                parts.append(f"                        {label}")
                parts.append(
                    '                        <svg class="ml-1 h-4 w-4" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor">'
                )
                parts.append(
                    '                            <path fill-rule="evenodd" d="M5.293 7.293a1 1 0 011.414 0L10 10.586l3.293-3.293a1 1 0 111.414 1.414l-4 4a1 1 0 01-1.414 0l-4-4a1 1 0 010-1.414z" clip-rule="evenodd"/>'
                )
                parts.append("                        </svg>")
                parts.append("                    </button>")
                parts.append(
                    f'                    <div class="absolute left-0 mt-2 w-48 rounded-md shadow-lg {dropdown_bg} ring-1 ring-black ring-opacity-5 invisible group-hover:visible">'
                )
                parts.append('                        <div class="py-1">')

                for dropdown_item in dropdown_items:
                    if dropdown_item.get("divider"):
                        parts.append(
                            '                            <div class="border-t border-gray-200"></div>'
                        )
                    else:
                        dropdown_label = dropdown_item.get("label", "")
                        dropdown_url = dropdown_item.get("url", "#")
                        disabled = dropdown_item.get("disabled", False)
                        disabled_class = " opacity-50 cursor-not-allowed" if disabled else ""

                        parts.append(
                            f'                            <a href="{dropdown_url}" class="{link_class} block px-4 py-2 text-sm{disabled_class}">{dropdown_label}</a>'
                        )

                parts.append("                        </div>")
                parts.append("                    </div>")
                parts.append("                </div>")
            else:
                # Regular nav item
                label = item.get("label", "")
                url = item.get("url", "#")
                active = item.get("active", False)
                disabled = item.get("disabled", False)

                if active:
                    item_class = link_active_class
                else:
                    item_class = link_class

                if disabled:
                    item_class += " opacity-50 cursor-not-allowed"

                parts.append(
                    f'                <a href="{url}" class="{item_class} px-3 py-2 rounded-md text-sm font-medium">{label}</a>'
                )

        parts.append("            </div>")

        # Mobile menu button
        parts.append('            <div class="md:hidden">')
        parts.append(
            f'                <button type="button" class="{link_class} p-2 rounded-md" onclick="document.getElementById(\'{self.id}-mobile\').classList.toggle(\'hidden\')">'
        )
        parts.append(
            '                    <svg class="h-6 w-6" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor">'
        )
        parts.append(
            '                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 12h16M4 18h16" />'
        )
        parts.append("                    </svg>")
        parts.append("                </button>")
        parts.append("            </div>")

        parts.append("        </div>")

        # Mobile menu
        parts.append(f'        <div class="hidden md:hidden" id="{self.id}-mobile">')
        parts.append('            <div class="px-2 pt-2 pb-3 space-y-1">')

        for item in self.items:
            if item.get("dropdown"):
                # Dropdown items in mobile
                label = item.get("label", "")
                dropdown_items = item.get("dropdown", [])

                parts.append(
                    f'                <div class="font-semibold {link_class} block px-3 py-2 rounded-md text-base">{label}</div>'
                )

                for dropdown_item in dropdown_items:
                    if not dropdown_item.get("divider"):
                        dropdown_label = dropdown_item.get("label", "")
                        dropdown_url = dropdown_item.get("url", "#")
                        disabled = dropdown_item.get("disabled", False)
                        disabled_class = " opacity-50 cursor-not-allowed" if disabled else ""

                        parts.append(
                            f'                <a href="{dropdown_url}" class="{link_class} block px-6 py-2 rounded-md text-sm{disabled_class}">{dropdown_label}</a>'
                        )
            else:
                # Regular nav item
                label = item.get("label", "")
                url = item.get("url", "#")
                active = item.get("active", False)
                disabled = item.get("disabled", False)

                if active:
                    item_class = link_active_class
                else:
                    item_class = link_class

                if disabled:
                    item_class += " opacity-50 cursor-not-allowed"

                parts.append(
                    f'                <a href="{url}" class="{item_class} block px-3 py-2 rounded-md text-base font-medium">{label}</a>'
                )

        parts.append("            </div>")
        parts.append("        </div>")

        parts.append("    </div>")
        parts.append("</nav>")

        return "\n".join(parts)

    def _render_plain(self) -> str:
        """Render plain HTML navbar"""
        sticky_class = f" navbar-sticky-{self.sticky}" if self.sticky else ""

        parts = [f'<nav class="navbar navbar-{self.variant}{sticky_class}">']
        parts.append(f'    <div class="navbar-container-{self.container}">')

        # Brand
        if self.brand:
            brand_text = self.brand.get("text", "")
            brand_url = self.brand.get("url", "#")
            brand_logo = self.brand.get("logo", "")

            parts.append(f'        <a href="{brand_url}" class="navbar-brand">')
            if brand_logo:
                parts.append(
                    f'            <img src="{brand_logo}" alt="{brand_text}" class="navbar-logo">'
                )
            parts.append(f"            {brand_text}")
            parts.append("        </a>")

        # Toggle button
        parts.append(
            f"        <button class=\"navbar-toggler\" onclick=\"document.getElementById('{self.id}-menu').classList.toggle('show')\">☰</button>"
        )

        # Nav items
        parts.append(f'        <div class="navbar-menu" id="{self.id}-menu">')
        parts.append('            <ul class="navbar-nav">')

        for item in self.items:
            if item.get("dropdown"):
                # Dropdown item
                label = item.get("label", "")
                dropdown_items = item.get("dropdown", [])

                parts.append('                <li class="nav-item dropdown">')
                parts.append(f'                    <a href="#" class="nav-link">{label} ▼</a>')
                parts.append('                    <ul class="dropdown-menu">')

                for dropdown_item in dropdown_items:
                    if dropdown_item.get("divider"):
                        parts.append('                        <li class="dropdown-divider"></li>')
                    else:
                        dropdown_label = dropdown_item.get("label", "")
                        dropdown_url = dropdown_item.get("url", "#")
                        disabled = dropdown_item.get("disabled", False)
                        disabled_class = " disabled" if disabled else ""

                        parts.append(
                            f'                        <li><a href="{dropdown_url}" class="dropdown-item{disabled_class}">{dropdown_label}</a></li>'
                        )

                parts.append("                    </ul>")
                parts.append("                </li>")
            else:
                # Regular nav item
                label = item.get("label", "")
                url = item.get("url", "#")
                active = item.get("active", False)
                disabled = item.get("disabled", False)

                active_class = " active" if active else ""
                disabled_class = " disabled" if disabled else ""

                parts.append('                <li class="nav-item">')
                parts.append(
                    f'                    <a href="{url}" class="nav-link{active_class}{disabled_class}">{label}</a>'
                )
                parts.append("                </li>")

        parts.append("            </ul>")
        parts.append("        </div>")
        parts.append("    </div>")
        parts.append("</nav>")

        return "\n".join(parts)
