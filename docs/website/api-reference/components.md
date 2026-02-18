# Components API Reference

## `class Component`

Base class for stateless components — no events, no lifecycle, just rendering.

```python
from djust.components.base import Component
```

### Methods

#### `__init__(**kwargs)`

Pass initial values as keyword arguments. Call `super().__init__(**kwargs)`.

#### `get_context_data() -> dict`

Return template context. Override to provide data to the template.

#### `render() -> str`

Renders the component to an HTML string. Called automatically by `__str__()`.

**Usage:**

```python
class Badge(Component):
    template = '<span class="badge badge-{{ variant }}">{{ label }}</span>'

    def __init__(self, label: str, variant: str = "primary"):
        super().__init__(label=label, variant=variant)
        self.label = label
        self.variant = variant

    def get_context_data(self):
        return {"label": self.label, "variant": self.variant}
```

In a LiveView:

```python
def mount(self, request, **kwargs):
    self.status_badge = Badge("Active", variant="success")

def get_context_data(self, **kwargs):
    return {"status_badge": self.status_badge}
```

In the template: `{{ status_badge }}` (calls `render()` via `__str__()`).

---

## `class LiveComponent`

Stateful component with its own mount/render lifecycle and event handlers.

```python
from djust.components.base import LiveComponent
```

### Class Attributes

| Attribute | Type | Description |
|-----------|------|-------------|
| `template` | `str` | Inline HTML template |
| `template_name` | `str` | Path to a template file |

### Abstract Methods (must implement)

#### `mount(**kwargs)`

Called once when the component is first rendered. Initialize all state here.

```python
def mount(self, **kwargs):
    self.count = kwargs.get("initial", 0)
```

#### `get_context_data() -> dict`

Return template context. Called before every render.

```python
def get_context_data(self):
    return {"count": self.count}
```

### Instance Methods

#### `trigger_update()`

Re-render this component and push the diff to the client. Call after changing state in an event handler.

#### `send_parent(event_name, data)`

Send an event to the parent LiveView.

**Parameters:**
- `event_name` (`str`) — Event name string
- `data` (`dict`) — Event payload

The parent receives it via `handle_component_event()`.

### Critical Template Rule

Every element in a LiveComponent template that has a `dj-*` event attribute **must** include `data-component-id="{{ component_id }}"`. Without it, events route to the parent LiveView:

```html
<div data-component-id="{{ component_id }}">
    <button dj-click="increment" data-component-id="{{ component_id }}">+</button>
    <span>{{ count }}</span>
</div>
```

### Parent–Child Communication

```python
# Component sends event up:
class CounterWidget(LiveComponent):
    def increment(self):
        self.count += 1
        self.trigger_update()
        self.send_parent("count_changed", {"count": self.count})

# Parent receives it:
class DashboardView(LiveView):
    def handle_component_event(self, component_id, event, data):
        if event == "count_changed":
            self.total = data["count"]
```

---

## Built-in Components

### `AlertComponent`

```python
from djust.components import AlertComponent

self.alert = AlertComponent(
    message="Saved!",
    type="success",       # "success", "danger", "warning", "info"
    dismissible=True,
)

# Methods:
self.alert.show("New message!", "danger")
self.alert.dismiss()
```

### `ModalComponent`

```python
from djust.components import ModalComponent

self.modal = ModalComponent(
    title="Confirm Delete",
    body="This cannot be undone.",
    show=False,
)

# Methods:
self.modal.show()
self.modal.hide()
```

### `TabsComponent`

```python
from djust.components import TabsComponent

self.tabs = TabsComponent(
    tabs=[
        {"id": "tab1", "label": "Overview", "content": "<p>...</p>"},
        {"id": "tab2", "label": "Settings", "content": "<p>...</p>", "badge": "3"},
    ],
    active="tab1",
)

# Methods:
self.tabs.activate_tab("tab2")
```

### `TableComponent`

```python
from djust.components import TableComponent

self.table = TableComponent(
    columns=[
        {"key": "name", "label": "Name", "sortable": True},
        {"key": "email", "label": "Email"},
    ],
    rows=[
        {"name": "Alice", "email": "alice@example.com"},
    ],
    striped=True,
    hoverable=True,
)
```

### `PaginationComponent`

```python
from djust.components import PaginationComponent

self.pagination = PaginationComponent(
    current_page=1,
    total_pages=10,
    on_page_change="go_to_page",   # event handler name
)
```

### `ProgressComponent`

```python
from djust.components import ProgressComponent

self.progress = ProgressComponent(value=65, max=100, label="65%")
```

### `ForeignKeySelect` / `ManyToManySelect`

```python
from djust.components.forms import ForeignKeySelect, ManyToManySelect

self.author_select = ForeignKeySelect(
    name="author",
    queryset=Author.objects.all(),
    label_field="name",
    searchable=True,
    search_fields=["name", "email"],
)
```

---

## Component Registry

```python
from djust.components import register_component, get_component, list_components

# Register a custom component globally
register_component("my_widget", MyWidgetComponent)

# Retrieve by name
cls = get_component("my_widget")

# List all registered components
names = list_components()  # ['alert', 'modal', 'my_widget', ...]
```

---

## Stateless UI Components

Quick-use display components. No event handling.

```python
from djust.components.ui import Badge, Button, Card, Alert

badge = Badge(label="New", variant="success")
button = Button(label="Click me", variant="primary", disabled=False)
card = Card(title="My Card", body="Content here")
```

Full list:
`Badge`, `Button`, `Card`, `Alert`, `Modal`, `Accordion`, `Avatar`, `Breadcrumb`,
`Checkbox`, `Divider`, `Dropdown`, `Icon`, `Input`, `ListGroup`, `NavBar`,
`Pagination`, `Progress`, `Radio`, `Range`, `Select`, `Spinner`, `Switch`,
`Table`, `Tabs`, `TextArea`, `Toast`, `Tooltip`

---

## See Also

- [Components guide](../core-concepts/components.md)
- [LiveView API](./liveview.md)
