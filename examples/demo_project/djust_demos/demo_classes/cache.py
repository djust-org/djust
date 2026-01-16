"""
Cache Demo - Cached product search
"""

from djust.decorators import cache, debounce
from djust_shared.components.ui import CodeBlock


class CacheDemo:
    """
    Self-contained cache demo with state and code examples.

    State is stored on the parent view to persist across requests.
    """

    PYTHON_CODE = '''from djust.decorators import cache, debounce

PRODUCTS = [
    "Laptop - Dell XPS 13",
    "Mouse - Logitech MX Master",
    "Keyboard - Mechanical RGB",
    # ...
]

@debounce(wait=0.5)
@cache(ttl=300, key_params=["query"])
def cache_search(self, query: str = "", **kwargs):
    if query:
        self.cache_results = [p for p in PRODUCTS
                             if query.lower() in p.lower()]
    self.cache_server_calls += 1'''

    HTML_CODE = '''<input
    type="text"
    @input="cache_search"
    placeholder="Try: laptop, mouse..."
    value="{{ cache_query }}"
>

<div>Server Calls: {{ cache_server_calls }}</div>
<div>Cache Efficiency: {{ cache_efficiency }}%</div>
<div>Results: {{ cache_results|length }}</div>'''

    # Product database
    PRODUCTS = [
        "Laptop - Dell XPS 13",
        "Laptop - MacBook Pro",
        "Mouse - Logitech MX Master",
        "Keyboard - Mechanical RGB",
        "Monitor - 4K Dell",
        "Headphones - Sony WH-1000XM5",
    ]

    def __init__(self, view):
        """Initialize cache demo"""
        self.view = view

        # Initialize state on view if not present
        if not hasattr(view, 'cache_query'):
            view.cache_query = ""
        if not hasattr(view, 'cache_results'):
            view.cache_results = []
        if not hasattr(view, 'cache_server_calls'):
            view.cache_server_calls = 0
        if not hasattr(view, 'cache_total_searches'):
            view.cache_total_searches = 0

    @debounce(wait=0.5)
    @cache(ttl=300, key_params=["query"])
    def cache_search(self, query: str = "", **kwargs):
        """Cached + debounced product search"""
        self.view.cache_query = query
        self.view.cache_total_searches += 1

        if query:
            self.view.cache_results = [
                product for product in self.PRODUCTS
                if query.lower() in product.lower()
            ]
            self.view.cache_server_calls += 1
        else:
            self.view.cache_results = []
            self.view.cache_server_calls += 1

    def get_context(self):
        """Return context for template rendering"""
        # Create CodeBlock components on-the-fly (can't be serialized)
        code_python = CodeBlock(
            code=self.PYTHON_CODE,
            language="python",
            filename="views.py",
            show_header=False
        )
        code_html = CodeBlock(
            code=self.HTML_CODE,
            language="html",
            filename="cache.html",
            show_header=False
        )

        # Calculate cache efficiency
        if self.view.cache_total_searches > 0:
            cache_efficiency = round(
                ((self.view.cache_total_searches - self.view.cache_server_calls) / self.view.cache_total_searches) * 100
            )
        else:
            cache_efficiency = 0

        return {
            'cache_query': self.view.cache_query,
            'cache_results': self.view.cache_results,
            'cache_server_calls': self.view.cache_server_calls,
            'cache_efficiency': cache_efficiency,
            'cache_code_python': code_python,
            'cache_code_html': code_html,
        }
