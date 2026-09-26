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

In the template: `{{ status_dot }}` — calls `__str__()` → `render()`, which
marks the markup safe, so no `|safe` is needed on any render path (an earlier
version of this note said otherwise while
[#2501](https://github.com/djust-org/djust/issues/2501) was open; it is
closed).

A component's constructor kwargs are its **state**, and writing an attribute
writes that state
([ADR-033](https://github.com/djust-org/djust/blob/main/docs/adr/033-plain-component-state-and-identity.md)).
So a handler changes the component the way it changes any attribute, and the
re-render carries it:

```python
class MyView(LiveView):
    def mount(self, request, **kwargs):
        self.rating = Rating(value=4, max_stars=5)

    @event_handler()
    def set_rating(self, value, **kwargs):
        self.rating.value = value
```

`value` arrives as an `int`: every shipped component emits its values typed
(`dj-value-value:int="4"`), so a handler never starts with `int(value)`.

Change detection compares a plain component by that state (the #2900 rule
that already covered class-level components), budgeted like any container.
A component holding data can narrow the walk with
`fingerprint_fields = ("columns",)`: listed keys are walked, every other key
is a one-node leaf — a scalar compares by value, a container by identity, so
`self.table.rows = fetch()` is seen and `self.table.rows.append(x)` is not.
The data components that ship with djust declare theirs.

**One handler, several instances.** Give each instance a `name`; every event
it emits carries it as `dj-value-name`, so the handler knows which one spoke —
the same word an HTML form uses:

```python
class ReviewView(LiveView):
    def mount(self, request, **kwargs):
        self.ratings = [Rating(name=f"row-{row.pk}", value=row.stars) for row in rows]

    @event_handler()
    def set_rating(self, value, name=None, **kwargs):
        for rating in self.ratings:
            if rating.name == name:
                rating.value = value
```

`event=` still renames the event (`Rating(event="rate_delivery")`); it is
the verb, `name` is the noun.

**Which shape when.** A fixed widget with behaviour of its own — tabs, an
accordion, a modal — is a
[class-level `LiveComponent`](../guides/components.md#class-level-components):
per-view state, its own handlers, routed by attribute name, like fields on a
`Form`. Anything data-driven or repeated — a rating per row, a chart, a
table — is a plain component held by the view: state in its kwargs, written
to by the view's handlers, identified by `name`, like a formset. Rebuilding a
component in a handler still works; it is no longer the taught shape.

Do not make a plain `Component` a class attribute: that is one shared object
across every user of the view. Class-level declaration is for
[`LiveComponent` descriptors](../guides/components.md#class-level-components),
which give each view its own state.

### Rendering Priority

Stateless components try rendering methods in order (fastest first):

1. `_rust_impl_class` — Pure Rust implementation (~1μs)
2. `template` string — Rust template engine (~5-10μs)
3. `_render_custom()` — Python method (~50-100μs)

## LiveComponent (Stateful)

LiveComponents have their own mount/render lifecycle and can handle events independently. Use for self-contained widgets that manage their own state.

```python
from djust.components.base import LiveComponent
from djust.decorators import event_handler

class CounterWidget(LiveComponent):
    template = """
        <div>
            <button dj-click="decrement">-</button>
            <span>{{ count }}</span>
            <button dj-click="increment">+</button>
        </div>
    """

    def mount(self, **kwargs):
        self.count = kwargs.get("initial", 0)

    def get_context_data(self):
        return {"count": self.count}

    @event_handler()
    def increment(self, **kwargs):
        self.count += 1
        self.trigger_update()

    @event_handler()
    def decrement(self, **kwargs):
        self.count -= 1
        self.trigger_update()
```

**Critical rules:**

- Events inside a component route to it automatically: the client uses the nearest ancestor with `data-component-id`. Inline `template` components are wrapped in `<div data-component-id="…">` for you. For `template_name` components, put `data-component-id="{{ component_id }}"` once on the root element.
- Call `self.trigger_update()` after changing state
- `mount()` and `get_context_data()` have no-op defaults; override `get_context_data()` to expose state to the template

### Parent–Child Communication

Send events from a LiveComponent to its parent LiveView:

```python
class CounterWidget(LiveComponent):
    @event_handler()
    def increment(self, **kwargs):
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

## Choose the owner

Pick the tool by who owns the behavior:

- **Browser presentation only** (show or hide, nothing for Python to read):
  native HTML such as `<details>` or `popover`, and no server component.
- **The page** (a row's action on a record): an ordinary `@event_handler()`
  that receives the record id.
- **A reusable widget that owns state and mechanics:** an interactive
  component, such as `djust.components.interactive.DropdownMenu`. It opens,
  closes and validates by itself, and reports typed outputs that your view
  subscribes to with `@menu.on.selected`. Available from djust 1.3 (not in the
  1.3.0rc1 pre-release).

See [Interactive Components](../guides/interactive-components.md).

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

djust components use the theme's CSS custom properties for styling. Color tokens are HSL triplets (the CSS wraps them as `hsl(var(--primary))`). Customize via:

```css
:root {
    --primary: 239 84% 67%;
    --success: 142 71% 45%;
    --destructive: 0 84% 60%;
    --radius: 0.5rem;
}
```

For the full component library and design system, install the extras:

```bash
pip install "djust[components,theming]"
```

The components live in `djust.components` and theming in `djust.theming`. The standalone `djust-components` and `djust-theming` packages are frozen compatibility shims; see [Migrating from the standalone packages](../guides/migration-from-standalone-packages.md).

## Known Limitations

- File-based templates (`template_name`) do **not** auto-wrap with `data-component-id`. Add the attribute manually on the root element.

## Next Steps

- [Events](./events.md) — event binding in templates
- [API Reference: Components](../api-reference/components.md) — full component API
