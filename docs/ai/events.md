# Event Handlers

All handlers require `@event_handler()` decorator and `**kwargs`.

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

# Debounce (wait after typing stops)
@event_handler()
@debounce(wait=0.5)
def search(self, value: str = "", **kwargs):
    self.query = value
    self._refresh()

# Throttle (limit call rate)
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
