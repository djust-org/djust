# Event Handlers

All handlers require the `@event_handler()` decorator. `manage.py check` compares each template binding with its handler (`djust.T019`–`T022`).

The rules below are the default legacy policy. For new code, prefer the strict
policy (`@event_handler(parameter_policy="strict")`, or project-wide
`LIVEVIEW_CONFIG = {"event_parameter_policy": "strict"}`; ADR-036):
- Closed, annotated signatures are the contract; `**kwargs` is not required.
- Pass arguments with `dj-value-*` only (never `data-*` or `dj-params`); no type suffix is needed.
- The browser sends a generated value (`value`/`field` for input/change, form fields for submit, `key`/`code` for keyboard) only when the handler declares that parameter name or a `**` catch-all.
- A `dj-value-*` name colliding with a generated name is rejected. `_target` is never sent: use `field` or `dj-value-*`.
- Invalid input is rejected before the handler runs, so don't add defaults just to survive malformed events.

<!-- djust-example: ai-item-view scenario=strict-policy -->
```python
from djust import LiveView
from djust.decorators import event_handler


class ItemView(LiveView):
    template_name = "items/list.html"

    def mount(self, request, **kwargs):
        self.selected_id = 0
        self.query = ""

    @event_handler(parameter_policy="strict")
    def select_item(self, item_id: int, active: bool = False) -> None:
        self.selected_id = item_id  # still authorize the record before using it

    @event_handler(parameter_policy="strict")
    def search(self, value: str) -> None:
        self.query = value
```

```html
<button dj-click="select_item" dj-value-item-id="{{ item.id }}" dj-value-active="true">Select</button>
<input name="q" dj-input="search" value="{{ query }}">
```

Legacy policy:

Legacy `dj-input`, `dj-change` and `dj-submit` also send `field` and `_target`: keep `**kwargs` on those handlers, or declare the names.

<!-- djust-example: skip -- legacy-policy handler fragments with no view class; import-checked by scripts/check-doc-snippets.py -->
```python
from djust.decorators import event_handler, debounce, throttle

# Input/change: parameter MUST be named `value`
@event_handler()
def search(self, value: str = "", **kwargs):
    self.query = value
    self._refresh()

# Button with data attributes: data-item-id="5" -> item_id=5
@event_handler()
def delete(self, item_id: int = 0, **kwargs):
    Item.objects.filter(id=item_id).delete()
    self._refresh()

# Form submission: fields arrive as kwargs
@event_handler()
def save(self, **form_data):
    name = form_data.get("name")

# Debounce — the client waits 0.5s after the last keystroke, then sends once
@event_handler()
@debounce(wait=0.5)
def search(self, value: str = "", **kwargs):
    self.query = value
    self._refresh()

# Throttle — the client sends at most once per second
@event_handler()
@throttle(interval=1.0)
def on_scroll(self, position: int = 0, **kwargs):
    self.scroll_pos = position
```

Template bindings:
```html
<button dj-click="delete" data-item-id="{{ item.id }}">Delete</button>
<input dj-input="search" value="{{ query }}" />
<select dj-change="filter"><option value="all">All</option></select>
<form dj-submit="save">{% csrf_token %}<button type="submit">Save</button></form>
```

Rules:
- `value` is the magic parameter name for `dj-input`/`dj-change` events
- Always provide default values for all parameters
- `data-*` attributes are converted: `data-item-id` -> `item_id`
- Type hints enable automatic coercion: `item_id: int` converts `"5"` to `5`
