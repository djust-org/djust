---
title: "Interactive Components"
slug: interactive-components
section: guides
order: 4.5
level: intermediate
description: "Components that own their behavior and report typed outputs: DropdownMenu, keyed collections, and when a plain view handler is the better choice"
---

# Interactive Components

**Available from djust 1.3** (not in the 1.3.0rc1 pre-release).

An interactive component owns its own mechanics. A dropdown opens, closes and
checks that a selection is allowed, all by itself. It then tells your view
what happened through a typed *output*, which the view subscribes to with a
decorator:

```python
from djust import LiveView
from djust.components.interactive import DropdownMenu


class ProjectView(LiveView):
    template_name = "projects/detail.html"

    project_menu = DropdownMenu(
        label="Project",
        items=[
            {"label": "Edit", "value": "edit"},
            {"label": "Archive", "value": "archive"},
            {"separator": True},
            {"label": "Delete", "value": "delete", "disabled": True},
        ],
    )

    def mount(self, request, **kwargs):
        self.selected_action = ""

    @project_menu.on.selected
    def on_project_menu_selected(self, component: DropdownMenu, value: str) -> None:
        self.selected_action = value
```

```html
<div dj-root>
    {{ project_menu }}
    <p>Selected: {{ selected_action }}</p>
</div>
```

That is the whole integration. The menu opens and closes without any view
code. The callback runs only after a valid choice, once, with the actual menu
as `component`. Choosing the disabled "Delete", or a value that isn't in
`items`, never reaches the callback.

`project_menu` and `selected` are real Python names. mypy and Pyright report a
misspelled menu, an unknown output such as `project_menu.on.seleted`, and a
callback with the wrong signature, such as `value: int`. The callback's name
is yours; renaming it never disconnects it.

## Choose the owner first

Not everything that repeats or opens needs a component. Pick by who owns the
behavior:

| What owns the behavior? | Use |
| --- | --- |
| The browser only: showing or hiding with nothing for Python to read | Native HTML such as `<details>` or a `popover`; no server component. |
| The page: a row's action on a record | An ordinary `@event_handler()` that receives the record id ([Repeated rows](#repeated-rows-pass-the-record-id)). |
| A reusable, stateful widget | An interactive component with typed outputs (this page). |

## `DropdownMenu`

`from djust.components.interactive import DropdownMenu`. This is not the
legacy plain renderer of the same name in `djust.components.components`,
which keeps working unchanged. System check `djust.Q004` warns when a module
imports both.

- **Configuration:**
  - `label`: the trigger's text.
  - `items`: a list of `ActionItem`s (`{"label", "value", "disabled"?}`, with
    nonempty unique `value`s) and `SeparatorItem`s (`{"separator": True}`).
  - `visibility`: `"server"` (the default) or `"client"`.
- **State:**
  - `open`: server visibility only; Python can read and set it.
  - `selected`: the last chosen value.
  - `key`: the declared name, read-only.
- **Outputs:**
  - `@menu.on.selected(self, component, value: str)`: after a valid choice.
  - `@menu.on.toggled(self, component, open: bool)`: after it opens or closes.
- **Local actions:** `toggle`, `close` and `select` handle clicks inside the
  component. You don't write handlers for them.

The generated reference in the [Components API](../api-reference/components.md)
lists every argument, state property, output and action, generated from the
component's own code.

### Two menus, separate callbacks

Each declaration is independent, and so is every view instance: opening one
menu never changes another menu, or another user's page.

```python
from djust import LiveView
from djust.components.interactive import DropdownMenu


class ToolbarView(LiveView):
    template_name = "projects/toolbar.html"

    project_menu = DropdownMenu(
        label="Project",
        items=[{"label": "Edit", "value": "edit"}, {"label": "Archive", "value": "archive"}],
    )
    account_menu = DropdownMenu(
        label="Account",
        items=[{"label": "Settings", "value": "settings"}],
    )

    def mount(self, request, **kwargs):
        self.selected_action = ""
        self.show_account_settings = False

    @project_menu.on.selected
    def on_project_menu_selected(self, component: DropdownMenu, value: str) -> None:
        self.selected_action = value

    @account_menu.on.selected
    def on_account_menu_selected(self, component: DropdownMenu, value: str) -> None:
        self.show_account_settings = value == "settings"
```

