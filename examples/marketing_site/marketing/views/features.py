"""
Features page view.
"""
from .base import StaticMarketingView


class FeaturesView(StaticMarketingView):
    """
    Features page showcasing djust capabilities.

    Features technical deep dive into the architecture:
    - Rust VDOM Engine
    - Unified Templates
    - ORM JIT Compiler
    - Python-First State Management
    """

    template_name = 'marketing/features.html'
    page_slug = 'features'

    def mount(self, request, **kwargs):
        """Initialize features page state."""
        super().mount(request, **kwargs)

        # Hero section
        self.hero_badge = "Technical Deep Dive"
        self.hero_title = "Under the Hood"
        self.hero_description = "Explore the architecture that makes djust the fastest Python LiveView framework."

        # Rust VDOM Engine section (01)
        self.vdom_features = [
            {
                'title': 'Sub-millisecond Patching',
                'description': 'Diffing happens in ~0.8ms, compared to 25ms+ for Django template rendering.',
            },
            {
                'title': 'Smart Diffing',
                'description': 'We track variable dependencies. If you only change <code>{{ count }}</code>, we only check that node.',
            },
        ]

        # Benchmark data for VDOM section
        self.benchmarks = [
            {'name': 'Django Template', 'time': '25.0ms', 'width': '100%', 'color': 'gray-600'},
            {'name': 'Jinja2', 'time': '12.0ms', 'width': '48%', 'color': 'gray-500'},
            {'name': 'djust (Rust)', 'time': '0.8ms', 'width': '3%', 'color': 'brand-rust', 'highlight': True},
        ]

        # Unified Templates section (02)
        self.unified_steps = [
            "Parser resolves <code>{% extends %}</code> and merges blocks.",
            "Merged template is compiled to Rust VDOM.",
            "State changes trigger a diff of the <b>entire tree</b>.",
            "Patches are sent for <b>any</b> changed node, anywhere in the tree.",
        ]

        # ORM JIT Compiler section (03)
        self.orm_features = [
            {
                'title': 'Zero N+1 Queries',
                'description': 'Forgot <code>select_related</code>? No problem. We fix it for you.',
            },
            {
                'title': 'Zero Data Leaks',
                'description': "If a field isn't in the HTML, it's never fetched from the DB.",
            },
        ]

        # State Management section (04)
        self.state_patterns = [
            {
                'decorator': '@debounce(wait=0.5)',
                'color': 'blue-400',
                'title': 'Search & Autocomplete',
                'description': 'Automatically delays the server request until the user stops typing. No more flooding your database with partial queries.',
                'code': '@debounce(0.5)\ndef search(self, query):\n  self.results = DB.search(query)',
            },
            {
                'decorator': '@optimistic',
                'color': 'brand-rust',
                'title': 'Instant Feedback',
                'description': 'Updates the UI immediately in the browser, then validates on the server. If the server rejects it, the UI rolls back automatically.',
                'code': '@optimistic\ndef toggle_like(self):\n  self.liked = not self.liked',
            },
            {
                'decorator': '@client_state',
                'color': 'purple-400',
                'title': 'Component Sync',
                'description': 'Synchronize state between multiple components purely on the client-side, without a server roundtrip.',
                'code': "@client_state(['tab'])\ndef set_tab(self, tab):\n  self.tab = tab",
            },
        ]

    def get_context_data(self, **kwargs):
        """Add features page context."""
        context = super().get_context_data(**kwargs)
        context.update({
            'hero_badge': self.hero_badge,
            'hero_title': self.hero_title,
            'hero_description': self.hero_description,
            'vdom_features': self.vdom_features,
            'benchmarks': self.benchmarks,
            'unified_steps': self.unified_steps,
            'orm_features': self.orm_features,
            'state_patterns': self.state_patterns,
        })
        return context
