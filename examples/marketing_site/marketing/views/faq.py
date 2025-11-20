"""
FAQ page view with search and filtering.
"""
from typing import Any, Dict, List

from djust.decorators import debounce, event_handler

from .base import BaseMarketingView


class FAQView(BaseMarketingView):
    """
    FAQ page with live search and category filtering.

    Demonstrates @debounce decorator for search performance.
    """

    template_name = 'marketing/faq.html'
    page_slug = 'faq'

    def mount(self, request: Any, **kwargs: Any) -> None:
        """Initialize FAQ page state."""
        super().mount(request, **kwargs)

        # Search and filter state
        self.search_query = ""
        self.active_category = "all"

        # FAQ categories
        self.categories = [
            'all',
            'getting-started',
            'comparison',
            'performance',
            'deployment',
            'pricing',
        ]

        # FAQ items (from original static HTML)
        self._all_faqs: List[Dict[str, Any]] = [
            {
                'id': 1,
                'category': 'getting-started',
                'question': 'What is djust?',
                'answer': 'djust is a hybrid Python/Rust framework that brings Phoenix LiveView-style reactive server-side rendering to Django. It allows you to build real-time, interactive web applications using only Python—no JavaScript required. The Rust VDOM engine provides blazing-fast rendering (0.8ms) and sub-millisecond diffing (<100μs).',
            },
            {
                'id': 2,
                'category': 'getting-started',
                'question': 'Do I need to know Rust to use djust?',
                'answer': 'No! djust provides a 100% Python API. Rust is only used internally for the VDOM engine and template rendering. You write all your code in Python, just like regular Django. The Rust components are pre-compiled and distributed via pip.',
            },
            {
                'id': 3,
                'category': 'getting-started',
                'question': 'How do I install djust?',
                'answer': 'Simply run pip install djust. This will install djust and all dependencies including Django and Channels. Then follow our Quick Start guide to build your first app in 5 minutes.',
            },
            {
                'id': 4,
                'category': 'comparison',
                'question': 'How is djust different from Phoenix LiveView?',
                'answer': 'djust brings LiveView concepts to Django/Python. Key differences: (1) Rust VDOM engine is faster than Elixir\'s (~40% faster diffing), (2) smaller client bundle (~5KB vs ~30KB), (3) Python instead of Elixir/functional programming, (4) Django ORM integration. Choose djust if you\'re already using Django or prefer Python.',
            },
            {
                'id': 5,
                'category': 'comparison',
                'question': 'How does djust compare to Laravel Livewire?',
                'answer': 'djust is 10x faster (Rust vs PHP rendering), has a 10x smaller bundle (~5KB vs ~50KB), and supports real-time WebSocket updates (not just HTTP polling). Both offer similar developer experience, but djust provides better performance and real-time capabilities. See our detailed comparison.',
            },
            {
                'id': 6,
                'category': 'comparison',
                'question': 'Should I use djust or HTMX?',
                'answer': 'HTMX is framework-agnostic and simpler, but requires more manual wiring. djust provides stateful components, automatic VDOM diffing, real-time WebSocket updates, and Django Forms integration. Choose djust for complex interactive UIs, HTMX for simpler server-driven pages.',
            },
            {
                'id': 7,
                'category': 'performance',
                'question': 'Why is djust so fast?',
                'answer': 'Three reasons: (1) Rust VDOM engine - template rendering in 0.8ms, diffing in <100μs, (2) Minimal client JavaScript - only ~5KB to download, (3) Automatic N+1 query elimination - JIT serialization pattern optimizes database queries. See our benchmarks for details.',
            },
            {
                'id': 8,
                'category': 'performance',
                'question': 'How many concurrent connections can djust handle?',
                'answer': '10,000+ concurrent WebSocket connections per server (4 cores, 8GB RAM). Memory usage is ~2.5 KB per connection thanks to Rust\'s efficient VDOM storage. For higher scale, use Redis backend for horizontal scaling across multiple servers.',
            },
            {
                'id': 9,
                'category': 'deployment',
                'question': 'How do I deploy djust to production?',
                'answer': 'Deploy like any Django app, but use an ASGI server (uvicorn, daphne, hypercorn) instead of WSGI. Example: uvicorn myproject.asgi:application --host 0.0.0.0 --port 8000. Works on Heroku, AWS, Google Cloud, DigitalOcean, Railway, etc. See our deployment guide for platform-specific instructions.',
            },
            {
                'id': 10,
                'category': 'deployment',
                'question': 'Do I need WebSockets in production?',
                'answer': 'WebSockets are optional! You can configure djust to use HTTP-only mode with use_websocket=False in settings. HTTP mode works like Livewire (polling), but you lose real-time push updates. Recommended: use WebSockets for best UX.',
            },
            {
                'id': 11,
                'category': 'deployment',
                'question': 'How do I scale horizontally?',
                'answer': 'Use the Redis state backend: STATE_BACKEND=\'redis\' in settings. This allows multiple servers to share session state. Redis backend uses native Rust MessagePack serialization (5-10x faster than pickle, 30-40% smaller).',
            },
            {
                'id': 12,
                'category': 'getting-started',
                'question': 'Is djust secure?',
                'answer': 'Yes! djust\'s architecture is inherently more secure than traditional SPAs: (1) Zero API attack surface - no REST/GraphQL endpoints to exploit, (2) Server-side business logic - pricing, validation, authorization stays hidden, (3) CSRF protection - built-in Django CSRF, (4) XSS prevention - automatic HTML escaping. See our security page.',
            },
            {
                'id': 13,
                'category': 'pricing',
                'question': 'Is djust free?',
                'answer': 'Yes! djust is MIT-licensed open source, free forever for any use (including commercial). We offer optional Pro ($49/dev/month) and Enterprise (custom) tiers with premium features, priority support, and advanced tooling. See pricing.',
            },
            {
                'id': 14,
                'category': 'pricing',
                'question': 'What do I get with Pro?',
                'answer': 'Pro includes: priority email support (24hr SLA), premium UI component library, advanced debugging tools, performance monitoring dashboard, migration assistance, team collaboration features, and priority feature requests. 14-day free trial available.',
            },
            {
                'id': 15,
                'category': 'getting-started',
                'question': 'Can I use djust with existing Django apps?',
                'answer': 'Absolutely! djust is designed to work alongside traditional Django views. You can incrementally adopt it - start with one interactive page, then add more. It works with existing Django models, forms, authentication, and middleware.',
            },
            {
                'id': 16,
                'category': 'getting-started',
                'question': 'Does djust work with Django REST Framework?',
                'answer': 'Yes, but you usually won\'t need DRF with djust. djust replaces REST APIs for your frontend—business logic runs on the server and you send minimal VDOM patches instead of JSON. Keep DRF for mobile apps or third-party integrations.',
            },
            {
                'id': 17,
                'category': 'getting-started',
                'question': 'Can I use Tailwind/Bootstrap with djust?',
                'answer': 'Yes! djust has built-in framework adapters for Bootstrap 5, Tailwind, and plain HTML. Set css_framework=\'bootstrap5\' or \'tailwind\' in settings. Django Forms automatically render with framework-specific classes.',
            },
            {
                'id': 18,
                'category': 'getting-started',
                'question': 'My WebSocket connection keeps disconnecting. What\'s wrong?',
                'answer': 'Common causes: (1) Nginx/load balancer not configured for WebSockets (add proxy_http_version 1.1 and upgrade headers), (2) session timeout too short (increase SESSION_TTL), (3) firewall blocking WebSocket traffic. Check browser console for error messages.',
            },
            {
                'id': 19,
                'category': 'getting-started',
                'question': 'Where can I get help?',
                'answer': 'Join our Discord community for quick help, check the documentation, or browse GitHub issues. Pro users get priority email support with 24hr SLA. Enterprise users get 24/7 phone & Slack support.',
            },
        ]

        # Initialize filtered results
        self._refresh_faqs()

    @event_handler()
    @debounce(wait=0.3)
    def search(self, value: str = "", **kwargs: Any) -> None:
        """
        Search FAQs with debouncing.

        Waits 300ms after user stops typing before filtering.
        """
        self.search_query = value
        self._refresh_faqs()

    @event_handler()
    def filter_category(self, category: str = "all", **kwargs: Any) -> None:
        """Filter FAQs by category."""
        self.active_category = category
        self._refresh_faqs()

    def _refresh_faqs(self) -> None:
        """Filter FAQs based on search query and category."""
        filtered = self._all_faqs

        # Filter by category
        if self.active_category != "all":
            filtered = [faq for faq in filtered if faq['category'] == self.active_category]

        # Filter by search query
        if self.search_query:
            query_lower = self.search_query.lower()
            filtered = [
                faq for faq in filtered
                if query_lower in faq['question'].lower() or query_lower in faq['answer'].lower()
            ]

        self._filtered_faqs = filtered

    def get_context_data(self, **kwargs: Any) -> Dict[str, Any]:
        """Add FAQ page context."""
        context = super().get_context_data(**kwargs)

        # Expose filtered FAQs (JIT serialization pattern)
        self.faqs = self._filtered_faqs

        # Category display names
        category_names = {
            'all': 'All Questions',
            'getting-started': 'Getting Started',
            'comparison': 'Comparisons',
            'performance': 'Performance',
            'deployment': 'Deployment',
            'pricing': 'Pricing',
        }

        # Provide categories with display names as tuples
        categories_with_names = [
            (cat, category_names.get(cat, cat))
            for cat in self.categories
        ]

        context.update({
            'search_query': self.search_query,
            'active_category': self.active_category,
            'categories': self.categories,
            'categories_with_names': categories_with_names,
            'category_names': category_names,
            'faqs': self.faqs,
            'total_count': len(self._all_faqs),
            'filtered_count': len(self._filtered_faqs),
        })
        return context
