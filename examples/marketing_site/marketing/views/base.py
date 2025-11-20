"""
Base view for marketing site.

Provides shared functionality:
- Navigation menu data
- GitHub star count
- Active page detection
- Common layout

Two base classes:
- StaticMarketingView: For static pages (Rust rendering, no client.js, ~70% smaller)
- BaseMarketingView: For interactive pages (LiveView with real-time features)
"""
import requests
from djust import LiveView
from django.urls import reverse


class StaticMarketingView(LiveView):
    """
    Base view for static marketing pages (no interactivity needed).

    Use this for: home, features, security, use-cases, comparison, benchmarks
    These pages don't need client.js for interactivity.

    Benefits:
    - Uses Rust template rendering (10-100x faster than Django templates)
    - ~70% smaller HTML (no 63KB client.js inline)
    - Faster page loads
    - Browser can cache static assets better

    Implementation:
    - Inherits from LiveView to get Rust rendering
    - Overrides _inject_client_script() to skip client.js injection
    """

    # Active page slug (override in child views)
    page_slug = 'home'

    def mount(self, request, **kwargs):
        """Initialize shared state for all marketing pages."""
        # Navigation menu items with resolved URLs
        self.nav_items = [
            {'name': 'Home', 'url': reverse('marketing:home'), 'slug': 'home'},
            {'name': 'Features', 'url': reverse('marketing:features'), 'slug': 'features'},
            {'name': 'Security', 'url': reverse('marketing:security'), 'slug': 'security'},
            {'name': 'Examples', 'url': reverse('marketing:examples'), 'slug': 'examples'},
            {'name': 'Playground', 'url': reverse('marketing:playground'), 'slug': 'playground'},
            {'name': 'Comparison', 'url': reverse('marketing:comparison'), 'slug': 'comparison'},
            {'name': 'Benchmarks', 'url': reverse('marketing:benchmarks'), 'slug': 'benchmarks'},
            {'name': 'Use Cases', 'url': reverse('marketing:use_cases'), 'slug': 'use_cases'},
            {'name': 'Pricing', 'url': reverse('marketing:pricing'), 'slug': 'pricing'},
            {'name': 'Quick Start', 'url': reverse('marketing:quickstart'), 'slug': 'quickstart'},
            {'name': 'FAQ', 'url': reverse('marketing:faq'), 'slug': 'faq'},
        ]

        # GitHub star count (cached)
        self.github_stars = self._fetch_github_stars()

        # Active page (override in child views)
        self.active_page = getattr(self, 'page_slug', 'home')

    def _inject_client_script(self, html: str) -> str:
        """
        Override to skip client.js injection for static pages.

        Static pages don't need client.js since they have no interactivity.
        This saves ~63KB per page load.
        """
        return html

    def _fetch_github_stars(self):
        """
        Fetch GitHub star count for djust repository.

        Returns cached value on error to avoid blocking page loads.
        Uses Django's cache framework for 5-minute caching to avoid rate limits.
        """
        from django.conf import settings
        from django.core.cache import cache

        # Return 0 if no GitHub repo configured
        github_repo = getattr(settings, 'GITHUB_REPO', '')
        if not github_repo:
            return 0

        # Try to get from cache first (5-minute cache)
        cached_stars = cache.get('github_stars')
        if cached_stars is not None:
            return cached_stars

        try:
            response = requests.get(
                f'https://api.github.com/repos/{github_repo}',
                timeout=2,
                headers={'Accept': 'application/vnd.github.v3+json'}
            )
            if response.status_code == 200:
                data = response.json()
                stars = data.get('stargazers_count', 0)
                # Cache for 5 minutes (300 seconds)
                cache.set('github_stars', stars, 300)
                return stars
        except Exception:
            pass

        # Return default value on error
        return 0


class BaseMarketingView(LiveView):
    """
    Base view for all marketing pages.

    Provides navigation data and GitHub star count.
    All marketing views should inherit from this.
    """

    # Base template for layout (child views will extend this)
    base_template = 'marketing/base.html'

    def mount(self, request, **kwargs):
        """Initialize shared state for all marketing pages."""
        # Navigation menu items with resolved URLs
        self.nav_items = [
            {'name': 'Home', 'url': reverse('marketing:home'), 'slug': 'home'},
            {'name': 'Features', 'url': reverse('marketing:features'), 'slug': 'features'},
            {'name': 'Security', 'url': reverse('marketing:security'), 'slug': 'security'},
            {'name': 'Examples', 'url': reverse('marketing:examples'), 'slug': 'examples'},
            {'name': 'Playground', 'url': reverse('marketing:playground'), 'slug': 'playground'},
            {'name': 'Comparison', 'url': reverse('marketing:comparison'), 'slug': 'comparison'},
            {'name': 'Benchmarks', 'url': reverse('marketing:benchmarks'), 'slug': 'benchmarks'},
            {'name': 'Use Cases', 'url': reverse('marketing:use_cases'), 'slug': 'use_cases'},
            {'name': 'Pricing', 'url': reverse('marketing:pricing'), 'slug': 'pricing'},
            {'name': 'Quick Start', 'url': reverse('marketing:quickstart'), 'slug': 'quickstart'},
            {'name': 'FAQ', 'url': reverse('marketing:faq'), 'slug': 'faq'},
        ]

        # GitHub star count (cached)
        self.github_stars = self._fetch_github_stars()

        # Active page (override in child views)
        self.active_page = getattr(self, 'page_slug', 'home')

    def _fetch_github_stars(self):
        """
        Fetch GitHub star count for djust repository.

        Returns cached value on error to avoid blocking page loads.
        Uses Django's cache framework for 5-minute caching to avoid rate limits.
        """
        from django.conf import settings
        from django.core.cache import cache

        # Return 0 if no GitHub repo configured
        github_repo = getattr(settings, 'GITHUB_REPO', '')
        if not github_repo:
            return 0

        # Try to get from cache first (5-minute cache)
        cached_stars = cache.get('github_stars')
        if cached_stars is not None:
            return cached_stars

        try:
            response = requests.get(
                f'https://api.github.com/repos/{github_repo}',
                timeout=2,
                headers={'Accept': 'application/vnd.github.v3+json'}
            )
            if response.status_code == 200:
                data = response.json()
                stars = data.get('stargazers_count', 0)
                # Cache for 5 minutes (300 seconds)
                cache.set('github_stars', stars, 300)
                return stars
        except Exception:
            pass

        # Return default value on error
        return 0

    def get_context_data(self, **kwargs):
        """Add shared context data to all pages."""
        context = super().get_context_data(**kwargs)
        context.update({
            'nav_items': self.nav_items,
            'active_page': self.active_page,
            'github_stars': self.github_stars,
        })
        return context
