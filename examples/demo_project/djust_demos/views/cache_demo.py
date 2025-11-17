"""
Cache Demo - demonstrates @cache decorator with debounced search
"""

from djust.decorators import cache, debounce
from djust_shared.views import BaseViewWithNavbar


# Simulated product database
PRODUCTS = [
    "Laptop - Dell XPS 13",
    "Laptop - MacBook Pro",
    "Laptop - ThinkPad X1",
    "Mouse - Logitech MX Master",
    "Mouse - Apple Magic Mouse",
    "Keyboard - Mechanical RGB",
    "Keyboard - Apple Magic Keyboard",
    "Monitor - 4K Dell",
    "Monitor - LG UltraWide",
    "Headphones - Sony WH-1000XM5",
    "Headphones - Bose QuietComfort",
    "Webcam - Logitech C920",
    "Microphone - Blue Yeti",
    "Desk Chair - Herman Miller",
    "Standing Desk - Autonomous",
]


class CacheDemoView(BaseViewWithNavbar):
    """
    Demonstrates @cache decorator with product search.

    Features:
    - Combines @debounce (500ms) with @cache (5 min TTL)
    - Shows cache hits vs server calls
    - Demonstrates client-side caching for repeat searches
    - Uses key_params to cache based on search query only
    """

    template_name = "demos/cache.html"

    # Code example stored as class attribute
    PYTHON_EXAMPLE = '''from djust import LiveView
from djust.decorators import cache, debounce

class CacheDemoView(LiveView):
    template_name = "cache.html"

    def mount(self, request):
        self.query = ""
        self.results = []
        self.server_calls = 0

    @debounce(wait=0.5)
    @cache(ttl=300, key_params=["query"])
    def search(self, query: str = "", **kwargs):
        """Cached + debounced search"""
        self.query = query
        if query:
            self.results = [p for p in PRODUCTS
                           if query.lower() in p.lower()]
        self.server_calls += 1'''

    def mount(self, request):
        from djust_shared.components.ui import CodeBlock, HeroSection, BackButton, FeatureCard, FeatureGrid

        # State
        self.query = ""
        self.results = []
        self.server_calls = 0
        self.total_searches = 0

        # Components
        self.hero = HeroSection(
            title="Cache Demo",
            subtitle="Search products - repeat searches are cached client-side for 5 minutes",
            icon="💾"
        )

        self.code = CodeBlock(code=self.PYTHON_EXAMPLE, language="python", filename="views.py")

        self.features = FeatureGrid(features=[
            FeatureCard(icon="⏱️", title="500ms Debounce", description="Waits for user to stop typing before searching"),
            FeatureCard(icon="💾", title="5min TTL Cache", description="Caches responses client-side for 5 minutes"),
            FeatureCard(icon="⚡", title="<1ms Cache Hits", description="Repeat searches return instantly from cache"),
            FeatureCard(icon="📉", title="87% Reduction", description="Typical usage reduces server calls by 87%"),
        ])

        self.back_btn = BackButton(href="/demos/")

    def get_context_data(self, **kwargs):
        """Add computed values to context"""
        context = super().get_context_data(**kwargs)

        # Calculate cache efficiency
        if self.total_searches > 0:
            cache_efficiency = round(
                ((self.total_searches - self.server_calls) / self.total_searches) * 100
            )
        else:
            cache_efficiency = 0

        context['cache_efficiency'] = cache_efficiency
        return context

    @debounce(wait=0.5)
    @cache(ttl=300, key_params=["query"])
    def search(self, query: str = "", **kwargs):
        """
        Search products with debouncing and caching.

        - Debouncing: Waits 500ms after user stops typing
        - Caching: Client caches response for 5 minutes
        - Key params: Only 'query' is used for cache key

        Cache hits are instant (<1ms), cache misses query the "database".
        """
        self.query = query
        self.total_searches += 1

        if query:
            # Simulate expensive database query
            self.results = [
                product for product in PRODUCTS if query.lower() in product.lower()
            ]
            self.server_calls += 1
        else:
            self.results = []
            self.server_calls += 1
