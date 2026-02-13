# djust Best Practices

A practical guide to building reactive LiveView applications with djust.

---

## Table of Contents

- [State Management](#state-management)
- [The LiveView Lifecycle](#the-liveview-lifecycle)
- [Event Handlers](#event-handlers)
- [Two-Way Data Binding](#two-way-data-binding)
- [Computed Properties](#computed-properties)
- [Server-Side Updates](#server-side-updates)
- [Performance](#performance)
- [Security](#security)
- [Common UI Patterns](#common-ui-patterns)
- [Forms](#forms)
- [Testing](#testing)
- [Debugging](#debugging)
- [Common Pitfalls](#common-pitfalls)

---

## State Management

### Declare reactive state with `state()`

Use the `state()` descriptor to declare reactive properties at the class level. State is automatically included in the template context and triggers re-renders when changed.

```python
from djust import LiveView, state

class DashboardView(LiveView):
    template_name = "dashboard.html"

    # Declare state at class level with defaults
    services = state(default=[])
    selected_id = state(default="")
    search_query = state(default="")

    def mount(self, request, **kwargs):
        self.services = get_all_statuses()
```

Avoid initializing state only inside `mount()` — declaring it at the class level makes the view's state self-documenting and ensures consistent defaults.

### Public vs. private variables

djust follows a naming convention for what gets exposed to templates:

- **Public** (`self.items`) — automatically available in template context
- **Private** (`self._items`) — internal state, hidden from templates

Use private variables for intermediate data (QuerySets, caches) and only expose what the template needs.

### State Serialization Rules

**Critical**: LiveView state must be JSON-serializable because it's sent over WebSockets and persisted between events. Storing non-serializable objects causes mysterious errors.

#### What's Serializable

These types are safe to store in state:

| Type | Examples | Notes |
|------|----------|-------|
| Primitives | `str`, `int`, `float`, `bool`, `None` | ✓ Always safe |
| Collections | `list`, `dict`, `tuple`, `set` | ✓ Safe if contents are serializable |
| Django models | `User`, `Product`, etc. | ✓ Serialized via JIT (10-100x faster) |
| Django QuerySets | `Product.objects.filter(...)` | ✓ Use private → public pattern |
| Date/time | `datetime`, `date`, `time` | ✓ Auto-converted to ISO strings |
| UUID | `uuid.UUID` | ✓ Auto-converted to string |
| Decimal | `decimal.Decimal` | ✓ Auto-converted to string |

#### What's NOT Serializable

These types will cause serialization failures:

| Type | Examples | Impact |
|------|----------|--------|
| Service instances | `boto3.client()`, `PaymentService()` | ✗ Converts to useless string `"<Client object at 0x...>"` |
| API sessions | `requests.Session()`, `httpx.Client()` | ✗ Loses connection, headers, state |
| Database connections | `connection.cursor()` | ✗ Can't serialize socket |
| File handles | `open(...)`, `io.BytesIO()` | ✗ Can't serialize file descriptor |
| Threads/locks | `threading.Thread()`, `threading.Lock()` | ✗ Not transferable across processes |
| Cached methods | `@cached_property` results with services | ✗ If cached value is non-serializable |

#### Detection & Prevention

djust provides multiple layers of protection:

**1. Runtime validation (DEBUG mode)**

In development, djust detects non-serializable state and raises `TypeError` immediately:

```python
class MyView(LiveView):
    def mount(self, request, **kwargs):
        self.client = boto3.client('s3')  # ✗ TypeError in DEBUG mode
```

```
TypeError: Cannot serialize client=<botocore.client.S3 object at 0x...> in MyView.
Service instances must be created on-demand via helper methods.
See: docs/guides/services.md
```

**2. System check V006**

```bash
python manage.py check --tag djust
```

Static analysis detects service instantiation patterns in `mount()` methods.

**3. Best practice: Helper method pattern**

Instead of storing services in state, create them on-demand:

```python
# ✗ BAD: Stores service in state
class MyView(LiveView):
    def mount(self, request, **kwargs):
        self.s3_client = boto3.client('s3')  # Gets serialized to string!

    @event_handler
    def upload(self, **kwargs):
        self.s3_client.upload_file(...)  # AttributeError: str has no upload_file

# ✓ GOOD: Helper method creates on-demand
class MyView(LiveView):
    def _get_s3_client(self):
        """Create S3 client on-demand. Not stored in state."""
        return boto3.client('s3')

    @event_handler
    def upload(self, **kwargs):
        client = self._get_s3_client()
        client.upload_file(...)  # Works every time
```

For more patterns and examples, see the [Working with External Services](services.md) guide.

---

## The LiveView Lifecycle

Every LiveView follows this lifecycle: **mount → refresh → get_context_data → render**. Understanding this pattern is key to writing correct djust code.

```python
from djust import LiveView
from djust.decorators import event_handler, debounce
from django.db.models import Q

class ProductListView(LiveView):
    template_name = "products/list.html"

    search_query = state(default="")
    filter_category = state(default="all")
    sort_by = state(default="name")

    def mount(self, request, **kwargs):
        """Phase 1: Initialize state, load initial data."""
        self._refresh_products()

    def _refresh_products(self):
        """Phase 2: Build QuerySet and store in private variable."""
        products = Product.objects.all()

        if self.search_query:
            products = products.filter(
                Q(name__icontains=self.search_query)
                | Q(description__icontains=self.search_query)
            )

        if self.filter_category != "all":
            products = products.filter(category=self.filter_category)

        products = products.order_by(self.sort_by)

        # CRITICAL: Store in private variable
        self._products = products

    @event_handler
    @debounce(wait=0.5)
    def search(self, value: str = "", **kwargs):
        """Phase 3: Event handlers update state and refresh."""
        self.search_query = value
        self._refresh_products()

    def get_context_data(self, **kwargs):
        """Phase 4: Move private data to public for JIT serialization."""
        self.products = self._products
        context = super().get_context_data(**kwargs)
        return context
```

### Why the private → public pattern?

Assigning a QuerySet to a public variable inside `get_context_data()` lets djust's Rust JIT serializer handle the conversion — 10-100x faster than Python serialization. This is the single biggest performance optimization in djust.

```python
# GOOD: Let Rust JIT serialize
def get_context_data(self, **kwargs):
    self.products = self._products       # Private → public
    return super().get_context_data(**kwargs)  # Triggers JIT

# BAD: Bypasses JIT
def mount(self, request, **kwargs):
    self.products = Product.objects.all()  # Direct public assignment

# BAD: Disables JIT
def _refresh(self):
    self._products = list(Product.objects.all())  # Converting to list

# BAD: Manual serialization
def get_context_data(self, **kwargs):
    context = super().get_context_data(**kwargs)
    context["products"] = [{"id": p.id, "name": p.name} for p in self._products]
    return context
```

---

## Event Handlers

### Always use `@event_handler` and `**kwargs`

```python
from djust import event_handler

@event_handler
def select_service(self, service_id="", **kwargs):
    """Always include **kwargs — djust sends internal parameters."""
    if service_id not in SERVICES_BY_ID:
        return
    self.selected_service_id = service_id
```

Key rules:

1. **`@event_handler` is required** — without it, the method won't be discoverable as an event handler
2. **Always include `**kwargs`** — djust sends internal parameters like `_targetElement` that must be absorbed
3. **Provide default values** for all parameters — prevents errors when parameters are missing
4. **Use `value` for input/change events** — `dj-input` and `dj-change` send the input value as `value`

### Passing parameters from templates

Use `data-*` attributes. Kebab-case converts to snake_case automatically:

```html
<button dj-click="select_service" data-service-id="keycloak">
    Select Keycloak
</button>
```

`data-service-id` → `service_id` parameter.

### Template event binding reference

```html
<!-- Text input (fires on every keystroke) -->
<input type="text" dj-input="search" value="{{ search_query }}">

<!-- Select/dropdown (fires on selection change) -->
<select dj-change="filter_category">
    <option value="all">All</option>
</select>

<!-- Click with data attributes -->
<button dj-click="delete_item" data-item-id="{{ item.id }}">Delete</button>

<!-- Confirmation dialog -->
<button dj-click="delete_item"
        dj-confirm="Are you sure?"
        data-item-id="{{ item.id }}">
    Delete
</button>

<!-- Form submission -->
<form dj-submit="save_form">
    {% csrf_token %}
    <input name="email" type="email">
    <button type="submit">Save</button>
</form>

<!-- Keyboard events -->
<input dj-keydown="handle_key" value="{{ query }}">

<!-- Focus/blur -->
<input dj-focus="on_focus" dj-blur="on_blur">
```

### Decorator stacking

Decorators modify event handler behavior. Stack them in the right order:

```python
# Search with debouncing (wait for user to stop typing)
@event_handler
@debounce(wait=0.5)
def search(self, value: str = "", **kwargs):
    self.search_query = value
    self._refresh_results()

# Scroll tracking with throttling (limit call rate)
@event_handler
@throttle(interval=0.1)
def on_scroll(self, scroll_y: int = 0, **kwargs):
    self.scroll_position = scroll_y

# Like button with optimistic UI (instant feedback before server confirms)
@event_handler
@optimistic
def like_post(self, post_id: int = 0, **kwargs):
    Post.objects.filter(id=post_id).update(likes=F("likes") + 1)

# Autocomplete with client-side caching
@event_handler
@cache(ttl=300, key_params=["query"])
def autocomplete(self, query: str = "", **kwargs):
    self.suggestions = City.objects.filter(name__istartswith=query)[:10]

# Admin-only action with permission check
@event_handler
@permission_required("myapp.delete_item")
def delete_item(self, item_id: int = 0, **kwargs):
    Item.objects.filter(id=item_id).delete()

# Rate-limited action
@event_handler
@rate_limit(rate=5, burst=3)
def expensive_operation(self, **kwargs):
    pass
```

### When to use which decorator

| Pattern                       | Decorator                   | Typical config |
| ----------------------------- | --------------------------- | -------------- |
| Search/filter input           | `@debounce(wait=0.5)`       | 300-500ms      |
| Scroll/resize/mousemove       | `@throttle(interval=0.1)`   | 100-200ms      |
| Like/toggle/vote              | `@optimistic`               | —              |
| Autocomplete/repeated lookups | `@cache(ttl=300)`           | 60-300s TTL    |
| Multi-component coordination  | `@client_state(keys=[...])` | —              |
| Destructive/admin actions     | `@permission_required(...)` | Django perms   |
| Abuse prevention              | `@rate_limit(rate=N)`       | 5-10 req/s     |
| Long-form editing             | `DraftModeMixin`            | auto-save      |

---

## Two-Way Data Binding

Use `dj-model` to bind form inputs directly to state. No event handler needed.

```html
<input type="text" dj-model="search_query" placeholder="Search...">
```

```python
class MyView(LiveView):
    search_query = state(default="")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        if self.search_query:
            ctx["results"] = search_items(self.search_query)
        return ctx
```

### Modifiers

```html
<!-- Default: syncs on every keystroke -->
<input dj-model="query">

<!-- Lazy: syncs on blur (when user leaves the field) -->
<input dj-model.lazy="query">

<!-- Debounced: syncs after N ms of inactivity -->
<input dj-model.debounce-500="query">
```

### Security

`dj-model` has built-in protections:

- Cannot set private attributes (starting with `_`)
- Cannot set forbidden fields (`template_name`, `request`, etc.)
- Only updates existing attributes (no dynamic attribute creation)
- Type coercion matches the existing attribute type

---

## Computed Properties

Use `@computed` for values derived from state. They're available in templates as properties — no `()` needed.

```python
from djust import computed, state

class DashboardView(LiveView):
    services = state(default=[])

    @computed
    def active_count(self):
        return sum(1 for s in self.services if s["active"])

    @computed
    def percentage_active(self):
        total = len(self.services)
        return (self.active_count / total * 100) if total else 0
```

```html
<p>{{ active_count }} of {{ services|length }} active ({{ percentage_active }}%)</p>
```

For complex derived data that doesn't fit a single property, compute it in `get_context_data()`:

```python
def get_context_data(self, **kwargs):
    ctx = super().get_context_data(**kwargs)
    if self.search_query:
        lines = self.log_content.split("\n")
        ctx["filtered_logs"] = [
            line for line in lines
            if self.search_query.lower() in line.lower()
        ]
    else:
        ctx["filtered_logs"] = self.log_content.split("\n")
    return ctx
```

---

## Server-Side Updates

### Auto-refresh with `tick_interval`

For periodic updates, use server-side ticks — not client-side `setInterval`:

```python
class DashboardView(LiveView):
    tick_interval = 8000  # milliseconds

    def handle_tick(self):
        """Called automatically every 8 seconds."""
        self.services = get_all_statuses()
        self.last_updated = timezone.now()
```

Benefits over client-side polling:

- Server controls refresh rate (can adjust based on load)
- No client-side JavaScript needed
- Automatic WebSocket lifecycle management
- No risk of duplicate events across tabs

### Push updates from outside the view

Use `push_to_view` to push updates from background tasks, signals, or other views:

```python
from djust import push_to_view

# From a Celery task, signal handler, or management command
push_to_view("dashboard", {"services": get_all_statuses()})
```

---

## Performance

### 1. JIT Serialization (most important)

Always use the private → public pattern for QuerySets. See [The LiveView Lifecycle](#the-liveview-lifecycle) above.

### 2. Keyed lists with `data-key`

When rendering lists that can be reordered, filtered, or have items removed, add `data-key` to enable efficient DOM diffing:

```html
<!-- GOOD: Only changed items generate patches -->
{% for task in tasks %}
    <li data-key="{{ task.id }}">{{ task.name }}</li>
{% endfor %}

<!-- BAD: Every shifted item gets rewritten -->
{% for task in tasks %}
    <li>{{ task.name }}</li>
{% endfor %}
```

Skip keys for append-only lists, small static lists (<10 items), or lists that always replace entirely.

### 3. FingerprintMixin for large state

For views with large state dictionaries, `FingerprintMixin` uses MD5 hashes to skip unchanged values — reducing WebSocket bandwidth by 80-90%:

```python
from djust.optimization.fingerprint import FingerprintMixin

class MyView(FingerprintMixin, LiveView):
    fingerprinted_assigns = ["services", "logs", "users"]
```

### 4. Temporary assigns for append-only lists

For collections that grow over time (chat messages, logs), use `temporary_assigns` to clear state after each render. Only new items are sent over the wire.

```python
class ChatView(LiveView):
    temporary_assigns = {"messages": []}

    def mount(self, request, **kwargs):
        self.messages = Message.objects.all()[:50]

    @event_handler
    def send_message(self, content="", **kwargs):
        msg = Message.objects.create(content=content, user=self.request.user)
        self.messages = [msg]  # Only the new message
```

The template must use `dj-update="append"`:

```html
<ul dj-update="append" id="messages">
    {% for msg in messages %}
        <li id="msg-{{ msg.id }}">{{ msg.content }}</li>
    {% endfor %}
</ul>
```

### 5. Loading states

Use `dj-loading.*` attributes for zero-JS loading indicators:

```html
<!-- Disable button while processing -->
<button dj-click="save" dj-loading.disable>Save</button>

<!-- Show spinner during load -->
<div dj-loading.show style="display:none">Loading...</div>

<!-- Hide content during load -->
<div dj-loading.hide>Content here</div>

<!-- Add CSS class during load -->
<div dj-loading.class="opacity-50">Content</div>
```

---

## Security

### Authentication

```python
from djust import LoginRequiredMixin, PermissionRequiredMixin

# Require login
class SecureView(LoginRequiredMixin, LiveView):
    pass

# Or via class attribute
class SecureView(LiveView):
    login_required = True

# Require specific permissions
class AdminView(PermissionRequiredMixin, LiveView):
    permission_required = "myapp.change_settings"
```

### Authorization in event handlers

Always re-verify permissions in event handlers — the initial `mount()` check isn't enough for stateful views:

```python
def mount(self, request, **kwargs):
    if not request.user.is_authenticated:
        raise PermissionDenied("Must be logged in")

@event_handler
@permission_required("myapp.delete_item")
def delete_item(self, item_id: int = 0, **kwargs):
    item = Item.objects.get(id=item_id)
    # Object-level check
    if item.owner != self.request.user:
        raise PermissionDenied("Not authorized")
    item.delete()
```

### Input validation

Always validate user input in event handlers:

```python
@event_handler
def select_service(self, service_id="", **kwargs):
    if service_id not in VALID_SERVICE_IDS:
        return  # Reject invalid input silently

    self.selected_service_id = service_id
```

### Template safety

- `{{ variable }}` is auto-escaped by default — safe against XSS
- Never use `{{ variable|safe }}` on user-controlled data
- Use `format_html()` for server-generated HTML with interpolated values
- Use `json.dumps()` for values injected into JavaScript contexts
- Always include `{% csrf_token %}` in forms

---

## Common UI Patterns

### Modal dialogs

```python
class MyView(LiveView):
    show_modal = state(default=False)
    modal_data = state(default={})

    @event_handler
    def open_modal(self, item_id="", **kwargs):
        self.modal_data = get_item_data(item_id)
        self.show_modal = True

    @event_handler
    def close_modal(self, **kwargs):
        self.show_modal = False
        self.modal_data = {}
```

```html
{% if show_modal %}
<div class="modal-overlay">
    <div class="modal">
        <button dj-click="close_modal">×</button>
        <div>{{ modal_data.name }}</div>
    </div>
</div>
{% endif %}
```

### Tab switching

```python
class MyView(LiveView):
    active_tab = state(default="overview")

    @event_handler
    def switch_tab(self, tab_name="", **kwargs):
        if tab_name in ("overview", "settings", "logs"):
            self.active_tab = tab_name
```

```html
<div class="tabs">
    <button dj-click="switch_tab" data-tab-name="overview"
            class="{% if active_tab == 'overview' %}active{% endif %}">
        Overview
    </button>
    <button dj-click="switch_tab" data-tab-name="settings"
            class="{% if active_tab == 'settings' %}active{% endif %}">
        Settings
    </button>
</div>

{% if active_tab == "overview" %}
    <!-- Overview content -->
{% elif active_tab == "settings" %}
    <!-- Settings content -->
{% endif %}
```

### Toast notifications

```python
class MyView(LiveView):
    toast_message = state(default="")

    @event_handler
    def save_item(self, **kwargs):
        try:
            save_to_database()
            self.toast_message = "Saved successfully!"
        except Exception as e:
            self.toast_message = f"Error: {e}"
```

```html
{% if toast_message %}
<div class="toast">{{ toast_message }}</div>
{% endif %}
```

### Loading states

```python
class MyView(LiveView):
    is_loading = state(default=False)

    @event_handler
    def load_data(self, **kwargs):
        self.is_loading = True
        try:
            self.data = expensive_api_call()
        finally:
            self.is_loading = False
```

```html
{% if is_loading %}
    <div class="spinner">Loading...</div>
{% else %}
    <div>{{ data }}</div>
{% endif %}
```

---

## Forms

Use `FormMixin` for Django form integration with real-time validation:

```python
from djust import LiveView
from djust.forms import FormMixin

class LeaseFormView(FormMixin, LiveView):
    template_name = "leases/form.html"
    form_class = LeaseForm

    def mount(self, request, pk=None, **kwargs):
        # For edits, load model BEFORE super().mount()
        if pk:
            self._model_instance = Lease.objects.get(pk=pk)
        super().mount(request, **kwargs)

    def form_valid(self, form):
        lease = form.save()
        self.success_message = "Lease saved!"
        self.redirect_url = reverse("lease-detail", kwargs={"pk": lease.pk})

    def form_invalid(self, form):
        self.error_message = "Please fix the errors below."
```

```html
<form dj-submit="submit_form">
    {% csrf_token %}
    {{ form.as_p }}
    <button type="submit">Save</button>
</form>
```

---

## Testing

### Test event handlers directly

LiveView event handlers are plain Python methods — test them without HTTP:

```python
from django.test import TestCase, RequestFactory

class DashboardViewTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = User.objects.create_user("test")

    def test_mount_initializes_state(self):
        request = self.factory.get("/")
        request.user = self.user

        view = DashboardView()
        view.mount(request)

        assert view.search_query == ""
        assert view.filter_category == "all"

    def test_search_filters_products(self):
        Product.objects.create(name="Django Book")
        Product.objects.create(name="Rust Book")

        request = self.factory.get("/")
        request.user = self.user

        view = ProductListView()
        view.mount(request)
        view.search(value="Django")

        assert view.search_query == "Django"
        assert view._products.count() == 1

    def test_context_includes_computed(self):
        request = self.factory.get("/")
        request.user = self.user

        view = DashboardView()
        view.mount(request)
        context = view.get_context_data()

        assert "active_count" in context or hasattr(view, "active_count")
```

### Use `LiveViewSmokeTest` for fuzz testing

djust includes a smoke test mixin that automatically tests views for crashes and XSS:

```python
from djust.testing import LiveViewSmokeTest

class MyViewSmokeTests(LiveViewSmokeTest, TestCase):
    view_class = MyView
```

---

## Debugging

### Debug panel

In development (`DEBUG = True`), djust's debug panel shows:

- WebSocket messages
- DOM patches applied
- Event handler calls
- State changes
- Performance metrics

### System checks

```bash
python manage.py check
```

Catches common issues: missing `@event_handler` decorators, unauthenticated views, WebSocket routing problems.

### Logging

```python
import logging
logger = logging.getLogger(__name__)

@event_handler
def my_handler(self, **kwargs):
    logger.debug("Handler called with %s", kwargs)
```

---

## Common Pitfalls

### Service Instances in State

**Problem:** Storing service instances (AWS clients, API sessions, database connections) in LiveView state.

**Why it's wrong:**
- Service instances aren't JSON-serializable — they contain connections, sockets, internal state
- During serialization, they convert to useless strings: `"<Client object at 0x...>"`
- On the next event, methods fail: `AttributeError: 'str' object has no attribute 'upload_file'`
- Silent failure makes debugging extremely difficult

**Solution:**
Use the helper method pattern to create services on-demand:

```python
# ❌ Don't do this
class PaymentView(LiveView):
    def mount(self, request, **kwargs):
        self.stripe_client = stripe.Client(api_key=settings.STRIPE_KEY)
        # After serialization: self.stripe_client becomes a string!

    @event_handler
    def charge_card(self, amount: int = 0, **kwargs):
        self.stripe_client.charges.create(amount=amount)  # AttributeError!

# ✅ Do this instead
class PaymentView(LiveView):
    def _get_stripe_client(self):
        """Create Stripe client on-demand. Not stored in state."""
        return stripe.Client(api_key=settings.STRIPE_KEY)

    @event_handler
    def charge_card(self, amount: int = 0, **kwargs):
        client = self._get_stripe_client()
        client.charges.create(amount=amount)  # Works every time
```

**Related:**
- Runtime check: Raises `TypeError` in DEBUG mode when serialization fails
- System check: `djust.V006` detects service patterns via AST analysis
- Guide: [Working with External Services](services.md)

### Missing `dj-root`

**Problem:** LiveView template has `dj-view` but is missing `dj-root`.

**Why it's wrong:**
- djust requires BOTH attributes to function:
  - `dj-view`: Identifies the LiveView class (for WebSocket connection)
  - `dj-root`: Marks the root element for VDOM patching
- Missing `dj-root` causes confusing error: **"DJE-053: No DOM changes detected"**
- Template renders correctly on initial load but WebSocket updates fail silently

**Solution:**
Add both attributes to the same root element:

```html
<!-- ❌ Don't do this -->
<div dj-view="MyView">
    <h1>{{ title }}</h1>
    <p>{{ content }}</p>
</div>

<!-- ✅ Do this instead -->
<div dj-view="MyView" dj-root>
    <h1>{{ title }}</h1>
    <p>{{ content }}</p>
</div>
```

**With template inheritance:**

```html
<!-- base.html -->
{% extends "base.html" %}

{% block content %}
<div dj-view="MyView" dj-root>
    <!-- CRITICAL: Both attributes on the SAME element -->
    <h1>{{ title }}</h1>
    {% block inner %}{% endblock %}
</div>
{% endblock %}
```

**Related:**
- System check: `djust.T002` detects missing `dj-root` (Warning severity)
- System check: `djust.T005` detects when attributes are on different elements
- Guide: [Template Requirements](template-requirements.md)
- Error code: [DJE-053](error-codes.md#dje-053-no-dom-changes)

### ASGI Configuration Issues

**Problem:** Using Daphne or Uvicorn without proper WebSocket routing in `asgi.py`.

**Why it's wrong:**
- HTTP requests work, but WebSocket connections fail with 404/403
- LiveView loads initially (HTTP) but becomes non-interactive
- Console shows: `WebSocket connection to 'ws://...' failed: Error during WebSocket handshake`
- Django Channels requires explicit WebSocket URL routing

**Solution:**
Use `ProtocolTypeRouter` to route WebSocket connections:

```python
# ❌ Don't do this
# asgi.py
import os
from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
application = get_asgi_application()  # Only handles HTTP!

# ✅ Do this instead
# asgi.py
import os
from django.core.asgi import get_asgi_application
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import AllowedHostsOriginValidator
from djust.routing import websocket_urlpatterns

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')

application = ProtocolTypeRouter({
    "http": get_asgi_application(),
    "websocket": AllowedHostsOriginValidator(
        URLRouter(websocket_urlpatterns)
    ),
})
```

**Related:**
- System check: `djust.C003` warns if WebSocket routing isn't configured
- Deployment guide: See DEPLOYMENT.md for production ASGI setup

### Missing WhiteNoise with Daphne

**Problem:** Using Daphne in production without WhiteNoise for static files.

**Why it's wrong:**
- Daphne doesn't serve static files by default (unlike `runserver`)
- CSS, JS, images return 404 in production
- djust client.js fails to load → no WebSocket connection
- Development works (DEBUG middleware) but production breaks

**Solution:**
Add WhiteNoise middleware:

```python
# settings.py
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',  # Add this!
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    # ... rest of middleware
]

# Recommended: Enable compression and caching
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'
```

Then collect static files before deployment:

```bash
python manage.py collectstatic --noinput
```

**Related:**
- Deployment guide: See DEPLOYMENT.md for full production checklist
- Alternative: Use Nginx/Caddy to serve static files

### Search Without Debouncing

**Problem:** Search input triggers API calls on every keystroke.

**Why it's wrong:**
- Excessive server load: typing "django" = 6 requests (d, dj, dja, djan, djang, django)
- Wasted database queries for incomplete input
- Poor UX: results flash rapidly as user types
- Race conditions: later responses can arrive before earlier ones

**Solution:**
Use `@debounce` to wait for user to stop typing:

```python
# ❌ Don't do this
@event_handler
def search(self, value: str = "", **kwargs):
    """Fires on EVERY keystroke"""
    self.search_query = value
    self._refresh_results()  # Database query every keystroke!

# ✅ Do this instead
@event_handler
@debounce(wait=0.5)  # Wait 500ms after typing stops
def search(self, value: str = "", **kwargs):
    """Fires 500ms after user stops typing"""
    self.search_query = value
    self._refresh_results()  # Only queries when typing stops
```

**Typical debounce values:**
- Search/filter: 300-500ms
- Autocomplete: 200-300ms
- Form validation: 500-1000ms
- Long-form editing (auto-save): 2000-5000ms

**Alternative:** Use `dj-model.debounce-N` in template:

```html
<input type="text" dj-model.debounce-500="search_query" placeholder="Search...">
```

**Related:**
- Decorator: `@throttle(interval=N)` for scroll/resize events (different use case)
- Performance guide: See [Performance](#performance) section above

### Manual client.js Loading

**Problem:** Adding `<script src="{% static 'djust/client.js' %}">` to base template.

**Why it's wrong:**
- djust automatically injects `client.js` for all LiveView pages
- Manual loading causes duplicate initialization
- Race conditions between manual and auto-injected scripts
- Console warnings: "client.js already loaded, skipping duplicate initialization"

**Solution:**
Remove the manual script tag. djust handles it automatically:

```html
<!-- ❌ Don't do this -->
<script src="{% static 'djust/client.js' %}" defer></script>

<!-- ✅ Do this instead -->
<!-- djust auto-injects client.js -->
```

System check `djust.C012` detects this automatically via `python manage.py check`.

### Converting QuerySets to Lists

**Problem:** Converting QuerySets to lists with `list()` or slicing with `[:]`.

**Why it's wrong:**
- JIT serialization requires QuerySets
- Lists force immediate evaluation (memory impact)
- Loses pagination benefits
- No lazy loading in templates

**Solution:**
Always pass QuerySets directly:

```python
# ❌ Don't do this
def get_context_data(self, **kwargs):
    context = super().get_context_data(**kwargs)
    context["items"] = list(self._items)  # Forces evaluation
    return context

# ✅ Do this instead
def get_context_data(self, **kwargs):
    context = super().get_context_data(**kwargs)
    context["items"] = self._items  # Keeps QuerySet
    return context
```

### Missing `data-key` on Dynamic Lists

**Problem:** Rendering dynamic lists without `data-key` attributes.

**Why it's wrong:**
- VDOM can't track identity across updates
- Re-renders entire list instead of updating changed items
- Loss of focus/scroll position
- Animations break

**Solution:**
Always add `data-key` to list items:

```html
<!-- ❌ Don't do this -->
{% for item in items %}
    <div>{{ item.name }}</div>
{% endfor %}

<!-- ✅ Do this instead -->
{% for item in items %}
    <div data-key="{{ item.id }}">{{ item.name }}</div>
{% endfor %}
```

### Missing `**kwargs` in Event Handlers

**Problem:** Event handlers without `**kwargs` parameter.

**Why it's wrong:**
- Breaks when extra params are passed from client
- Not forward-compatible with new framework features
- System check `djust.V003` warns about this

**Solution:**
Always include `**kwargs`:

```python
# ❌ Don't do this
@event_handler
def delete_item(self, item_id: int):
    Item.objects.filter(id=item_id).delete()

# ✅ Do this instead
@event_handler
def delete_item(self, item_id: int = 0, **kwargs):
    Item.objects.filter(id=item_id).delete()
```

### No Default Values for Handler Parameters

**Problem:** Handler parameters without defaults.

**Why it's wrong:**
- Breaks if client doesn't send the parameter
- Not defensive against missing data
- Harder to test

**Solution:**
Provide sensible defaults:

```python
# ❌ Don't do this
@event_handler
def search(self, query: str, **kwargs):
    self.results = search_products(query)

# ✅ Do this instead
@event_handler
def search(self, query: str = "", **kwargs):
    if not query:
        self.results = []
    else:
        self.results = search_products(query)
```

### Exposing Sensitive Data

**Problem:** Exposing sensitive data through public instance variables.

**Why it's wrong:**
- All public variables are sent to client in JIT serialization
- Sensitive data visible in browser DevTools
- Security risk

**Solution:**
Use private variables for sensitive data:

```python
# ❌ Don't do this
def mount(self, request, **kwargs):
    self.user_password_hash = request.user.password  # Exposed!
    self.api_secret = settings.SECRET_KEY  # Exposed!

# ✅ Do this instead
def mount(self, request, **kwargs):
    self._user_password_hash = request.user.password  # Private
    self._api_secret = settings.SECRET_KEY  # Private
```

See the [Security Guide](security.md) for more details.

---

## Checklist

When building a djust LiveView:

- [ ] Declare state with `state(default=...)` at the class level
- [ ] Never store service instances in state — use helper methods instead
- [ ] Add both `dj-view` and `dj-root` to template root element
- [ ] Use `@event_handler` on all event handlers
- [ ] Include `**kwargs` in all event handlers
- [ ] Provide default values for all handler parameters
- [ ] Use `value` as the parameter name for `dj-input`/`dj-change`
- [ ] Store QuerySets in private variables (`self._items`)
- [ ] Assign to public in `get_context_data()` for JIT serialization
- [ ] Call `super().get_context_data()` to trigger Rust serialization
- [ ] Never convert QuerySets to lists
- [ ] Add `data-key` to dynamic list items
- [ ] Use `@debounce` on search/filter inputs to reduce server load
- [ ] Add `{% csrf_token %}` to all forms
- [ ] Validate user input in event handlers
- [ ] Use `dj-confirm` for destructive actions
- [ ] Check authentication/authorization in `mount()` and handlers
- [ ] Configure WebSocket routing in `asgi.py` with `ProtocolTypeRouter`
- [ ] Add WhiteNoise middleware when using Daphne/Uvicorn
- [ ] Run `python manage.py check --tag djust` to catch common issues
