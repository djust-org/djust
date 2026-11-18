"""
Example of using the NavbarComponent in djust.

This demonstrates the shadcn-like component approach where components are:
1. Style-independent (work with Bootstrap, Tailwind, or plain CSS)
2. Self-contained (include all their rendering logic)
3. Customizable (can be extended and configured)
"""

from djust import LiveView, state
from djust.components.layout import NavbarComponent, NavItem
from djust.decorators import event_handler


class BaseViewWithNavbar(LiveView):
    """
    Base view that provides a navbar to all child views.

    For LiveView, we need to explicitly provide the navbar since LiveView
    doesn't use Django's context processors.
    """

    exposure_policy = "explicit"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Create navbar for LiveView (context processors don't work with LiveView)
        path = self.request.path if hasattr(self, 'request') else "/"

        nav_items = [
            NavItem("Home", "/", active=(path == "/")),
            NavItem("Demos", "/demos/", active=path.startswith("/demos/")),
            NavItem("Components", "/kitchen-sink/", active=path.startswith("/kitchen-sink/")),
            NavItem("Forms", "/forms/", active=path.startswith("/forms/")),
            NavItem("Docs", "/docs/", active=path.startswith("/docs/")),
            NavItem("Hosting ↗", "https://djustlive.com", external=True),
        ]

        # Render navbar to HTML string (LiveView requires JSON-serializable context)
        navbar_component = NavbarComponent(
            brand_name="",  # Empty, we'll show logo only
            brand_logo="/static/images/djust.png",
            brand_href="/",
            items=nav_items,
            fixed_top=True,
            logo_height=16,
        )
        context['navbar_html'] = navbar_component.render()

        return context

    def is_active(self, path: str) -> bool:
        """Helper to determine if a path is active"""
        if not hasattr(self, 'request'):
            return False
        return self.request.path == path or self.request.path.startswith(path + '/')


# Example view using the navbar
class IndexViewWithNavbar(BaseViewWithNavbar):
    """
    Example view that inherits the navbar from BaseViewWithNavbar.

    The navbar will automatically adapt to your configured CSS framework:
    - If settings.DJUST['css_framework'] = 'bootstrap5' → Bootstrap navbar
    - If settings.DJUST['css_framework'] = 'tailwind' → Tailwind navbar
    - If settings.DJUST['css_framework'] = 'plain' → Plain HTML navbar
    """

    template_name = 'index_with_navbar.html'

    title = state("djust - Reactive Django")
    subtitle = state("Build real-time apps with Python and Rust")


# Alternative: Directly using navbar in a single view
class CustomNavbarView(LiveView):
    """
    Example showing direct navbar usage in a single view.

    This approach gives you more control over navbar configuration
    on a per-view basis.
    """

    exposure_policy = "explicit"
    template_name = 'custom_navbar.html'

    title = state("Custom Navbar Example")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Create a custom navbar with different styling
        navbar = NavbarComponent(
            brand_name="My App",
            brand_logo=None,  # No logo
            brand_href="/",
            items=[
                NavItem("Dashboard", "/dashboard/", active=True),
                NavItem("Settings", "/settings/", icon="⚙️"),
                NavItem("Help", "/help/", icon="❓"),
            ],
            fixed_top=False,  # Not fixed
            custom_classes="bg-dark",  # Add custom classes
        )

        context['navbar'] = navbar
        context['title'] = self.title
        return context


# Example showing dynamic navbar manipulation
class DynamicNavbarView(LiveView):
    """
    Example showing how to dynamically update navbar items.

    This is useful for:
    - Adding items based on user permissions
    - Showing/hiding items based on state
    - Dynamically updating the active item
    """

    exposure_policy = "explicit"
    template_name = 'dynamic_navbar.html'

    # The navbar is built per render from these fields; a component is never
    # view state. persist="server" keeps them across the HTTP fallback.
    current_page = state("Home", persist="server")
    show_admin = state(False, persist="server", render=False)

    _PAGE_URLS = {"Home": "/", "About": "/about/", "Admin": "/admin/"}

    @event_handler
    def add_admin_menu(self, **kwargs):
        """Event handler to add admin menu items"""
        self.show_admin = True

    @event_handler
    def navigate_to(self, page: str = "", **kwargs):
        """Event handler to change active page"""
        if page in self._PAGE_URLS:
            self.current_page = page

    def get_context_data(self, **kwargs):
        active = self._PAGE_URLS.get(self.current_page, "/")
        items = [
            NavItem("Home", "/", active=active == "/"),
            NavItem("About", "/about/", active=active == "/about/"),
        ]
        if self.show_admin:
            items.append(NavItem("Admin", "/admin/", icon="🔧", active=active == "/admin/"))
        navbar = NavbarComponent(brand_name="Dynamic App", brand_href="/", items=items)
        return super().get_context_data(navbar=navbar, **kwargs)
