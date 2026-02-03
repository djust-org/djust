"""
Navigation Demo — URL state management with live_patch and live_redirect.

Demonstrates:
    - live_patch() for URL updates without remounting (query params, history)
    - live_redirect() for navigating to different views
    - handle_params() for responding to URL changes
    - Browser back/forward integration
"""

from djust import LiveView
from djust.decorators import event_handler
from djust.mixins.navigation import NavigationMixin


class NavigationDemoView(NavigationMixin, LiveView):
    """
    Multi-tab interface demonstrating URL state management.
    
    Features:
    - Tab navigation via live_patch (URL updates, no remount)
    - Pagination via live_patch (page numbers in URL)
    - Browser back/forward works correctly
    - Query params reflected in UI
    - Redirect demo to show live_redirect
    """
    
    template_name = "demos/navigation_demo.html"
    
    # Sample data for the tabs
    PRODUCTS = [
        {"id": 1, "name": "Laptop Pro", "category": "electronics", "price": 1299},
        {"id": 2, "name": "Wireless Mouse", "category": "electronics", "price": 49},
        {"id": 3, "name": "USB-C Hub", "category": "electronics", "price": 79},
        {"id": 4, "name": "Mechanical Keyboard", "category": "electronics", "price": 149},
        {"id": 5, "name": "Monitor 27\"", "category": "electronics", "price": 399},
        {"id": 6, "name": "Desk Chair", "category": "furniture", "price": 299},
        {"id": 7, "name": "Standing Desk", "category": "furniture", "price": 549},
        {"id": 8, "name": "Desk Lamp", "category": "furniture", "price": 45},
        {"id": 9, "name": "Bookshelf", "category": "furniture", "price": 189},
        {"id": 10, "name": "Filing Cabinet", "category": "furniture", "price": 129},
        {"id": 11, "name": "Running Shoes", "category": "sports", "price": 129},
        {"id": 12, "name": "Yoga Mat", "category": "sports", "price": 35},
        {"id": 13, "name": "Dumbbells Set", "category": "sports", "price": 89},
        {"id": 14, "name": "Tennis Racket", "category": "sports", "price": 79},
        {"id": 15, "name": "Basketball", "category": "sports", "price": 29},
    ]
    
    CATEGORIES = ["all", "electronics", "furniture", "sports"]
    ITEMS_PER_PAGE = 4

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._init_navigation()

    def mount(self, request, **kwargs):
        """Initialize view state from URL params."""
        # Default state
        self.tab = "browse"
        self.category = "all"
        self.page = 1
        self.search = ""
        self.sort = "name"
        self.detail_id = None
        self.navigation_history = []
        
        # Parse initial URL params
        params = dict(request.GET.items())
        self._apply_params(params)

    def _apply_params(self, params):
        """Apply URL params to view state."""
        if "tab" in params:
            self.tab = params["tab"]
        if "category" in params:
            self.category = params["category"]
        if "page" in params:
            try:
                self.page = max(1, int(params["page"]))
            except (ValueError, TypeError):
                self.page = 1
        if "search" in params:
            self.search = params["search"]
        if "sort" in params:
            self.sort = params["sort"]
        if "detail" in params:
            try:
                self.detail_id = int(params["detail"])
            except (ValueError, TypeError):
                self.detail_id = None

    def handle_params(self, params, uri):
        """Called when URL params change (back/forward, live_patch)."""
        self._apply_params(params)
        self.navigation_history.append(f"Params changed: {params}")
        if len(self.navigation_history) > 5:
            self.navigation_history = self.navigation_history[-5:]

    def get_filtered_products(self):
        """Get products filtered by category and search."""
        products = self.PRODUCTS
        
        # Filter by category
        if self.category != "all":
            products = [p for p in products if p["category"] == self.category]
        
        # Filter by search
        if self.search:
            search_lower = self.search.lower()
            products = [p for p in products if search_lower in p["name"].lower()]
        
        # Sort
        if self.sort == "name":
            products = sorted(products, key=lambda p: p["name"])
        elif self.sort == "price_asc":
            products = sorted(products, key=lambda p: p["price"])
        elif self.sort == "price_desc":
            products = sorted(products, key=lambda p: p["price"], reverse=True)
        
        return products

    def get_paginated_products(self):
        """Get current page of products."""
        products = self.get_filtered_products()
        start = (self.page - 1) * self.ITEMS_PER_PAGE
        end = start + self.ITEMS_PER_PAGE
        return products[start:end]

    def get_total_pages(self):
        """Get total number of pages."""
        products = self.get_filtered_products()
        return max(1, (len(products) + self.ITEMS_PER_PAGE - 1) // self.ITEMS_PER_PAGE)

    def get_context_data(self):
        ctx = super().get_context_data()
        ctx.update({
            "tab": self.tab,
            "category": self.category,
            "page": self.page,
            "search": self.search,
            "sort": self.sort,
            "detail_id": self.detail_id,
            "categories": self.CATEGORIES,
            "products": self.get_paginated_products(),
            "total_pages": self.get_total_pages(),
            "total_products": len(self.get_filtered_products()),
            "navigation_history": self.navigation_history,
            "current_url_params": self._build_current_params(),
        })
        
        # Get detail product if viewing one
        if self.detail_id:
            ctx["detail_product"] = next(
                (p for p in self.PRODUCTS if p["id"] == self.detail_id), None
            )
        
        return ctx

    def _build_current_params(self):
        """Build dict of current URL params for display."""
        params = {"tab": self.tab}
        if self.category != "all":
            params["category"] = self.category
        if self.page > 1:
            params["page"] = self.page
        if self.search:
            params["search"] = self.search
        if self.sort != "name":
            params["sort"] = self.sort
        if self.detail_id:
            params["detail"] = self.detail_id
        return params

    # -------------------------------------------------------------------------
    # Event Handlers — demonstrate live_patch and live_redirect
    # -------------------------------------------------------------------------

    @event_handler
    def switch_tab(self, tab="browse", **kwargs):
        """Switch tabs using live_patch."""
        self.tab = tab
        self.page = 1  # Reset to first page
        self.detail_id = None
        self.live_patch(params={"tab": tab})

    @event_handler
    def filter_category(self, category="all", **kwargs):
        """Filter by category using live_patch."""
        self.category = category
        self.page = 1  # Reset to first page
        self.live_patch(params={
            "tab": self.tab,
            "category": category,
            "sort": self.sort,
        })

    @event_handler
    def change_page(self, page=1, **kwargs):
        """Change page using live_patch."""
        try:
            self.page = max(1, min(int(page), self.get_total_pages()))
        except (ValueError, TypeError):
            self.page = 1
        
        params = {"tab": self.tab, "page": self.page}
        if self.category != "all":
            params["category"] = self.category
        if self.search:
            params["search"] = self.search
        if self.sort != "name":
            params["sort"] = self.sort
        
        self.live_patch(params=params)

    @event_handler
    def change_sort(self, sort="name", **kwargs):
        """Change sort order using live_patch."""
        self.sort = sort
        self.page = 1
        params = {"tab": self.tab, "sort": sort}
        if self.category != "all":
            params["category"] = self.category
        self.live_patch(params=params)

    @event_handler
    def search_products(self, search="", **kwargs):
        """Search products using live_patch."""
        self.search = search
        self.page = 1
        params = {"tab": self.tab}
        if search:
            params["search"] = search
        if self.category != "all":
            params["category"] = self.category
        self.live_patch(params=params)

    @event_handler
    def view_detail(self, product_id=None, **kwargs):
        """View product detail using live_patch (same view, different state)."""
        try:
            self.detail_id = int(product_id) if product_id else None
        except (ValueError, TypeError):
            self.detail_id = None
        
        params = {"tab": "detail", "detail": self.detail_id}
        self.tab = "detail"
        self.live_patch(params=params)

    @event_handler
    def back_to_browse(self, **kwargs):
        """Go back to browse using live_patch."""
        self.detail_id = None
        self.tab = "browse"
        params = {"tab": "browse"}
        if self.category != "all":
            params["category"] = self.category
        if self.page > 1:
            params["page"] = self.page
        self.live_patch(params=params)

    @event_handler
    def clear_filters(self, **kwargs):
        """Clear all filters using live_patch with empty params."""
        self.category = "all"
        self.search = ""
        self.sort = "name"
        self.page = 1
        self.live_patch(params={"tab": self.tab})

    @event_handler
    def demo_redirect(self, **kwargs):
        """Demonstrate live_redirect to a different URL."""
        # This would navigate to a different LiveView without page reload
        # For the demo, we'll just redirect to the same view with different params
        self.live_redirect("/demos/navigation/", params={"tab": "settings"})

    @event_handler
    def replace_history(self, **kwargs):
        """Demonstrate live_patch with replace=True."""
        # Using replace=True means this won't create a new history entry
        self.live_patch(params={"tab": self.tab, "replaced": "true"}, replace=True)
