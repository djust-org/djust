"""
Server Function Demo (v0.7.0)

Demonstrates:
- `@server_function` — same-origin browser RPC (no re-render, no assigns diff).
- `djust.call(viewSlug, funcName, params)` — the client helper that invokes it.
- `dj-prefetch` — hover/touch intent-based prefetch for fast navigation.
"""

from djust.decorators import server_function
from djust_shared.views import BaseViewWithNavbar


class ServerFunctionDemoView(BaseViewWithNavbar):
    """
    Showcase a direct browser-to-Python RPC without triggering a re-render.

    The ``search_products`` function is decorated with ``@server_function``
    so it is callable from the browser as::

        const hits = await djust.call(
            'demos.serverfunctiondemoview',
            'search_products',
            { q: 'chair' }
        );

    Unlike ``@event_handler``, the call does NOT re-render the view — the
    return value is JSON-serialized straight back to the caller. Perfect for
    typeahead, autocomplete, form-field validators, and other client-side
    UI that wants to consume a server-computed value without rebuilding
    the surrounding DOM.
    """

    template_name = "demos/server_function_demo.html"
    api_name = "demos.server_function_demo"

    # Sample catalog — in a real app this would be a Django queryset.
    CATALOG = [
        {"id": 1, "name": "Ergonomic Chair", "price": 449.00},
        {"id": 2, "name": "Standing Desk", "price": 899.00},
        {"id": 3, "name": "Monitor Arm", "price": 149.00},
        {"id": 4, "name": "USB-C Hub", "price": 79.00},
        {"id": 5, "name": "Mechanical Keyboard", "price": 189.00},
        {"id": 6, "name": "Noise-Cancelling Headphones", "price": 329.00},
        {"id": 7, "name": "Desk Lamp", "price": 59.00},
        {"id": 8, "name": "Coffee Mug", "price": 14.00},
    ]

    def mount(self, request, **kwargs):
        # State on the server — the @server_function does NOT touch this.
        # It's here to prove the "no re-render" contract: invoking
        # djust.call should not modify ``ping_count``.
        self.ping_count = 0

    @server_function
    def search_products(self, q: str = "", **kwargs):
        """Return a filtered product list as JSON — client side handles render."""
        query = (q or "").strip().lower()
        if not query:
            return []
        return [p for p in self.CATALOG if query in p["name"].lower()][:10]

    @server_function
    def get_time(self, **kwargs):
        """Return the server's current ISO timestamp — no side effects."""
        import datetime

        return {"now": datetime.datetime.utcnow().isoformat() + "Z"}
