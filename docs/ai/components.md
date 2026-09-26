# Components

## Two Types

- **Component** (stateless) — Fast rendering, no events. Use for badges, icons, cards.
- **LiveComponent** (stateful) — Has mount/render lifecycle, event handlers, parent-child comms.

## Stateless Component

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

### Rendering Waterfall
1. `_rust_impl_class` — Pure Rust (~1us)
2. `template` — Rust template engine with Django fallback (~5-10us)
3. `_render_custom()` — Python method (~50-100us)

### Usage in LiveView
```python
class MyView(LiveView):
    def mount(self, request, **kwargs):
        self.badge = Badge("New", variant="success")
    def get_context_data(self, **kwargs):
        return {"badge": self.badge}
```
Template: `{{ badge }}` (calls `__str__` → `render()`)

## LiveComponent

```python
from djust.components.base import LiveComponent

class CounterWidget(LiveComponent):
    template = """
        <div>
            <button dj-click="decrement" data-component-id="{{ component_id }}">-</button>
            <span>{{ count }}</span>
            <button dj-click="increment" data-component-id="{{ component_id }}">+</button>
        </div>
    """

    def mount(self, **kwargs):
        self.count = kwargs.get("initial", 0)

    def increment(self):
        self.count += 1
        self.trigger_update()

    def decrement(self):
        self.count -= 1
        self.trigger_update()

    def get_context_data(self):
        return {"count": self.count}
```

### Critical Rules
- `data-component-id="{{ component_id }}"` on EVERY element with `dj-*` events
- Call `trigger_update()` after state changes
- Use `send_parent(event_name, data_dict)` for parent communication
- `mount()` is required (abstract). Initialize all state here.
- `get_context_data()` is required (abstract). Return all template vars.

### Parent Receives Events
```python
class MyView(LiveView):
    def handle_component_event(self, component_id, event, data):
        if event == "count_changed":
            self.total = data["count"]
```

### File-Based Templates
```python
class UserCard(LiveComponent):
    template_name = "components/user_card.html"  # Django template
```
Note: file-based templates do NOT auto-wrap with `data-component-id` div. Add it yourself.

## Built-in Components

### LiveComponents (stateful)
```python
from djust.components import AlertComponent, ModalComponent, TabsComponent, TableComponent
from djust.components import ButtonComponent, CardComponent, DropdownComponent
from djust.components import ProgressComponent, SpinnerComponent
from djust.components import PaginationComponent
from djust.components.forms import ForeignKeySelect, ManyToManySelect
```

### Stateless Components (fast)
```python
from djust.components.ui import Badge, Button, Card, Alert, Modal
from djust.components.ui import Accordion, Avatar, Breadcrumb, Checkbox, Divider
from djust.components.ui import Dropdown, Icon, Input, ListGroup, NavBar
from djust.components.ui import Offcanvas, Pagination, Progress, Radio, Range
from djust.components.ui import Select, Spinner, Switch, Table, Tabs
from djust.components.ui import TextArea, Toast, Tooltip
```

## Interactive components (djust 1.3+)

**Available from djust 1.3** (not in the 1.3.0rc1 pre-release). Import from
`djust.components.interactive`, never `djust.components.components`: that
`DropdownMenu` is the legacy renderer, and `djust.Q004` warns when a module
imports both.

<!-- djust-example: ai-project-menu scenario=project-menu -->
```python
from djust import LiveView
from djust.components.interactive import DropdownMenu


class ProjectView(LiveView):
    template_name = "projects/detail.html"

    project_menu = DropdownMenu(
        label="Project",
        items=[{"label": "Edit", "value": "edit"}, {"separator": True}],
    )

    def mount(self, request, **kwargs):
        self.selected_action = ""

    @project_menu.on.selected
    def on_project_menu_selected(self, component: DropdownMenu, value: str) -> None:
        self.selected_action = value
```

```html
<div dj-root>{{ project_menu }}<p>{{ selected_action }}</p></div>
```

Rules:
- **Declaring:** declare the menu as a class attribute and subscribe with
  `@<menu>.on.selected` / `@<menu>.on.toggled`. Never add `@event_handler` to
  the callback. Never write `toggle`/`select` handlers: the component owns
  them.
- **Callbacks:** the parameters are `(self, component: DropdownMenu, value: str)`
  for `selected` and `(self, component: DropdownMenu, open: bool)` for
  `toggled`, and a callback returns `None`.
- **Items:** `{"label", "value", "disabled"?}` with nonempty unique values, or
  `{"separator": True}`.
