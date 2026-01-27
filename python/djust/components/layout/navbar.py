"""
Navbar component for djust.

A style-independent navigation bar component that adapts to different CSS frameworks.
Similar to shadcn/ui components - framework-agnostic with customizable styles.
"""

from typing import Dict, Any, Optional
from ..base import LiveComponent
from django.utils.safestring import mark_safe
from ...config import config


class NavItem:
    """Represents a navigation item"""

    def __init__(
        self,
        label: str,
        href: str,
        active: bool = False,
        icon: Optional[str] = None,
        external: bool = False,
        target: Optional[str] = None,
        badge: Optional[int] = None,
        badge_variant: str = "primary",
    ):
        self.label = label
        self.href = href
        self.active = active
        self.icon = icon
        self.external = external
        self.target = target or ("_blank" if external else None)
        self.badge = badge
        self.badge_variant = badge_variant


class NavbarComponent(LiveComponent):
    """
    Framework-agnostic navbar component.

    Like shadcn/ui, this component is style-independent and adapts to your CSS framework.
    It works with Bootstrap, Tailwind, or plain HTML/CSS.

    Usage:
        from djust.components.layout import NavbarComponent, NavItem

        # In your LiveView or base template context:
        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context['navbar'] = NavbarComponent(
                brand_name="djust",
                brand_logo="/static/images/djust.png",
                brand_href="/",
                items=[
                    NavItem("Home", "/", active=True),
                    NavItem("Demos", "/demos/"),
                    NavItem("Components", "/kitchen-sink/"),
                    NavItem("Forms", "/forms/"),
                    NavItem("Docs", "/docs/"),
                    NavItem("Hosting", "https://djustlive.com", external=True),
                ],
                fixed_top=True,
                container_fluid=False,
            )
            return context

        # In template:
        {{ navbar.render }}

    The component automatically adapts to your configured CSS framework:
    - Bootstrap 5: Uses navbar-expand-lg, navbar-brand, nav-link classes
    - Tailwind: Uses flex, items-center, px-4 utility classes
    - Plain: Uses semantic HTML with minimal classes for custom CSS
    """

    template_name = None  # Uses inline rendering

    def mount(self, **kwargs):
        """Initialize navbar state"""
        self.brand_name = kwargs.get("brand_name", "App")
        self.brand_logo = kwargs.get("brand_logo", None)
        self.brand_href = kwargs.get("brand_href", "/")
        self.items = kwargs.get("items", [])
        self.fixed_top = kwargs.get("fixed_top", True)
        self.container_fluid = kwargs.get("container_fluid", False)
        self.custom_classes = kwargs.get("custom_classes", "")
        self.logo_height = kwargs.get("logo_height", 16)  # Default to 16px like GitHub

    def get_context_data(self) -> Dict[str, Any]:
        """Get navbar context"""
        return {
            "brand_name": self.brand_name,
            "brand_logo": self.brand_logo,
            "brand_href": self.brand_href,
            "items": self.items,
            "fixed_top": self.fixed_top,
            "container_fluid": self.container_fluid,
            "custom_classes": self.custom_classes,
            "logo_height": self.logo_height,
        }

    def add_item(self, item: NavItem):
        """Add a navigation item"""
        self.items.append(item)
        self.trigger_update()

    def set_active(self, href: str):
        """Set the active navigation item by href"""
        for item in self.items:
            item.active = item.href == href
        self.trigger_update()

    def render(self) -> str:
        """Render navbar with framework-specific styling"""
        framework = config.get("css_framework", "bootstrap5")

        if framework == "bootstrap5":
            return mark_safe(self._render_bootstrap())
        elif framework == "tailwind":
            return mark_safe(self._render_tailwind())
        else:
            return mark_safe(self._render_plain())

    def _render_bootstrap(self) -> str:
        """Render Bootstrap 5 navbar"""
        position_class = "fixed-top" if self.fixed_top else ""
        container_class = "container-fluid" if self.container_fluid else "container"

        # Logo HTML
        logo_html = ""
        if self.brand_logo:
            alt_text = self.brand_name or "Logo"
            margin_style = " margin-right: 0.5rem;" if self.brand_name else ""
            logo_html = f'<img src="{self.brand_logo}" alt="{alt_text}" height="{self.logo_height}" style="width: auto;{margin_style}">'

        # Navigation items
        nav_items_html = ""
        for item in self.items:
            active_class = "active" if item.active else ""
            target_attr = f' target="{item.target}"' if item.target else ""
            icon_html = f"{item.icon} " if item.icon else ""

            # Badge HTML
            badge_html = ""
            if item.badge is not None and item.badge > 0:
                badge_html = (
                    f' <span class="badge bg-{item.badge_variant} rounded-pill">{item.badge}</span>'
                )

            # External link styling
            link_style = ""
            if item.external:
                link_style = ' style="color: #667eea !important; font-weight: 600;"'

            nav_items_html += f"""
                <li class="nav-item">
                    <a class="nav-link {active_class}" href="{item.href}"{target_attr}{link_style}>{icon_html}{item.label}{badge_html}</a>
                </li>
            """

        # Brand name HTML (only if not None)
        brand_name_html = f"<strong>{self.brand_name}</strong>" if self.brand_name else ""

        return f"""
        <nav class="navbar navbar-expand-lg navbar-custom {position_class} {self.custom_classes}" id="{self.component_id}">
            <div class="{container_class}">
                <a class="navbar-brand d-flex align-items-center" href="{self.brand_href}">
                    {logo_html}
                    {brand_name_html}
                </a>
                <div class="navbar-collapse" style="display: flex !important;">
                    <ul class="navbar-nav ms-auto">
                        {nav_items_html}
                    </ul>
                </div>
            </div>
        </nav>
        """

    def _render_tailwind(self) -> str:
        """Render Tailwind CSS navbar"""
        position_classes = "fixed top-0 left-0 right-0 z-50" if self.fixed_top else ""
        container_class = "container mx-auto" if not self.container_fluid else "w-full"

        # Logo HTML
        logo_html = ""
        if self.brand_logo:
            alt_text = self.brand_name or "Logo"
            logo_html = f'<img src="{self.brand_logo}" alt="{alt_text}" class="h-4 w-auto mr-2">'

        # Brand name HTML (only if not None)
        brand_name_html = (
            f'<span class="text-xl font-bold text-gray-900">{self.brand_name}</span>'
            if self.brand_name
            else ""
        )

        # Navigation items
        nav_items_html = ""
        for item in self.items:
            active_class = (
                "text-blue-600 font-semibold"
                if item.active
                else "text-gray-700 hover:text-blue-600"
            )
            target_attr = f' target="{item.target}"' if item.target else ""
            icon_html = f"{item.icon} " if item.icon else ""

            # External link styling
            if item.external:
                active_class = "text-purple-600 font-semibold hover:text-purple-700"

            nav_items_html += f"""
                <a href="{item.href}"{target_attr}
                   class="px-3 py-2 rounded-md text-sm font-medium transition-colors {active_class}">
                    {icon_html}{item.label}
                </a>
            """

        return f"""
        <nav class="bg-white border-b border-gray-200 shadow-sm {position_classes} {self.custom_classes}" id="{self.component_id}">
            <div class="{container_class} px-4">
                <div class="flex items-center justify-between h-16">
                    <a href="{self.brand_href}" class="flex items-center">
                        {logo_html}
                        {brand_name_html}
                    </a>
                    <div class="flex items-center space-x-1">
                        {nav_items_html}
                    </div>
                </div>
            </div>
        </nav>
        """

    def _render_plain(self) -> str:
        """Render plain HTML navbar with semantic classes for custom CSS"""
        position_class = "navbar-fixed" if self.fixed_top else ""

        # Logo HTML
        logo_html = ""
        if self.brand_logo:
            alt_text = self.brand_name or "Logo"
            logo_html = f'<img src="{self.brand_logo}" alt="{alt_text}" class="navbar-logo">'

        # Brand name HTML (only if not None)
        brand_name_html = (
            f'<span class="navbar-brand-text">{self.brand_name}</span>' if self.brand_name else ""
        )

        # Navigation items
        nav_items_html = ""
        for item in self.items:
            active_class = "active" if item.active else ""
            target_attr = f' target="{item.target}"' if item.target else ""
            external_class = "external" if item.external else ""
            icon_html = f'<span class="nav-icon">{item.icon}</span>' if item.icon else ""

            nav_items_html += f"""
                <li class="nav-item {active_class} {external_class}">
                    <a href="{item.href}"{target_attr}>{icon_html}{item.label}</a>
                </li>
            """

        return f"""
        <nav class="navbar {position_class} {self.custom_classes}" id="{self.component_id}">
            <div class="navbar-container">
                <a href="{self.brand_href}" class="navbar-brand">
                    {logo_html}
                    {brand_name_html}
                </a>
                <ul class="navbar-nav">
                    {nav_items_html}
                </ul>
            </div>
        </nav>
        """
