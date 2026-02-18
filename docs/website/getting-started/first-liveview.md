# Your First LiveView

Build a live counter — no page refreshes, no JavaScript to write.

## What You'll Build

A counter with increment/decrement buttons that updates instantly via WebSocket. The entire feature is Python.

## 1. Create the View

Create `myapp/views.py`:

```python
from djust import LiveView
from djust.decorators import event_handler


class CounterView(LiveView):
    template_name = "myapp/counter.html"

    def mount(self, request, **kwargs):
        """Called once when the page first loads. Initialize state here."""
        self.count = 0

    def get_context_data(self, **kwargs):
        """Return template context. Called before every render."""
        return {"count": self.count}

    @event_handler()
    def increment(self, **kwargs):
        self.count += 1

    @event_handler()
    def decrement(self, **kwargs):
        self.count -= 1
```

**Key rules:**
- `mount()` runs once — set initial state here, not in `__init__`
- Every event handler needs `@event_handler()` — djust blocks undecorated methods for security
- Always accept `**kwargs` in event handlers (djust may pass extra metadata)
- State lives on `self` — any change to `self.count` triggers a re-render automatically

## 2. Create the Template

Create `myapp/templates/myapp/counter.html`:

```html
<!DOCTYPE html>
<html>
<head>
    <title>Counter</title>
    {% load djust_tags %}
    {% djust_scripts %}
</head>
<body dj-view="{{ dj_view_id }}">
    <div dj-root>
        <h1>Count: {{ count }}</h1>

        <button dj-click="decrement">-</button>
        <button dj-click="increment">+</button>
    </div>
</body>
</html>
```

**Template requirements:**
- `{% load djust_tags %}` and `{% djust_scripts %}` load the client JS (~5KB)
- `dj-view="{{ dj_view_id }}"` on `<body>` connects the page to the WebSocket session
- `dj-root` marks the reactive region — only this subtree is patched on updates
- `dj-click="increment"` binds a click event to the `increment` handler

## 3. Add a URL

In `myapp/urls.py`:

```python
from django.urls import path
from myapp.views import CounterView

urlpatterns = [
    path("counter/", CounterView.as_view(), name="counter"),
]
```

## 4. Run It

```bash
uvicorn myproject.asgi:application --reload
```

Visit **http://localhost:8000/counter/** and click the buttons — the count updates instantly without a page reload.

## How It Works

1. The first request is a normal HTTP response (good for SEO and initial load)
2. The page JS opens a WebSocket connection to `/ws/live/`
3. When you click a button, the client sends `{"event": "increment"}` over the WebSocket
4. djust calls your `increment()` method, re-renders the template in Rust, diffs the VDOM, and sends only the changed HTML fragments back
5. The client patches the DOM — no full page reload

## Responding to Input

For text inputs, use `dj-input` (fires on every keystroke) or `dj-change` (fires on blur):

```python
@event_handler()
def search(self, value: str = "", **kwargs):
    """The 'value' parameter receives the current input value."""
    self.query = value
```

```html
<input type="text" dj-input="search" value="{{ query }}" placeholder="Search..." />
<p>You searched for: {{ query }}</p>
```

## Passing Data from the DOM

Use `data-*` attributes to pass data to event handlers:

```python
@event_handler()
def delete_item(self, item_id: int = 0, **kwargs):
    """data-item-id="5" becomes item_id=5 (auto-converted to int)."""
    self.items = [i for i in self.items if i["id"] != item_id]
```

```html
{% for item in items %}
<li>
    {{ item.name }}
    <button dj-click="delete_item" data-item-id="{{ item.id }}">Delete</button>
</li>
{% endfor %}
```

## Next Steps

- [Core Concepts](./core-concepts.md) — understand the lifecycle and state model
- [Forms](../forms/index.md) — real-time form validation
- [State Management](../state/index.md) — debouncing, loading states, optimistic updates
