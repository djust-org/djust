# Events

djust uses `dj-*` HTML attributes to bind server-side Python handlers to client-side DOM events.

## Event Bindings

| Attribute                     | Fires when                  | Handler receives           |
| ----------------------------- | --------------------------- | -------------------------- |
| `dj-click="handler"`          | Element is clicked          | `data-*` attrs as kwargs   |
| `dj-input="handler"`          | Input value changes (keyup) | `value=` string            |
| `dj-change="handler"`         | Input/select loses focus    | `value=` string            |
| `dj-submit="handler"`         | Form is submitted           | All named fields as kwargs |
| `dj-keydown.enter="handler"`  | Enter key pressed           | `**kwargs`                 |
| `dj-keydown.escape="handler"` | Escape key pressed          | `**kwargs`                 |

## Defining Handlers

All event handlers **must** use the `@event_handler()` decorator. djust blocks any method not decorated with it (security):

```python
from djust.decorators import event_handler

class MyView(LiveView):
    @event_handler()
    def increment(self, **kwargs):
        self.count += 1

    @event_handler()
    def search(self, value: str = "", **kwargs):
        """dj-input/dj-change: current input value arrives as 'value'."""
        self.query = value

    @event_handler()
    def delete(self, item_id: int = 0, **kwargs):
        """data-item-id="{{ item.id }}" becomes item_id=5 (auto-coerced)."""
        self.items = [i for i in self.items if i["id"] != item_id]

    @event_handler()
    def save(self, **form_data):
        """dj-submit: all named form fields arrive as kwargs."""
        name = form_data.get("name", "")
        email = form_data.get("email", "")
```

**Rules:**

- Always accept `**kwargs` — djust may pass extra metadata
- Provide default values for all parameters (`value: str = ""`)
- Use type hints for automatic coercion (`item_id: int` converts `"5"` → `5`)
- `value` is the magic parameter name for `dj-input` and `dj-change`

## Template Bindings

### Click

```html
<!-- Simple click -->
<button dj-click="increment">+</button>

<!-- Pass data via data-* attributes -->
<button dj-click="delete" data-item-id="{{ item.id }}">Delete</button>
```

`data-*` attributes are converted: `data-item-id` → `item_id`.

### Input

```html
<!-- Fires on every keystroke -->
<input type="text" dj-input="search" value="{{ query }}" placeholder="Search..." />
```

### Change (Select / Blur)

```html
<select dj-change="filter_status">
    <option value="all" {% if status == "all" %}selected{% endif %}>All</option>
    <option value="active" {% if status == "active" %}selected{% endif %}>Active</option>
</select>
```

### Form Submit

```html
<form dj-submit="save_form">
    {% csrf_token %}
    <input name="title" type="text" value="{{ title }}" />
    <input name="email" type="email" value="{{ email }}" />
    <button type="submit">Save</button>
</form>
```

`{% csrf_token %}` is required in all `dj-submit` forms.

### Keyboard Events

```html
<input dj-keydown.enter="submit_search" dj-keydown.escape="clear_search" />
```

## Rate Limiting

Use decorators to control how often handlers fire:

```python
from djust.decorators import event_handler, debounce, throttle

# Wait 500ms after the last keystroke before firing
@event_handler()
@debounce(wait=0.5)
def search(self, value: str = "", **kwargs):
    self.query = value
    self._refresh()

# Fire at most once per second
@event_handler()
@throttle(interval=1.0)
def on_scroll(self, position: int = 0, **kwargs):
    self.scroll_pos = position
```

## Loading States

Show feedback while a handler is running:

```python
from djust.decorators import event_handler, loading

@event_handler()
@loading("is_saving")
def save(self, **form_data):
    """self.is_saving=True while this runs, False after."""
    expensive_operation()
```

```html
<button dj-click="save" {% if is_saving %}disabled{% endif %}>
    {% if is_saving %}Saving...{% else %}Save{% endif %}
</button>
```

## DOM Keying for Lists

When rendering lists that can reorder, add `data-key` for optimal VDOM diffing:

```html
{% for item in items %}
<div data-key="{{ item.id }}">
    {{ item.name }}
    <button dj-click="delete" data-item-id="{{ item.id }}">Delete</button>
</div>
{% endfor %}
```

Without `data-key`, djust diffs by position and may produce unnecessary DOM mutations.

## Ignoring DOM Subtrees

If a subtree is managed by external JavaScript (charts, editors), prevent djust from patching it:

```html
<div dj-update="ignore" id="my-chart"></div>
```

## Next Steps

- [Templates](./templates.md) — full template directives reference
- [State Management](../state/index.md) — debounce, throttle, loading states, optimistic updates
- [Hooks](../guides/hooks.md) — client-side JavaScript lifecycle hooks