```html
<div dj-root>
    {{ project_menu }}
    {{ account_menu }}
    <p>Selected project action: {{ selected_action }}</p>
    {% if show_account_settings %}<p>Account settings selected.</p>{% endif %}
</div>
```

A callback may change other state: here the page re-renders with the new
text. Callbacks can be `async def`. Each source and output has at most one
callback. A second subscription for the same pair is an error when the class
is defined.

### Browser-owned visibility and observations

With `visibility="client"`, the browser opens and closes the menu as a native
popover, with no server round trip. Python does not own `open` then (reading
or setting it raises), but it can still *observe* the visibility:

```python
from djust import LiveView
from djust.components.interactive import DropdownMenu


class HelpView(LiveView):
    template_name = "help/menu.html"

    help_menu = DropdownMenu(
        label="Help",
        items=[{"label": "Shortcuts", "value": "shortcuts"}],
        visibility="client",
    )

    def mount(self, request, **kwargs):
        self.help_opened = 0

    @help_menu.on.toggled
    def on_help_menu_toggled(self, component: DropdownMenu, open: bool) -> None:
        if open:
            self.help_opened += 1
```

```html
<div dj-root>
    {{ help_menu }}
    <p>Help opened {{ help_opened }} times.</p>
</div>
```

- **No subscription, no traffic.** Without `@help_menu.on.toggled`, toggling
  sends nothing to the server.
- **No change, no render.** An observer that changes no state causes no
  render and no page update.
- **Reports are the current state, not a log.** Reports sent while one is in
  flight are merged into the latest one. Duplicate, out-of-order and stale
  reports are dropped, and after a reconnect the current value is reported
  once. Don't use observations for exactly-once business actions.
- **A report is an observation, not proof.** It comes from the browser. It
  doesn't authorize anything, and djust never stores it as `open`.

## Repeated rows: pass the record id

For a row that only chooses an operation on a record, use no component per
row. Pass the record id to an ordinary view handler:

```python
from django.core.exceptions import PermissionDenied
from djust import LiveView, event_handler


class RowActionsView(LiveView):
    template_name = "projects/row_actions.html"

    def mount(self, request, **kwargs):
        self.projects = [{"id": 42, "name": "Alpha"}, {"id": 87, "name": "Beta"}]
        self.selected_project = 0
        self.selected_action = ""

    @event_handler()
    def project_action(self, project_id: int, action: str) -> None:
        if project_id not in {p["id"] for p in self.projects}:
            raise PermissionDenied
        if action not in {"details", "activity"}:
            raise ValueError("Unknown project action")
        self.selected_project = project_id
        self.selected_action = action
```

```html
<div dj-root>
    {% for project in projects %}
      <span>{{ project.name }}</span>
      <button type="button" dj-click="project_action"
              dj-value-project-id:int="{{ project.id }}" dj-value-action="details">
        Details
      </button>
    {% endfor %}
    <p>{{ selected_project }}: {{ selected_action }}</p>
</div>
```

For database rows, look up and authorize the record in the handler on every
event. A submitted id, or a list rendered earlier, is not permission.

## Stateful rows: keyed collections

When each row really owns component state (its own open menu, its own
selection), declare a keyed collection. Its members come from `sync()`, as
ordered `(key, DropdownMenu(...))` pairs:

