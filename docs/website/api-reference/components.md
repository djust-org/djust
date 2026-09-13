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

In the template: `{{ status_badge }}` (calls `render()` via `__str__()`). Add `|safe` (`{{ status_badge|safe }}`) until [#2501](https://github.com/djust-org/djust/issues/2501)'s escaping fix lands — without it the markup renders as literal text.

---

## `class LiveComponent`

Stateful component with its own mount/render lifecycle and event handlers.

```python
from djust.components.base import LiveComponent
```

### Class Attributes

| Attribute       | Type  | Description             |
| --------------- | ----- | ----------------------- |
| `template`      | `str` | Inline HTML template    |
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
    @event_handler()
    def increment(self, **kwargs):
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

A `sortable` column header carries `aria-sort` (`none` / `ascending` /
`descending`) and a visual mark in the framework's icon convention —
Bootstrap Icons classes on `bootstrap5` (`bi-arrow-down-up`, `bi-caret-up-fill`,
`bi-caret-down-fill`; load the Bootstrap Icons stylesheet), the vendored
heroicons SVG on `tailwind`, and `⇅` / `▲` / `▼` on `plain`.

`selectable=True` adds a checkbox per row and one in the header. A row is
identified by its `row_key` value (default `"id"`), stored as a string in
`selected_rows` — the same convention as `{% data_table %}`:

```python
self.table = TableComponent(columns=..., rows=..., selectable=True, row_key="id")
# after the user ticks two rows:
self.table.selected_rows  # ["3", "7"]
```

The row checkbox toggles that row (`toggle_row`); the header checkbox selects
every **visible** row, or clears the selection when every visible row is
already selected (`toggle_all`).

`filterable=True` adds a global filter input above the table: rows stay visible
when **any** column's string value contains the query, case-insensitively.
`{"filterable": True}` on a column adds a filter input under that header that
narrows on that column alone. Both compose (every filter must match), and they
compose with the sort: rows are filtered, then sorted. The rows you passed are
never narrowed — clearing an input restores them. The conventions mirror
`{% data_table %}` / `DataTableMixin` (`icontains`, an empty value removes the
filter, select-all is the post-filter set):

```python
self.table = TableComponent(
    columns=[
        {"key": "name", "label": "Name", "sortable": True, "filterable": True},
        {"key": "email", "label": "Email"},
    ],
    rows=...,
    filterable=True,
)
# after the user types:
self.table.filter_query     # "ali"      — the global input (filter_rows)
self.table.column_filters   # {"name": "al"}  — per column (filter_column)
```

The inputs are `dj-input` controls debounced at 300 ms (`dj-debounce="300"`),
routed to the component with `data-component-id`, and labelled
(`aria-label="Search table"` / `aria-label="Filter <label>"`).

#### Table anatomy and CSS classes

Use additive class hooks to customize the table without replacing its renderer.
For Bootstrap, `table_class="align-middle caption-top"` enables vertical alignment
and a top caption; `thead_class="table-dark"` styles the header. Supply utility
classes appropriate to your stylesheet when using Tailwind or plain rendering.

```python
self.table = TableComponent(
    columns=[{"key": "name", "label": "Name"}, {"key": "count", "label": "Count"}],
    rows=[{"name": "Alice", "count": 3}],
    table_class="align-middle caption-top",
    thead_class="table-dark",
    caption="Items by owner",
    caption_class="text-muted",
    footer={"name": "Total", "count": 3},
    tfoot_class="table-light",
)
```

| Option | Purpose | Default |
| --- | --- | --- |
| `table_class` | Classes appended to the framework's table classes | `""` |
| `thead_class`, `tbody_class`, `tfoot_class` | Classes on the respective table sections | `""` |
| `caption` | Text in a semantic `<caption>` before the header | `None` (omitted) |
| `caption_class` | Classes on the caption | `""` |
| `footer` | One summary row, a mapping keyed like the data rows | `None` (omitted) |

Caption and footer text and all class strings are HTML-escaped. Footer cells follow
the declared column order; missing keys render empty cells, and selection adds an
empty alignment cell. The footer is a supplied summary, not an automatically
calculated total, and is not sorted, filtered, or selected with the data rows.


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

## Markdown editor

See the [Markdown Editor guide](../guides/markdown-editor.md) for optional Visual/Markdown editing,
native form integration, asset loading, theme variables and editing limitations.
