"""
NavBar component demonstration.

Shows various navbar configurations and use cases.
"""

from djust import LiveView
from djust.components.ui import NavBar


class NavBarDemoView(LiveView):
    """
    Demonstration of NavBar component in various configurations.
    """

    template_name = 'demos/navbar_demo.html'

    def mount(self, request, **kwargs):
        """Initialize navbar examples."""

        # Example 1: Simple light navbar
        self.simple_navbar = NavBar(
            brand={'text': 'SimpleApp', 'url': '/'},
            items=[
                {'label': 'Home', 'url': '/', 'active': True},
                {'label': 'About', 'url': '/about'},
                {'label': 'Contact', 'url': '/contact'},
            ],
            variant='light'
        )

        # Example 2: Dark navbar with logo
        self.dark_navbar = NavBar(
            brand={
                'text': 'DarkApp',
                'url': '/',
                'logo': 'https://via.placeholder.com/30x30'
            },
            items=[
                {'label': 'Dashboard', 'url': '/dashboard', 'active': True},
                {'label': 'Analytics', 'url': '/analytics'},
                {'label': 'Settings', 'url': '/settings'},
            ],
            variant='dark'
        )

        # Example 3: Navbar with dropdowns
        self.dropdown_navbar = NavBar(
            brand={'text': 'EnterpriseApp', 'url': '/'},
            items=[
                {'label': 'Home', 'url': '/', 'active': True},
                {
                    'label': 'Products',
                    'dropdown': [
                        {'label': 'All Products', 'url': '/products'},
                        {'label': 'New Arrivals', 'url': '/products/new'},
                        {'label': 'Best Sellers', 'url': '/products/bestsellers'},
                        {'divider': True},
                        {'label': 'Categories', 'url': '/categories'},
                    ]
                },
                {
                    'label': 'Services',
                    'dropdown': [
                        {'label': 'Consulting', 'url': '/services/consulting'},
                        {'label': 'Development', 'url': '/services/dev'},
                        {'label': 'Support', 'url': '/services/support'},
                    ]
                },
                {'label': 'About', 'url': '/about'},
                {'label': 'Contact', 'url': '/contact'},
            ],
            variant='light'
        )

        # Example 4: Sticky navbar
        self.sticky_navbar = NavBar(
            brand={'text': 'StickyNav', 'url': '/'},
            items=[
                {'label': 'Top of Page', 'url': '#top'},
                {'label': 'Features', 'url': '#features'},
                {'label': 'Pricing', 'url': '#pricing'},
                {'label': 'FAQ', 'url': '#faq'},
            ],
            sticky='top',
            variant='dark'
        )

        # Example 5: Navbar with disabled items
        self.disabled_navbar = NavBar(
            brand={'text': 'BetaApp', 'url': '/'},
            items=[
                {'label': 'Home', 'url': '/', 'active': True},
                {'label': 'Features', 'url': '/features'},
                {'label': 'Coming Soon', 'url': '#', 'disabled': True},
                {
                    'label': 'More',
                    'dropdown': [
                        {'label': 'Available Now', 'url': '/available'},
                        {'label': 'Beta Feature', 'url': '#', 'disabled': True},
                        {'divider': True},
                        {'label': 'Documentation', 'url': '/docs'},
                    ]
                },
            ],
            variant='light'
        )

        # Example 6: Full-featured navbar
        self.full_navbar = NavBar(
            brand={
                'text': 'FullFeatured',
                'url': '/',
                'logo': 'https://via.placeholder.com/40x40'
            },
            items=[
                {'label': 'Dashboard', 'url': '/dashboard', 'active': True},
                {
                    'label': 'Products',
                    'dropdown': [
                        {'label': 'All Products', 'url': '/products'},
                        {'label': 'Categories', 'url': '/categories'},
                        {'divider': True},
                        {'label': 'Add New', 'url': '/products/new'},
                    ]
                },
                {
                    'label': 'Reports',
                    'dropdown': [
                        {'label': 'Sales', 'url': '/reports/sales'},
                        {'label': 'Inventory', 'url': '/reports/inventory'},
                        {'label': 'Analytics', 'url': '/reports/analytics'},
                    ]
                },
                {'label': 'Settings', 'url': '/settings'},
            ],
            variant='dark',
            sticky='top',
            container='fluid',
            expand='lg'
        )

    def get_context_data(self, **kwargs):
        """Return template context."""
        context = super().get_context_data(**kwargs)
        context.update({
            'simple_navbar': self.simple_navbar,
            'dark_navbar': self.dark_navbar,
            'dropdown_navbar': self.dropdown_navbar,
            'sticky_navbar': self.sticky_navbar,
            'disabled_navbar': self.disabled_navbar,
            'full_navbar': self.full_navbar,
        })
        return context
