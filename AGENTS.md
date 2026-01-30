# AGENTS.md — djust Framework

> djust: Phoenix LiveView-style reactive server-side rendering for Django, powered by Rust.
> State changes in Python automatically push minimal DOM patches to the client via WebSocket.
> ~5KB client JS, zero build step.

## Quick Start Pattern

```python
# views.py
from djust import LiveView, event_handler

class CounterView(LiveView):
    template_name = "counter.html"  # or use template_string = "..."

    def mount(self, request, **kwargs):
        self.count = 0

    @event_handler()
    def increment(self):
        self.count += 1  # triggers re-render + VDOM diff + WebSocket patch

# urls.py
from djust.routing import live_urlpatterns
urlpatterns = live_urlpatterns([
    path("counter/", CounterView, name="counter"),
])

# settings.py
INSTALLED_APPS = [..., "djust"]
# Add to ASGI application for WebSocket support
```

```html
<!-- counter.html -->
{% load live_tags %}
<div>
  <h1>Count: {{ count }}</h1>
  <button dj-click="increment">+</button>
</div>
```

## API Index

Format: `NAME | SIGNATURE/USAGE | SOURCE FILE | DESCRIPTION`

### Core Classes

```
LiveView          | class LiveView                              | python/djust/live_view.py        | Base class for reactive server-rendered views
LiveView.mount    | mount(self, request, **kwargs)              | python/djust/live_view.py        | Initialize state on first load
LiveView.render   | render(self, request=None) -> str           | python/djust/live_view.py        | Render template with current state
Component         | class Component                             | python/djust/components/base.py  | Base stateless component
LiveComponent     | class LiveComponent                         | python/djust/components/base.py  | Stateful component with own lifecycle
live_view         | @live_view decorator                        | python/djust/live_view.py        | Function-based LiveView alternative
```

### Decorators

```
@event_handler    | @event_handler(params, description, coerce_types) | python/djust/decorators.py | Mark method as client-callable event handler
@reactive         | @reactive                                         | python/djust/decorators.py | Mark property as reactive (triggers re-render)
@state            | @state(default=None)                              | python/djust/decorators.py | Create stateful property with default
@computed         | @computed                                         | python/djust/decorators.py | Computed property derived from state
@debounce         | @debounce(wait=0.3, max_wait=None)                | python/djust/decorators.py | Debounce handler execution
@throttle         | @throttle(interval, leading, trailing)            | python/djust/decorators.py | Throttle handler execution
@optimistic       | @optimistic                                       | python/djust/decorators.py | Optimistic UI update before server confirms
@cache            | @cache(ttl=300, key_params=["query"])             | python/djust/decorators.py | Cache handler results
@client_state     | @client_state(keys=["filter"])                    | python/djust/decorators.py | Cross-component shared state
@rate_limit       | @rate_limit(rate=100, burst=20)                   | python/djust/decorators.py | Rate limit handler calls
```

### Template HTML Attributes

```
dj-click          | dj-click="handler_name"            | Bind click event to handler
dj-input          | dj-input="handler_name"            | Bind input event (sends value)
dj-change         | dj-change="handler_name"           | Bind change event (sends value)
dj-submit         | dj-submit="handler_name"           | Bind form submit (sends form data)
dj-blur           | dj-blur="handler_name"             | Bind blur event
dj-focus          | dj-focus="handler_name"            | Bind focus event
dj-keydown        | dj-keydown="handler_name"          | Bind keydown event
dj-keyup          | dj-keyup="handler_name"            | Bind keyup event
dj-loading.show   | dj-loading.show                    | Show element while loading
dj-loading.disable| dj-loading.disable                 | Disable element while loading
dj-loading.add-class | dj-loading.add-class="spinner" | Add CSS class while loading
dj-loading.remove-class | dj-loading.remove-class="visible" | Remove CSS class while loading
dj-loading-text   | dj-loading-text="Saving..."        | Replace text while loading
dj-update         | dj-update="append|prepend|replace" | DOM update strategy (default: replace)
dj-id             | dj-id="element_id"                 | Stable element tracking across re-renders
data-*            | data-count="5"                     | Pass data to handlers (auto type-coerced)
```

### Template Tags

```
{% load live_tags %}
{% live_form view %}              | Render entire form with live validation
{% live_field view "field_name" %}| Render single field with live validation
```

### Forms Integration

```
FormMixin         | class FormMixin                    | python/djust/forms.py    | Add Django form support to LiveView
LiveViewForm      | class LiveViewForm(forms.Form)     | python/djust/forms.py    | Enhanced form with live validation
validate_field    | validate_field(field_name, value)   | python/djust/forms.py    | Validate single field on blur/change
validate_form     | validate_form(data)                | python/djust/forms.py    | Validate full form
submit_form       | submit_form(data)                  | python/djust/forms.py    | Handle form submission
as_live           | form.as_live(**kwargs)              | python/djust/forms.py    | Render form with djust bindings
```

### Mixins

```
DraftModeMixin    | class DraftModeMixin               | python/djust/drafts.py         | Auto-save form state to localStorage
RustLiveView      | from djust.mixins.rust_bridge      | python/djust/mixins/rust_bridge.py | Use Rust rendering backend
```

