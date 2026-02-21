# LiveView

The `LiveView` class is the core building block of djust. It combines a Django class-based view with a persistent WebSocket session that keeps state on the server.

## Basic Structure

```python
from djust import LiveView
from djust.decorators import event_handler


class MyView(LiveView):
    template_name = "myapp/my_view.html"

    def mount(self, request, **kwargs):
        """Initialize state. Called once on page load."""
        self.count = 0
        self.items = []

    def get_context_data(self, **kwargs):
        """Return template context. Called before every render."""
        return {"count": self.count, "items": self.items}

    @event_handler()
    def increment(self, **kwargs):
        """Handle a dj-click="increment" event."""
        self.count += 1
```

## Lifecycle Hooks

### `mount(request, **kwargs)`

Called **once** when the LiveView is first loaded (HTTP request). Use this to:

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

### `handle_params(params, url, **kwargs)`

Called when URL parameters change via `live_patch()` navigation (without a full page reload):

```python
def handle_params(self, params, url, **kwargs):
    self.page = int(params.get("page", 1))
    self._refresh()
```

### `handle_info(event, data, **kwargs)`

Called when the server sends a message to this LiveView (e.g., from background tasks or PubSub):

```python
def handle_info(self, event, data, **kwargs):
    if event == "new_message":
        self.messages.append(data["message"])
```

## State Management

State lives on `self`. Any public attribute (`self.count`) is:

- Included in the template context
- Preserved across WebSocket events
- Re-rendered when changed

Private attributes (prefixed with `_`) are excluded from serialization:

```python
def mount(self, request, **kwargs):
    self._db_items = Item.objects.all()  # private — not serialized
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

LiveViews work without WebSockets — they degrade gracefully to standard HTTP form submissions. This enables server-side rendering for environments that don't support WebSockets (some proxies, crawlers).

## Next Steps

- [Events](./events.md) — event binding and handler patterns
- [Components](./components.md) — reusable UI components
- [Templates](./templates.md) — template directives reference
- [Loading States & Background Work](../guides/loading-states.md) — spinners, async operations
