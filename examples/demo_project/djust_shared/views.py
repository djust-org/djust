"""
Base view classes for demo_app.

Provides common components like navbar to all views.
"""

from django.views.generic import TemplateView
from djust.components.layout import NavbarComponent, NavItem


class BaseTemplateView(TemplateView):
    """
    Base TemplateView with navbar component.

    All template-based views should inherit from this to get
    the navbar automatically.
    """

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Add navbar component to all views
        context['navbar'] = self.get_navbar()

        return context

    def get_navbar(self):
        """
        Get the navbar component.

        Override this method in child views to customize the navbar.
        """
        return NavbarComponent(
            brand_name="",  # Empty, we'll show logo only
            brand_logo="/static/images/djust.png",
            brand_href="/",
            items=self.get_nav_items(),
            fixed_top=True,
            logo_height=16,
        )

    def get_nav_items(self):
        """
        Get navigation items.

        Override in child views to customize which item is active.
        """
        return [
            NavItem("Home", "/", active=self.is_active("/")),
            NavItem("Demos", "/demos/", active=self.is_active("/demos/")),
            NavItem("Components", "/kitchen-sink/", active=self.is_active("/kitchen-sink/")),
            NavItem("Forms", "/forms/", active=self.is_active("/forms/")),
            NavItem("Docs", "/docs/", active=self.is_active("/docs/")),
            NavItem("Hosting ↗", "https://djustlive.com", external=True),
        ]

    def is_active(self, path: str) -> bool:
        """Helper to determine if a path is active"""
        if not hasattr(self, 'request'):
            return False

        # Exact match for home
        if path == "/" and self.request.path == "/":
            return True

        # Prefix match for other paths
        if path != "/" and self.request.path.startswith(path):
            return True

        return False


# LiveView base classes
from djust import LiveView


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