- **Visibility:**
  - `visibility="client"` gives a native popover. Python must not read or set
    `open` then.
  - `.on.toggled` observes it. Without that subscription, nothing is sent to
    the server.
- **Rows:** for a row action on a record, use a plain `@event_handler()` with
  `dj-value-<id>`, not a component per row.
- **Keyed collections:** use `rows = DropdownMenu.collection()` only when rows
  own menu state.
  - Fill it with `self.rows.sync([(str(record.id), DropdownMenu(...)), ...])`,
    and render `{% for menu in rows.values %}{{ menu }}{% endfor %}`.
  - One `@rows.on.selected` callback serves every row; `component.key` is the
    row key.
  - `get(key)` returns the row or `None`.
- **Authorization:** callbacks must authorize the record a value refers to.
- **Actor views:** not supported (`djust.V020`).

## Common Patterns

### Alert with show/hide
```python
self.alert = AlertComponent(message="Saved!", type="success", dismissible=True)
self.alert.show("Error!", "danger")
self.alert.dismiss()
```

### Modal
```python
self.modal = ModalComponent(title="Confirm", body="Are you sure?", show=False)
self.modal.show()
self.modal.hide()
```

### Tabs
```python
self.tabs = TabsComponent(
    tabs=[
        {"id": "tab1", "label": "First", "content": "..."},
        {"id": "tab2", "label": "Second", "content": "...", "badge": "3"},
    ],
    active="tab1",
)
self.tabs.activate_tab("tab2")
```

### Data Table
```python
self.table = TableComponent(
    columns=[{"key": "name", "label": "Name", "sortable": True}],
    rows=[{"name": "Alice"}, {"name": "Bob"}],
    striped=True, hoverable=True,
)
```

### ForeignKey Select
```python
self.author = ForeignKeySelect(
    name="author", queryset=Author.objects.all(),
    label_field="name", searchable=True, search_fields=["name", "email"],
)
```

## Component Registry
```python
from djust.components import register_component, get_component, list_components

register_component("my_widget", MyWidgetComponent)
cls = get_component("my_widget")
```

## Template Pitfall

Avoid `{% elif %}` in inline templates — Rust engine limitation. Use separate `{% if %}` blocks.

## Styling

### Recommended: djust-components + djust-theming (style-agnostic)

`pip install djust-components djust-theming`

**djust-components** — Template tag UI library using CSS custom properties:
```html
{% load djust_components %}
{% modal id="confirm" title="Sure?" open=modal_open %}...{% endmodal %}
{% tabs id="settings" active=active_tab event="set_tab" %}
  {% tab id="general" label="General" %}...{% endtab %}
{% endtabs %}
{% card title="Dashboard" variant="elevated" %}...{% endcard %}
{% badge label="Online" status="online" pulse=True %}
{% data_table rows=rows columns=columns sort_by=sort_by sort_desc=sort_desc %}
```

Customize via CSS custom properties:
```css
:root {
  --dj-primary: #6366f1;
  --dj-success: #22c55e;
  --dj-danger: #ef4444;
  --dj-bg: #0f172a;
  --dj-border: rgba(99, 102, 241, 0.15);
  --dj-radius: 8px;
}
```

**djust-theming** — Design system with 11 themes x 12 color presets = 132 combos:
```python
LIVEVIEW_CONFIG = {
    "theme": {"theme": "material", "preset": "blue", "default_mode": "system"}
}
```
- Template tags: `{% theme_head %}`, `{% theme_switcher %}`, `{% theme_mode_toggle %}`
- LiveView mixin: `ThemeMixin` for reactive theme switching (no page reload)
- shadcn/ui import/export compatible
- Tailwind integration via `manage.py djust_theme tailwind-config`

### Legacy: Core built-in components

Core `djust.components` use `config.get("css_framework")` with per-framework render methods (`_render_bootstrap()`, `_render_tailwind()`, `_render_plain()`). Being migrated to CSS custom properties. Set via:
```python
DJUST = {"css_framework": "bootstrap5"}  # or "tailwind", "plain"
```


### Table presentation

`TableComponent` supports `caption` text, `caption_class`, and a `footer` mapping
keyed by column names. `table_class`, `thead_class`, `tbody_class`, and
`tfoot_class` add classes without replacing framework defaults. For Bootstrap,
use `table_class="align-middle caption-top"` and `thead_class="table-dark"` for
vertical alignment, a top caption, and a dark header. These options are available
in all three renderers; choose classes supplied by the configured stylesheet.
Footer values are caller-supplied summaries and do not participate in sorting,
filtering, or selection. New text and class options are escaped, not raw HTML.
