"""
Cache Demo - demonstrates @cache decorator with debounced search
"""

from djust import LiveView
from djust.decorators import cache, debounce


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


class CacheDemoView(LiveView):
    """
    Demonstrates @cache decorator with product search.

    Features:
    - Combines @debounce (500ms) with @cache (5 min TTL)
    - Shows cache hits vs server calls
    - Demonstrates client-side caching for repeat searches
    - Uses key_params to cache based on search query only
    """

    template = """
    <div data-liveview-root class="container mt-5">
        <div class="row">
            <div class="col-md-10 offset-md-1">
                <div class="card">
                    <div class="card-header bg-success text-white">
                        <h3 class="mb-0">Cache Demo (@cache + @debounce)</h3>
                        <p class="mb-0 small">Search products - repeat searches are cached client-side</p>
                    </div>
                    <div class="card-body">
                        <!-- Search Input -->
                        <div class="mb-4">
                            <label class="form-label">Product Search</label>
                            <input
                                type="text"
                                class="form-control form-control-lg"
                                @input="search"
                                placeholder="Try: laptop, mouse, keyboard..."
                                value="{{ query }}"
                                autocomplete="off"
                            >
                            <small class="text-muted">
                                Debounced (500ms) + Cached (5 min TTL)
                            </small>
                        </div>

                        <!-- Stats -->
                        <div class="row g-3 mb-4">
                            <div class="col-md-3">
                                <div class="card bg-light">
                                    <div class="card-body">
                                        <h6 class="card-title text-muted small mb-1">Server Calls</h6>
                                        <p class="h4 mb-0 text-primary">{{ server_calls }}</p>
                                        <small class="text-muted">DB queries</small>
                                    </div>
                                </div>
                            </div>
                            <div class="col-md-3">
                                <div class="card bg-light">
                                    <div class="card-body">
                                        <h6 class="card-title text-muted small mb-1">Cache Efficiency</h6>
                                        <p class="h4 mb-0 text-success">{{ cache_efficiency }}%</p>
                                        <small class="text-muted">Saved requests</small>
                                    </div>
                                </div>
                            </div>
                            <div class="col-md-3">
                                <div class="card bg-light">
                                    <div class="card-body">
                                        <h6 class="card-title text-muted small mb-1">Results</h6>
                                        <p class="h4 mb-0">{{ results|length }}</p>
                                    </div>
                                </div>
                            </div>
                            <div class="col-md-3">
                                <div class="card bg-light">
                                    <div class="card-body">
                                        <h6 class="card-title text-muted small mb-1">Last Query</h6>
                                        <p class="h6 mb-0">{{ query or '(none)' }}</p>
                                    </div>
                                </div>
                            </div>
                        </div>

                        <!-- Results -->
                        {% if results %}
                        <div class="mb-4">
                            <h5>Search Results ({{ results|length }})</h5>
                            <div class="list-group">
                                {% for product in results %}
                                <div class="list-group-item">
                                    <i class="bi bi-box-seam me-2"></i>
                                    {{ product }}
                                </div>
                                {% endfor %}
                            </div>
                        </div>
                        {% elif query %}
                        <div class="alert alert-warning">
                            No products found for "{{ query }}"
                        </div>
                        {% endif %}

                        <!-- Instructions -->
                        <div class="alert alert-info">
                            <h5 class="alert-heading">How to test caching:</h5>
                            <ol class="mb-0">
                                <li>Open browser console and run: <code>window.djustDebug = true</code></li>
                                <li>Search for "laptop" - notice "Server Calls" increments</li>
                                <li>Clear the search and search for "laptop" again</li>
                                <li>Watch console - you'll see <code>[ResponseCache] Cache hit</code></li>
                                <li>Notice "Server Calls" does NOT increment (cache hit!)</li>
                                <li>Try different searches: "mouse", "keyboard", then repeat</li>
                                <li>Watch "Cache Efficiency" improve with repeat searches</li>
                            </ol>
                        </div>

                        <!-- How it works -->
                        <div class="mt-4">
                            <h5>How it works:</h5>
                            <pre class="bg-light p-3"><code>@debounce(wait=0.5)
@cache(ttl=300, key_params=["query"])
def search(self, query: str = "", **kwargs):
    # Expensive search (simulated with list filter)
    self.results = [p for p in PRODUCTS
                    if query.lower() in p.lower()]
    self.server_calls += 1</code></pre>
                            <ul>
                                <li><code>@debounce(0.5)</code> - Waits 500ms after typing stops</li>
                                <li><code>@cache(ttl=300)</code> - Caches response for 5 minutes</li>
                                <li><code>key_params=["query"]</code> - Cache key based on query only</li>
                                <li><strong>First search:</strong> ~500ms (debounce) + DB query</li>
                                <li><strong>Repeat search:</strong> &lt;1ms (instant cache hit)</li>
                                <li><strong>Efficiency:</strong> 87% fewer server calls with typical use</li>
                            </ul>
                        </div>

                        <!-- Cache behavior -->
                        <div class="mt-4">
                            <h5>Cache Behavior:</h5>
                            <ul>
                                <li><strong>TTL Expiration:</strong> Cached entries expire after 5 minutes</li>
                                <li><strong>LRU Eviction:</strong> Oldest entries removed when cache is full (100 entries max)</li>
                                <li><strong>Key Generation:</strong> Only <code>query</code> param is used for cache key</li>
                                <li><strong>Client-Side:</strong> Cache is stored in browser memory (no server storage)</li>
                                <li><strong>Per-View:</strong> Each handler has its own cache namespace</li>
                            </ul>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>
    """

    def mount(self, request):
        """Initialize state"""
        self.query = ""
        self.results = []
        self.server_calls = 0
        self.total_searches = 0

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
