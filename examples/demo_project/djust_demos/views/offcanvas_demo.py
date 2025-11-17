"""
Offcanvas component demonstration.

Shows various offcanvas configurations and use cases.
"""

from djust import LiveView
from djust.components.ui import Offcanvas


class OffcanvasDemoView(LiveView):
    """
    Demonstration of Offcanvas component in various configurations.
    """

    template_name = 'demos/offcanvas_demo.html'

    def mount(self, request, **kwargs):
        """Initialize offcanvas examples."""

        # Example 1: Simple left sidebar
        self.left_offcanvas = Offcanvas(
            title="Navigation Menu",
            body="""
            <nav>
                <ul class="nav flex-column">
                    <li class="nav-item"><a class="nav-link active" href="/">Home</a></li>
                    <li class="nav-item"><a class="nav-link" href="/about">About</a></li>
                    <li class="nav-item"><a class="nav-link" href="/services">Services</a></li>
                    <li class="nav-item"><a class="nav-link" href="/contact">Contact</a></li>
                </ul>
            </nav>
            """,
            placement="start",
            id="leftSidebar"
        )

        # Example 2: Right sidebar for filters
        self.right_offcanvas = Offcanvas(
            title="Filters",
            body="""
            <form>
                <div class="mb-3">
                    <label for="priceRange" class="form-label">Price Range</label>
                    <input type="range" class="form-range" id="priceRange" min="0" max="100">
                </div>
                <div class="mb-3">
                    <label for="category" class="form-label">Category</label>
                    <select class="form-select" id="category">
                        <option>Electronics</option>
                        <option>Clothing</option>
                        <option>Books</option>
                    </select>
                </div>
                <div class="mb-3">
                    <div class="form-check">
                        <input class="form-check-input" type="checkbox" id="inStock">
                        <label class="form-check-label" for="inStock">In Stock Only</label>
                    </div>
                </div>
                <button type="submit" class="btn btn-primary w-100">Apply Filters</button>
            </form>
            """,
            placement="end",
            id="rightFilters"
        )

        # Example 3: Top drawer for notifications
        self.top_offcanvas = Offcanvas(
            title="Notifications",
            body="""
            <div class="list-group">
                <a href="#" class="list-group-item list-group-item-action">
                    <div class="d-flex w-100 justify-content-between">
                        <h6 class="mb-1">New message received</h6>
                        <small>5 mins ago</small>
                    </div>
                    <p class="mb-1">You have a new message from John Doe.</p>
                </a>
                <a href="#" class="list-group-item list-group-item-action">
                    <div class="d-flex w-100 justify-content-between">
                        <h6 class="mb-1">System update</h6>
                        <small>1 hour ago</small>
                    </div>
                    <p class="mb-1">New features are now available.</p>
                </a>
                <a href="#" class="list-group-item list-group-item-action">
                    <div class="d-flex w-100 justify-content-between">
                        <h6 class="mb-1">Security alert</h6>
                        <small>3 hours ago</small>
                    </div>
                    <p class="mb-1">Your password will expire in 7 days.</p>
                </a>
            </div>
            """,
            placement="top",
            id="topNotifications"
        )

        # Example 4: Bottom drawer for shopping cart
        self.bottom_offcanvas = Offcanvas(
            title="Shopping Cart",
            body="""
            <div class="cart-items">
                <div class="d-flex justify-content-between align-items-center mb-3 pb-3 border-bottom">
                    <div>
                        <h6 class="mb-0">Product 1</h6>
                        <small class="text-muted">$29.99</small>
                    </div>
                    <div>
                        <span class="badge bg-secondary">Qty: 2</span>
                    </div>
                </div>
                <div class="d-flex justify-content-between align-items-center mb-3 pb-3 border-bottom">
                    <div>
                        <h6 class="mb-0">Product 2</h6>
                        <small class="text-muted">$49.99</small>
                    </div>
                    <div>
                        <span class="badge bg-secondary">Qty: 1</span>
                    </div>
                </div>
                <div class="d-flex justify-content-between align-items-center pt-3">
                    <h5>Total:</h5>
                    <h5>$109.97</h5>
                </div>
                <button class="btn btn-success w-100 mt-3">Proceed to Checkout</button>
            </div>
            """,
            placement="bottom",
            id="bottomCart"
        )

        # Example 5: Offcanvas without backdrop
        self.no_backdrop_offcanvas = Offcanvas(
            title="Settings Panel",
            body="""
            <form>
                <div class="mb-3">
                    <label for="themeSwitch" class="form-label">Theme</label>
                    <select class="form-select" id="themeSwitch">
                        <option>Light</option>
                        <option>Dark</option>
                        <option>Auto</option>
                    </select>
                </div>
                <div class="mb-3">
                    <label for="fontSizeSlider" class="form-label">Font Size</label>
                    <input type="range" class="form-range" id="fontSizeSlider" min="12" max="24" value="16">
                </div>
                <div class="mb-3">
                    <div class="form-check form-switch">
                        <input class="form-check-input" type="checkbox" id="notificationsSwitch" checked>
                        <label class="form-check-label" for="notificationsSwitch">Enable Notifications</label>
                    </div>
                </div>
                <div class="mb-3">
                    <div class="form-check form-switch">
                        <input class="form-check-input" type="checkbox" id="autoSaveSwitch" checked>
                        <label class="form-check-label" for="autoSaveSwitch">Auto Save</label>
                    </div>
                </div>
                <button type="submit" class="btn btn-primary w-100">Save Settings</button>
            </form>
            """,
            placement="end",
            backdrop=False,
            id="noBackdropSettings"
        )

        # Example 6: Offcanvas without dismiss button
        self.no_dismiss_offcanvas = Offcanvas(
            title="Important Information",
            body="""
            <div class="alert alert-info">
                <h5 class="alert-heading">Read Carefully!</h5>
                <p>This is important information that should be read completely.</p>
                <hr>
                <p class="mb-0">Please click the button below to acknowledge.</p>
            </div>
            <p>This offcanvas doesn't have a dismiss button in the header. You must use the button below to close it.</p>
            <button class="btn btn-primary w-100 mt-3" data-bs-dismiss="offcanvas">I Understand</button>
            """,
            placement="start",
            dismissable=False,
            id="noDismissInfo"
        )

    def get_context_data(self, **kwargs):
        """Return template context."""
        context = super().get_context_data(**kwargs)
        context.update({
            'left_offcanvas': self.left_offcanvas,
            'right_offcanvas': self.right_offcanvas,
            'top_offcanvas': self.top_offcanvas,
            'bottom_offcanvas': self.bottom_offcanvas,
            'no_backdrop_offcanvas': self.no_backdrop_offcanvas,
            'no_dismiss_offcanvas': self.no_dismiss_offcanvas,
        })
        return context
