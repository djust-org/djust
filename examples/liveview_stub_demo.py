"""
Demonstration of LiveView type stub benefits.

This file shows how type stubs for LiveView enable:
1. IDE autocomplete for LiveView mixin methods
2. Type checking to catch errors like 'live_navigate' (nonexistent)
3. Better documentation and discoverability for LiveView APIs

Run with mypy to see type checking in action:
    mypy examples/liveview_stub_demo.py
"""

from djust import LiveView


class ProductListView(LiveView):
    """Demo view showing type-checked LiveView methods."""

    template_name = "products.html"

    def mount(self, request, **kwargs):
        """Initialize state - type checker knows this signature."""
        self.category = "all"
        self.page = 1
        self.products = []

    def get_context_data(self, **kwargs):
        """Get context - type checker knows this returns Dict[str, Any]."""
        context = super().get_context_data(**kwargs)
        context["total"] = len(self.products)
        return context

    # ========================================================================
    # Navigation Examples (live_patch, live_redirect, handle_params)
    # ========================================================================

    def filter_by_category(self, category="all", **kwargs):
        """✅ Correct usage of live_patch - type checker validates."""
        self.category = category
        self.page = 1
        # Type checker knows this signature:
        # live_patch(params=..., path=..., replace=False)
        self.live_patch(params={"category": category, "page": 1})

    def go_to_product_detail(self, product_id, **kwargs):
        """✅ Correct usage of live_redirect - type checker validates."""
        # Type checker knows this signature:
        # live_redirect(path: str, params=..., replace=False)
        self.live_redirect(f"/products/{product_id}/")

    def handle_params(self, params, uri):
        """✅ Override handle_params - type checker knows signature."""
        self.category = params.get("category", "all")
        self.page = int(params.get("page", 1))

    # ========================================================================
    # Push Events Examples
    # ========================================================================

    def save_settings(self, **kwargs):
        """✅ Correct usage of push_event - type checker validates."""
        # Type checker knows this signature:
        # push_event(event: str, payload: Optional[Dict[str, Any]])
        self.push_event("flash", {"message": "Settings saved!", "type": "success"})
        self.push_event("scroll_to", {"selector": "#top"})

    # ========================================================================
    # Streams Examples
    # ========================================================================

    def load_products(self, **kwargs):
        """✅ Correct usage of stream methods - type checker validates."""
        # Type checker knows stream() signature
        self.stream("products", self.get_products(), at=-1)

    def add_product(self, name, **kwargs):
        """✅ Correct usage of stream_insert - type checker validates."""
        product = self.create_product(name)
        # Type checker knows stream_insert(name: str, item: Any, at: int)
        self.stream_insert("products", product, at=0)  # Prepend

    def delete_product(self, product_id, **kwargs):
        """✅ Correct usage of stream_delete - type checker validates."""
        self.remove_product(product_id)
        # Type checker knows stream_delete(name: str, item_or_id: Any)
        self.stream_delete("products", product_id)

    def reset_products(self, **kwargs):
        """✅ Correct usage of stream_reset - type checker validates."""
        products = self.get_all_products()
        # Type checker knows stream_reset(name: str, items: Optional[Any])
        self.stream_reset("products", products)

    # ========================================================================
    # Typo Examples - Would Be Caught by Type Checker
    # ========================================================================

    def example_typos_that_would_be_caught(self):
        """
        These typos would be caught at lint time with type stubs.

        Without stubs, these errors only surface at runtime.
        """
        # ❌ Typo: 'live_navigate' doesn't exist (should be live_redirect)
        # self.live_navigate("/products/")
        # Error: Cannot find reference 'live_navigate' in 'live_view.pyi'

        # ❌ Typo: 'push_events' doesn't exist (should be push_event, singular)
        # self.push_events("flash", {"message": "Test"})
        # Error: Cannot find reference 'push_events' in 'live_view.pyi'

        # ❌ Typo: 'stream_append' doesn't exist (should be stream_insert)
        # self.stream_append("products", item)
        # Error: Cannot find reference 'stream_append' in 'live_view.pyi'

        # ❌ Wrong argument type
        # self.live_patch(params="invalid")  # Should be dict
        # Error: Argument of type 'str' cannot be assigned to parameter 'params' of type 'Optional[Dict[str, Any]]'

        # ❌ Missing required argument
        # self.live_redirect()  # Missing 'path' argument
        # Error: Missing positional argument 'path'

        pass

    # Helper methods for demo
    def get_products(self):
        return []

    def create_product(self, name):
        return {"id": 1, "name": name}

    def remove_product(self, product_id):
        pass

    def get_all_products(self):
        return []


# ============================================================================
# Function-based View Example
# ============================================================================


def demo_function_based_view():
    """Show that live_view decorator is also typed."""
    from djust import live_view

    # Type checker knows this signature:
    # live_view(template_name: Optional[str], template: Optional[str])

    @live_view(template_name="counter.html")
    def counter_view(request):
        count = 0

        def increment():
            nonlocal count
            count += 1

        return locals()

    # ❌ This would be caught by type checker:
    # @live_view(invalid_arg="test.html")
    # Error: No parameter named 'invalid_arg'


if __name__ == "__main__":
    print("=== LiveView Type Stub Demo ===")
    print("\nRun 'mypy examples/liveview_stub_demo.py' to see type checking!\n")

    print("✅ Benefits of LiveView type stubs:")
    print("1. IDE autocomplete for all LiveView methods")
    print("2. Catches typos like 'live_navigate' at lint time (not runtime)")
    print("3. Validates argument types (e.g., params must be dict)")
    print("4. Validates required arguments (e.g., live_redirect needs path)")
    print("5. Better API discoverability and documentation")

    print("\n✅ Methods covered in live_view.pyi:")
    print("   Navigation: live_patch, live_redirect, handle_params")
    print("   Push Events: push_event")
    print("   Streams: stream, stream_insert, stream_delete, stream_reset")
    print("   Lifecycle: mount, get_context_data, handle_tick, get_state")

    print("\n🔍 Try these to see type checking in action:")
    print("   1. Uncomment typo examples in example_typos_that_would_be_caught()")
    print("   2. Run: mypy examples/liveview_stub_demo.py")
    print("   3. See errors caught at lint time, not runtime!")