### State Backends

```
InMemoryStateBackend | default backend                 | python/djust/state_backends/   | In-memory state (single process)
RedisStateBackend    | with_compression() supported    | python/djust/state_backends/   | Redis state (multi-process, production)
get_backend          | get_backend()                   | python/djust/state_backends/   | Get configured backend instance
```

### Testing

```
LiveViewTestClient   | client.get(ViewClass, ctx)      | python/djust/testing.py  | Test LiveViews without browser
  .get              | get(view_class, initial_context)  | python/djust/testing.py  | Fetch initial render
  .post             | post(view_class, data)            | python/djust/testing.py  | Send event
  .assert_rendered_contains | assert_rendered_contains(text) | python/djust/testing.py | Assert output
SnapshotTestMixin    | class SnapshotTestMixin          | python/djust/testing.py  | Snapshot testing for templates
MockRequest          | MockRequest()                    | python/djust/testing.py  | Mock Django request for tests
performance_test     | @performance_test()              | python/djust/testing.py  | Performance benchmarking decorator
```

### JavaScript Client (window.djust)

```
sendEvent         | window.djust.sendEvent(handler, params)        | static/djust/client.js | Send event to server
updateState       | window.djust.updateState(key, value)           | static/djust/client.js | Update local state
getState          | window.djust.getState(key)                     | static/djust/client.js | Get local state value
broadcastState    | window.djust.broadcastState(keys, values)      | static/djust/client.js | Broadcast to other components
subscribeToState  | window.djust.subscribeToState(keys, callback)  | static/djust/client.js | Listen for state changes
```

### Configuration (settings.py)

```python
LIVEVIEW_CONFIG = {
    "use_websocket": True,            # WebSocket transport (False = HTTP polling)
    "max_message_size": 65536,        # Max WebSocket message bytes
    "event_security": "strict",       # "open" | "warn" | "strict"
    "hot_reload": True,               # Auto-reload on file changes (dev only)
    "jit_serialization": True,        # JIT-compiled model serialization
    "css_framework": "bootstrap5",    # "bootstrap5" | "tailwind" | None
    "debug_vdom": False,              # Log VDOM diffs
    "rate_limit": {
        "rate": 100, "burst": 20,
        "max_connections_per_ip": 10,
        "reconnect_cooldown": 5,
    },
}
```

### Management Commands

```
cleanup_liveview_sessions | manage.py cleanup_liveview_sessions --ttl=7200 --stats | Clean expired sessions
```

### CLI

```
python -m djust.cli stats    | Show state backend statistics
python -m djust.cli health   | Run health checks
python -m djust.cli analyze  | Analyze templates for optimization
python -m djust.cli clear    | Clear caches
```

## Common Patterns

### Live Search with Debounce + Cache
```python
class SearchView(LiveView):
    template_name = "search.html"
    def mount(self, request): self.query = ""; self.results = []

    @debounce(wait=0.5)
    @cache(ttl=300, key_params=["query"])
    @event_handler()
    def search(self, value: str = "", **kwargs):
        self.query = value
        self.results = Product.objects.filter(name__icontains=value)[:20]
```
```html
<input type="text" dj-input="search" placeholder="Search...">
<ul>{% for item in results %}<li>{{ item.name }}</li>{% endfor %}</ul>
```

### Form with Live Validation
```python
class ContactView(FormMixin, LiveView):
    template_name = "contact.html"
    form_class = ContactForm
    def form_valid(self, form): form.save(); self.success = True
```
```html
{% load live_tags %}
<form dj-submit="submit_form">
  {% live_field view "email" %}
  {% live_field view "message" %}
  <button type="submit" dj-loading.disable dj-loading-text="Sending...">Send</button>
</form>
```

### Multi-Component Shared State
```python
class FilterSidebar(LiveView):
    @client_state(keys=["active_filter"])
    @event_handler()
    def set_filter(self, filter: str = "all", **kwargs):
        self.active_filter = filter

class ProductList(LiveView):
    @client_state(keys=["active_filter"])
    @event_handler()
    def on_filter_change(self, active_filter: str = "all", **kwargs):
        self.products = Product.objects.filter(category=active_filter)
```

### Draft Auto-Save
```python
class EditorView(DraftModeMixin, LiveView):
    draft_enabled = True
    draft_key = "article_editor"
    # Form state auto-saves to localStorage, restored on revisit
```

## Key Concepts

- **LiveView lifecycle**: mount() → render() → [event handler → render() → VDOM diff → patch] loop
- **All state lives on the server** — client JS is only a thin transport layer
- **VDOM diffing** happens in Rust for sub-millisecond performance
- **Event handlers** are Python methods; the `dj-*` attributes bind DOM events to them
- **Type coercion** is automatic: `data-count="5"` arrives as `int` if handler has `count: int`
- **No build step** — include `{% static 'djust/client.js' %}` and you're done
- **WebSocket reconnection** is automatic with exponential backoff
- **Security**: `event_security: "strict"` only allows `@event_handler`-decorated methods
