"""
Components Guide View

An interactive documentation page explaining how components work together in djust.
This page itself is a LiveView that demonstrates the concepts it teaches!
"""

from djust import LiveView
from djust.components.layout import NavbarComponent, NavItem


class ComponentsGuideView(LiveView):
    """
    Interactive guide explaining component architecture.

    This view demonstrates component communication by having:
    - A navbar with a reactive badge
    - Buttons that update state
    - A counter display
    - The badge updates when the counter changes!
    """

    template_name = 'components-guide.html'

    def mount(self, request, **kwargs):
        """Initialize demo counter"""
        self.demo_count = 0

    def increment_demo_count(self):
        """Event handler to increment the demo counter"""
        self.demo_count += 1

    def reset_demo_count(self):
        """Event handler to reset the demo counter"""
        self.demo_count = 0

    def get_context_data(self, **kwargs):
        """Provide navbar and demo count to template"""
        context = super().get_context_data(**kwargs)

        # Create navbar with badge on "Docs" that shows demo_count
        context['navbar'] = NavbarComponent(
            brand_name=None,
            brand_logo="/static/images/djust.png",
            brand_href="/",
            items=[
                NavItem("Home", "/"),
                NavItem("Demos", "/demos/"),
                NavItem("Components", "/kitchen-sink/"),
                NavItem("Forms", "/forms/"),
                NavItem("Docs", "/docs/", badge=self.demo_count, badge_variant="success", active=True),
                NavItem("Hosting ↗", "https://djustlive.com", external=True),
            ],
            fixed_top=True,
            logo_height=28,
        )

        # Pass demo count to template
        context['demo_count'] = self.demo_count

        return context
