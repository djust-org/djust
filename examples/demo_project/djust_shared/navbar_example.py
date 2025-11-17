"""
Example of using the NavbarComponent in djust.

This demonstrates the shadcn-like component approach where components are:
1. Style-independent (work with Bootstrap, Tailwind, or plain CSS)
2. Self-contained (include all their rendering logic)
3. Customizable (can be extended and configured)
"""

from djust import LiveView
from djust.components.layout import NavbarComponent, NavItem


class BaseViewWithNavbar(LiveView):
    """
    Base view that provides a navbar to all child views.

    For LiveView, we need to explicitly provide the navbar since LiveView
    doesn't use Django's context processors.
    """

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

    def mount(self, request, **kwargs):
        self.title = "djust - Reactive Django"
        self.subtitle = "Build real-time apps with Python and Rust"


# Alternative: Directly using navbar in a single view
class CustomNavbarView(LiveView):
    """
    Example showing direct navbar usage in a single view.

    This approach gives you more control over navbar configuration
    on a per-view basis.
    """

    template_name = 'custom_navbar.html'

    def mount(self, request, **kwargs):
        self.title = "Custom Navbar Example"

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

    template_name = 'dynamic_navbar.html'

    def mount(self, request, **kwargs):
        self.current_page = "Home"

        # Create base navbar
        self.navbar = NavbarComponent(
            brand_name="Dynamic App",
            brand_href="/",
            items=[
                NavItem("Home", "/", active=True),
                NavItem("About", "/about/"),
            ],
        )

    def add_admin_menu(self):
        """Event handler to add admin menu items"""
        self.navbar.add_item(NavItem("Admin", "/admin/", icon="🔧"))

    def navigate_to(self, page: str):
        """Event handler to change active page"""
        self.current_page = page

        # Update active item
        page_urls = {
            "Home": "/",
            "About": "/about/",
            "Admin": "/admin/",
        }

        if page in page_urls:
            self.navbar.set_active(page_urls[page])

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['navbar'] = self.navbar
        context['current_page'] = self.current_page
        return context
