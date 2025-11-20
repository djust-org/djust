"""
Benchmarks page view.
"""
from typing import Any, Dict

from .base import StaticMarketingView


class BenchmarksView(StaticMarketingView):
    """
    Benchmarks page showing performance metrics.

    Displays detailed performance benchmarks comparing djust
    with other frameworks and implementations. Content from
    original static HTML site.
    """

    template_name = 'marketing/benchmarks.html'
    page_slug = 'benchmarks'

    def mount(self, request: Any, **kwargs: Any) -> None:
        """Initialize benchmarks page state."""
        super().mount(request, **kwargs)

        # Note: Benchmarks page is static content, no dynamic data needed
        # All benchmark data is displayed directly in the template

    def get_context_data(self, **kwargs: Any) -> Dict[str, Any]:
        """Add benchmarks page context."""
        context = super().get_context_data(**kwargs)
        return context