```python
from django.core.exceptions import PermissionDenied
from djust import LiveView
from djust.components.interactive import DropdownMenu


class ProjectListView(LiveView):
    template_name = "projects/list.html"
    row_menus = DropdownMenu.collection()

    def mount(self, request, **kwargs):
        self.projects = [{"id": 42, "name": "Alpha"}, {"id": 87, "name": "Beta"}]
        self.selected = ""
        self._sync_menus()

    def _sync_menus(self):
        self.row_menus.sync(
            [
                (
                    str(project["id"]),
                    DropdownMenu(
                        label=project["name"],
                        items=[
                            {"label": "Details", "value": "details"},
                            {"label": "Archive", "value": "archive"},
                        ],
                    ),
                )
                for project in self.projects
            ]
        )

    @row_menus.on.selected
    def on_row_menus_selected(self, component: DropdownMenu, value: str) -> None:
        project_id = int(component.key)
        if project_id not in {project["id"] for project in self.projects}:
            raise PermissionDenied
        self.selected = "%s:%s" % (project_id, value)
        if value == "archive":
            self.projects = [p for p in self.projects if p["id"] != project_id]
            self._sync_menus()
```

```html
<div dj-root>
    <ul>
        {% for menu in row_menus.values %}<li>{{ menu }}</li>{% endfor %}
    </ul>
    <p>{{ selected }}</p>
</div>
```

**Callbacks.** One `@row_menus.on.selected` callback receives whichever row
emitted. `component.key` is that row's key, not its position.

**Keys and `sync()`.** Keys are nonempty strings, normally record ids. The
`DropdownMenu(...)` you pass to `sync()` is configuration only: djust copies
it, so you can reuse one for many keys. On every `sync()`:

- **Kept keys** keep their live state (open, selection) and take the new label
  and items. A selection the new items no longer allow is cleared, without an
  output.
- **Reordering** moves rows, never their state.
- **A dropped key** is gone: later events for it are refused. Adding the key
  back gives a new row that starts closed.
- **Duplicate keys** or a bad pair raise an error before anything changes.

You may call `sync()` anywhere your view's code runs, including a row's own
callback, as `archive` does above.

**Reading the rows.** `row_menus.get(key)` returns the live row, or `None`.
`len(row_menus)` counts the rows, and iterating yields them in `sync()` order,
the same as `row_menus.values`.

Use a collection only when rows own state. For a row action on a record,
[pass the record id](#repeated-rows-pass-the-record-id) instead. Large lists
still need pagination or virtualization.

## Persistence, keyboard and security

- **Persistence.**
  - A server-owned menu's `open` and `selected` are saved with the view's
    session state, like its other state. They come back wherever that state
    does: on every HTTP-fallback request, and on a reconnect when the view
    sets `enable_state_snapshot = True`. Callbacks and configuration are never
    saved: they come from your code.
  - With `enable_state_snapshot = True`, Back navigation restores a page's
    menus with the same identities. A page with a keyed collection is instead
    mounted fresh on Back, so `sync()` supplies the current rows and a removed
    row cannot come back.
  - Under the explicit exposure policy, menu state is not persisted.
- **Keyboard and focus.**
  - A server-owned menu's trigger and items are ordinary buttons: Tab to
    them, and press Enter or Space.
  - A client-owned menu is a native popover: Enter on the trigger opens it,
    Escape or a click outside closes it, and focus returns to the trigger.
    Choosing an item closes it at once.
  - Neither mode provides arrow-key navigation between items, the way a
    complete ARIA menu does.
- **Security.**
  - Outputs are not browser-callable: a client can't invoke
    `on_project_menu_selected` directly, and an unknown or stale component
    identity is refused.
  - But the browser can still target any visible menu and send any value, so
    the component validates values against its own `items`. Your callback
    must still authorize what the value means for the underlying record, as
    any event handler does.
  - A failing callback reports the error like any event. It doesn't undo the
    component's own change on a WebSocket or SSE connection. Over the HTTP
    fallback, a failed request saves nothing.
- **Limits in djust 1.3.** Interactive components don't work on
  `use_actors = True` views; system check `djust.V020` reports that.
