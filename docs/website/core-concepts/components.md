# Components

djust has two types of components: **stateless** (fast rendering, no events) and **stateful** (LiveComponent, full lifecycle).

## Stateless Components

Use for pure display elements — badges, icons, cards, alerts that don't need to handle events themselves.

```python
from djust.components.base import Component

class StatusDot(Component):
    template = '<span class="dot dot-{{ color }}"></span>'

    def __init__(self, color: str = "green"):
        super().__init__(color=color)
        self.color = color

    def get_context_data(self):
        return {"color": self.color}
```

Use in a LiveView:

```python
class MyView(LiveView):
    def mount(self, request, **kwargs):
        self.status_dot = StatusDot(color="green")

    def get_context_data(self, **kwargs):
        return {"status_dot": self.status_dot}
```

In the template: `{{ status_dot }}` — calls `__str__()` → `render()`.

### Rendering Priority

Stateless components try rendering methods in order (fastest first):

1. `_rust_impl_class` — Pure Rust implementation (~1μs)
2. `template` string — Rust template engine (~5-10μs)
3. `_render_custom()` — Python method (~50-100μs)

## LiveComponent (Stateful)

LiveComponents have their own mount/render lifecycle and can handle events independently. Use for self-contained widgets that manage their own state.

```python
from djust.components.base import LiveComponent

class CounterWidget(LiveComponent):
    template = """
        <div data-component-id="{{ component_id }}">
            <button dj-click="decrement" data-component-id="{{ component_id }}">-</button>
            <span>{{ count }}</span>
            <button dj-click="increment" data-component-id="{{ component_id }}">+</button>
        </div>
    """

    def mount(self, **kwargs):
        self.count = kwargs.get("initial", 0)

    def get_context_data(self):
        return {"count": self.count}

    def increment(self):
        self.count += 1
        self.trigger_update()

    def decrement(self):
        self.count -= 1
        self.trigger_update()
```

**Critical rules:**

- `data-component-id="{{ component_id }}"` on **every** element with `dj-*` events — without it, events route to the parent LiveView instead
- Call `self.trigger_update()` after changing state
- `mount()` and `get_context_data()` are required

### Parent–Child Communication

Send events from a LiveComponent to its parent LiveView:

```python
class CounterWidget(LiveComponent):
    def increment(self):
        self.count += 1
        self.trigger_update()
        self.send_parent("count_changed", {"count": self.count})
```

The parent receives it via `handle_component_event()`:

```python
class MyView(LiveView):
    def handle_component_event(self, component_id, event, data):
        if event == "count_changed":
            self.total = data["count"]
```

## Built-in Components

### Stateful (LiveComponent)

```python
from djust.components import (
    AlertComponent,
    ModalComponent,
    TabsComponent,
    TableComponent,
    ButtonComponent,
    CardComponent,
    DropdownComponent,
    ProgressComponent,
    SpinnerComponent,
    PaginationComponent,
)
from djust.components.forms import ForeignKeySelect, ManyToManySelect
```

#### Alert

```python
self.alert = AlertComponent(message="Saved!", type="success", dismissible=True)
# Later:
self.alert.show("Error occurred!", "danger")
self.alert.dismiss()
```

#### Modal

```python
self.modal = ModalComponent(title="Confirm Delete", body="Are you sure?", show=False)
# Open/close:
self.modal.show()
self.modal.hide()
```

#### Tabs

```python
self.tabs = TabsComponent(
    tabs=[
        {"id": "overview", "label": "Overview", "content": "..."},
        {"id": "settings", "label": "Settings", "content": "...", "badge": "3"},
    ],
    active="overview",
)
self.tabs.activate_tab("settings")
```

#### Data Table

```python
self.table = TableComponent(
    columns=[
        {"key": "name", "label": "Name", "sortable": True},
        {"key": "email", "label": "Email"},
    ],
    rows=[{"name": "Alice", "email": "alice@example.com"}],
    striped=True,
    hoverable=True,
)
```

#### ForeignKey Select (with search)

```python
self.author = ForeignKeySelect(
    name="author",
    queryset=Author.objects.all(),
    label_field="name",
    searchable=True,
    search_fields=["name", "email"],
)
```

### Stateless (UI)

```python
from djust.components.ui import (
    Badge, Button, Card, Alert, Modal,
    Accordion, Avatar, Breadcrumb, Checkbox, Divider,
    Dropdown, Icon, Input, ListGroup, NavBar,
    Pagination, Progress, Radio, Range,
    Select, Spinner, Switch, Table, Tabs,
    TextArea, Toast, Tooltip,
)
```

## Component Registry

```python
from djust.components import register_component, get_component, list_components

# Register a custom component
register_component("my_widget", MyWidgetComponent)

# Look up by name
cls = get_component("my_widget")

# List all registered components
all_components = list_components()
```

## Styling

djust components use CSS custom properties for styling. Customize via:

```css
:root {
    --dj-primary: #6366f1;
    --dj-success: #22c55e;
    --dj-danger: #ef4444;
    --dj-radius: 8px;
}
```

Or install the separate packages for a full design system:

```bash
pip install djust-components djust-theming
```

## Known Limitations

- Avoid `{% elif %}` in inline component templates — use separate `{% if %}` blocks instead (Rust template engine limitation)
- File-based templates (`template_name`) do **not** auto-wrap with `data-component-id`. Add the attribute manually on the root element.

## Next Steps

- [Events](./events.md) — event binding in templates
- [API Reference: Components](../api-reference/components.md) — full component API
