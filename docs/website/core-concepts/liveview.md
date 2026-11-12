# LiveView

The `LiveView` class is the core building block of djust. It combines a Django class-based view with a persistent WebSocket session that keeps state on the server.

## Basic Structure

```python
from djust import LiveView
from djust.decorators import event_handler


class MyView(LiveView):
    template_name = "myapp/my_view.html"

    def mount(self, request, **kwargs):
        """Initialize state. get_context_data() below hands it to the template."""
        self.count = 0
        self.items = []

    def get_context_data(self, **kwargs):
        return {**super().get_context_data(**kwargs), "count": self.count, "items": self.items}

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

## State Management

State lives on `self`. An attribute (`self.count`) stays on the view for as long as the connection does, so the next event handler finds it. By itself it goes nowhere else ([explicit exposure](../state/explicit-exposure.md)):

- The template gets a value when `get_context_data()` supplies it. A name nothing supplies renders empty, with no error.
- A value survives a reconnect when it is declared with `state(..., persist="server")`. Anything else is set again by `mount()`.
- The browser gets a raw value only for `state(..., client=True)` fields.

```python
def mount(self, request, **kwargs):
    self._db_items = Item.objects.all()  # stays on the server
    self.count = self._db_items.count()

def get_context_data(self, **kwargs):
    return {**super().get_context_data(**kwargs), "count": self.count}
```

A leading underscore is a naming convention for internal values. It does not change what the template sees: no attribute reaches it unless `get_context_data()` hands it over.

## Inline Templates

For simple views, use `template` instead of `template_name`:

```python
class HelloView(LiveView):
    template = "<h1>Hello {{ name }}!</h1>"

    def mount(self, request, **kwargs):
        self.name = "World"

    def get_context_data(self, **kwargs):
        return {**super().get_context_data(**kwargs), "name": self.name}
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
