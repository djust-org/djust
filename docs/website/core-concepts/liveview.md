# LiveView

The `LiveView` class is the core building block of djust. It combines a Django class-based view with a persistent WebSocket session that keeps state on the server.

## Basic Structure

```python
from djust import LiveView
from djust.decorators import event_handler


class MyView(LiveView):
    template_name = "myapp/my_view.html"

    def mount(self, request, **kwargs):
        """Initialize state. Public attributes reach the template automatically."""
        self.count = 0
        self.items = []

    @event_handler()
    def increment(self, **kwargs):
        """Handle a dj-click="increment" event."""
        self.count += 1
```

## Lifecycle Hooks

### `mount(request, **kwargs)`

Called when the LiveView is first rendered over HTTP, and **again** when the WebSocket connects (unless state is restored from a snapshot). Keep it idempotent — no one-time side effects. Use this to:

- Initialize state variables
- Read URL parameters from `**kwargs`
- Fetch initial data from the database

```python
def mount(self, request, **kwargs):
    item_id = kwargs.get("item_id")
    self.item = Item.objects.get(pk=item_id)
    self.editing = False
```

After `mount()`, every state change triggers a re-render automatically.

### `get_context_data(**kwargs)`

Called before **every** render — both the initial HTTP render and every WebSocket update. Returns the template context dictionary.

Always call `super().get_context_data(**kwargs)` to include djust's required context:

```python
def get_context_data(self, **kwargs):
    context = super().get_context_data(**kwargs)
    context.update({
        "items": self.items,
        "count": self.count,
    })
    return context
```

### `handle_params(params, uri)`

Called after `mount()` on the initial render, and again on every URL change (`live_patch()`, browser back/forward) without a full page reload. Use it to derive state from query params:

```python
def handle_params(self, params, uri):
    self.page = int(params.get("page", 1))
    self._refresh()
```

### `handle_info(message)`

Called with out-of-band messages delivered to the view. Currently these are PostgreSQL `NOTIFY` events for views that subscribe with `self.listen(channel)` (`NotificationMixin`). The single argument is a dict:

```python
def handle_info(self, message):
    if message["type"] == "db_notify":
        self.refresh()
```

> **Known issue: #2962.** At 1.2.0rc10, calling `self.listen()` inside `mount()` never subscribes. Declare the channels at class level with `_listen_channels` instead.

## State Management

State lives on `self`. Any public attribute (`self.count`) is:

- Included in the template context
- Preserved across WebSocket events
- Re-rendered when changed

Private attributes (prefixed with `_`) are kept out of the template context and never sent to the client. Those set in `mount()` are still saved with the view's server-side state when they are JSON-serializable (models are stored as refs). Other values, such as QuerySets, are dropped on restore, so re-derive them rather than caching them in `_` attributes:

```python
def mount(self, request, **kwargs):
    self._db_items = Item.objects.all()  # private — not in the template, not restored
    self.count = self._db_items.count()  # public — available in template
```

## Inline Templates

For simple views, use `template` instead of `template_name`:

```python
class HelloView(LiveView):
    template = "<h1>Hello {{ name }}!</h1>"

    def mount(self, request, **kwargs):
        self.name = "World"
```

## URL Configuration

LiveViews use Django's standard `as_view()`:

```python
from django.urls import path
from myapp.views import MyView

urlpatterns = [
    path("items/<int:item_id>/", MyView.as_view(), name="my-view"),
]
```

## Authentication

Use Django's built-in mixins:

```python
from django.contrib.auth.mixins import LoginRequiredMixin
from djust import LiveView

class ProtectedView(LoginRequiredMixin, LiveView):
    template_name = "myapp/protected.html"
    login_url = "/login/"
```

Or use djust's permission decorator on individual handlers:

```python
from djust.decorators import event_handler, permission_required

@event_handler()
@permission_required("myapp.can_delete")
def delete_item(self, item_id: int = 0, **kwargs):
    Item.objects.filter(pk=item_id).delete()
```

## HTTP Fallback Mode

If the WebSocket is unavailable (for example, behind a proxy that blocks it), client.js sends each event as a JSON POST to the view's URL and applies the returned patches. JavaScript is still required. The initial GET is always a complete server-rendered page, which is what crawlers see.

## Next Steps

- [Events](./events.md) — event binding and handler patterns
- [Components](./components.md) — reusable UI components
- [Templates](./templates.md) — template directives reference
- [Loading States & Background Work](../guides/loading-states.md) — spinners, async operations
