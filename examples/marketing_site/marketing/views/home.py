"""
Home page view.
"""
from django.views.generic import TemplateView
from django.urls import reverse


class HomeView(TemplateView):
    """
    Homepage showcasing djust framework.

    Features:
    - Hero section with value proposition
    - Key features highlights
    - State management decorators showcase
    - Component system features

    Note: Uses Django TemplateView with DjustTemplateBackend for
    Rust rendering (10-100x faster than Django templates).
    No client.js injection, purely static content.
    """

    template_name = 'marketing/home.html'

    def get_context_data(self, **kwargs):
        """Add homepage context."""
        context = super().get_context_data(**kwargs)

        # Navigation items
        nav_items = [
            {'name': 'Home', 'url': reverse('marketing:home'), 'slug': 'home'},
            {'name': 'Features', 'url': reverse('marketing:features'), 'slug': 'features'},
            {'name': 'Security', 'url': reverse('marketing:security'), 'slug': 'security'},
            {'name': 'Examples', 'url': reverse('marketing:examples'), 'slug': 'examples'},
            {'name': 'Quick Start', 'url': reverse('marketing:quickstart'), 'slug': 'quickstart'},
        ]

        # GitHub stars (mock for now - could add real API call)
        github_stars = 0

        # Hero section data
        hero_badge = "v0.1.0 Alpha Release"
        hero_title = "Client-Side Behavior."
        hero_title_gradient = "Server-Side Code."
        hero_subtitle = "Build reactive, real-time applications with <b>Django</b> and <b>Python</b>. No JavaScript required. Powered by a high-performance <b>Rust VDOM engine</b>."

        # State Management Primitives (4 decorators)
        state_management_decorators = [
            {
                'decorator': '@debounce',
                'color': 'brand-rust',
                'description': 'Delay server requests until the user stops typing. Perfect for search inputs.',
                'code': '@debounce(wait=0.5)\ndef search(self, query):\n  self.results = ...',
            },
            {
                'decorator': '@optimistic',
                'color': 'brand-django',
                'description': 'Update the UI instantly, validate on the server later. Zero latency feel.',
                'code': '@optimistic\ndef like(self):\n  self.liked = True',
            },
            {
                'decorator': '@cache',
                'color': 'blue-400',
                'description': 'Cache responses client-side. Instant results for repeated queries.',
                'code': '@cache(ttl=300)\ndef get_cities(self):\n  return Cities.all()',
            },
            {
                'decorator': '@client_state',
                'color': 'purple-400',
                'description': 'Sync multiple components instantly without a server roundtrip.',
                'code': '@client_state(keys=["tab"])\ndef switch_tab(self, tab):\n  self.tab = tab',
            },
        ]

        # Component System section
        component_features = [
            {
                'title': 'Framework Agnostic',
                'description': 'Switch between Bootstrap 5 and Tailwind CSS with a single config setting.',
            },
            {
                'title': 'Two-Tier Architecture',
                'description': 'Use lightweight <code>Component</code> for static UI and powerful <code>LiveComponent</code> for interactive widgets.',
            },
        ]

        # Update context
        context.update({
            'nav_items': nav_items,
            'active_page': 'home',
            'github_stars': github_stars,
            'hero_badge': hero_badge,
            'hero_title': hero_title,
            'hero_title_gradient': hero_title_gradient,
            'hero_subtitle': hero_subtitle,
            'state_management_decorators': state_management_decorators,
            'component_features': component_features,
        })
        return context
