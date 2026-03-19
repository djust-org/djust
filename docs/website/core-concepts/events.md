# Events

djust uses `dj-*` HTML attributes to bind server-side Python handlers to client-side DOM events.

## Event Bindings

| Attribute                     | Fires when                       | Handler receives                  |
| ----------------------------- | -------------------------------- | --------------------------------- |
| `dj-click="handler"`          | Element is clicked               | `data-*` attrs as kwargs          |
| `dj-input="handler"`          | Input value changes (keyup)      | `value=` string, `_target=` name  |
| `dj-change="handler"`         | Input/select loses focus         | `value=` string, `_target=` name  |
| `dj-submit="handler"`         | Form is submitted                | All named fields as kwargs        |
| `dj-keydown.enter="handler"`  | Enter key pressed                | `**kwargs`                        |
| `dj-keydown.escape="handler"` | Escape key pressed               | `**kwargs`                        |
| `dj-window-keydown="handler"` | Keydown on `window`              | `key`, `code` + `dj-value-*`     |
| `dj-document-click="handler"` | Click on `document`              | `clientX`, `clientY` + `dj-value-*` |
| `dj-click-away="handler"`     | Click outside element            | `dj-value-*` attrs as kwargs      |
| `dj-shortcut="bindings"`      | Keyboard shortcut matched        | `key`, `code`, `shortcut`         |
| `dj-mounted="handler"`        | Element enters DOM (after patch) | `dj-value-*` attrs as kwargs      |

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

### Window & Document Events

Bind event listeners on `window` or `document` instead of the element itself. The declaring element provides context (component ID, `dj-value-*` params) but the listener is attached to the global target.

```html
<!-- Close modal on Escape (anywhere on page) -->
<div dj-window-keydown.escape="close_modal">

<!-- Track scroll position -->
<div dj-window-scroll="on_scroll" dj-value-section="hero">

<!-- Detect clicks anywhere on page -->
<div dj-document-click="on_background_click">

<!-- Handle window resize -->
<div dj-window-resize="on_resize">
```

Supported attributes:

| Attribute | Target | Event |
|---|---|---|
| `dj-window-keydown` | `window` | `keydown` |
| `dj-window-keyup` | `window` | `keyup` |
| `dj-window-scroll` | `window` | `scroll` |
| `dj-window-click` | `window` | `click` |
| `dj-window-resize` | `window` | `resize` |
| `dj-document-keydown` | `document` | `keydown` |
| `dj-document-keyup` | `document` | `keyup` |
| `dj-document-click` | `document` | `click` |

Key modifier filtering works the same as `dj-keydown`: `dj-window-keydown.escape="close"`.

`dj-window-scroll` and `dj-window-resize` default to 150ms throttle to prevent flooding. Override with `data-throttle` or `data-debounce` on the element.

### Click Away

Fire an event when the user clicks outside an element. Common for dropdowns, modals, and popovers:

```html
<div dj-click-away="close_dropdown" class="dropdown-menu">
    <!-- clicking outside this div fires close_dropdown -->
</div>
```

Uses a capture-phase document listener, so `stopPropagation()` inside the element does not prevent detection. Supports `dj-confirm` for confirmation dialogs and `dj-value-*` params.

### Keyboard Shortcuts (`dj-shortcut`)

Declarative keyboard shortcuts with modifier key support:

```html
<!-- Single shortcut -->
<div dj-shortcut="escape:close_modal">

<!-- Multiple shortcuts on one element -->
<div dj-shortcut="ctrl+k:open_search:prevent, escape:close_modal">

<!-- Modifier keys: ctrl, alt, shift, meta (cmd on Mac) -->
<button dj-shortcut="ctrl+shift+s:save_draft:prevent">Save</button>
```

Syntax: `[modifier+...]key:handler[:prevent]`, comma-separated for multiple bindings.

The `prevent` modifier calls `e.preventDefault()` to suppress browser defaults (e.g., `ctrl+k` normally opens the browser URL bar).

Shortcuts are automatically skipped when the user is typing in form inputs (input, textarea, select, contenteditable). Add `dj-shortcut-in-input` to force shortcuts even in inputs.

Handler receives `key`, `code`, and `shortcut` (the matched binding string, e.g., `"ctrl+k"`) as event params, along with any `dj-value-*` attributes.

## Form Field Targeting (`_target`)

When multiple form fields share one handler (e.g., `dj-change="validate"`), the `_target` parameter tells the server which field triggered the event. This enables efficient per-field validation without needing a separate handler per field.

```html
<form>
    <input name="email" dj-change="validate" />
    <input name="username" dj-change="validate" />
</form>
```

```python
@event_handler()
def validate(self, value: str = "", _target: str = "", **kwargs):
    if _target == "email":
        self.email_error = "" if "@" in value else "Invalid email"
    elif _target == "username":
        self.username_error = "" if len(value) >= 3 else "Too short"
```

`_target` is the triggering element's `name` attribute (falling back to `id`, then `null`). It is included automatically in `dj-change`, `dj-input`, and `dj-submit` (submitter button name) events. Matches Phoenix LiveView's `_target` convention.

## Preventing Double Submits

### `dj-disable-with`

Automatically disable a submit button and replace its text during form submission:

```html
<form dj-submit="save">
    {% csrf_token %}
    <input name="title" value="{{ title }}" />
    <button type="submit" dj-disable-with="Saving...">Save</button>
</form>
```

While the server processes the event, the button shows "Saving..." and is disabled. After the server responds, the original text and enabled state are restored. Also works with `dj-click`:

```html
<button dj-click="generate" dj-disable-with="Generating...">Generate Report</button>
```

### `dj-lock`

Prevent an element from firing its event again until the server responds:

```html
<button dj-click="save" dj-lock>Save</button>
```

Unlike `dj-disable-with` (which is cosmetic feedback), `dj-lock` blocks the event from firing at all while a previous invocation is in flight. For form elements (button, input, select, textarea), the element is disabled. For non-form elements (e.g., `<div dj-click="..." dj-lock>`), a `djust-locked` CSS class is applied instead.

Combine both for the full pattern:

```html
<button dj-click="save" dj-lock dj-disable-with="Saving...">Save</button>
```

All locked elements are unlocked when any server response arrives.

## Mounted Event (`dj-mounted`)

Fire a server event when an element enters the DOM after a VDOM patch:

```html
{% if show_chart %}
<div dj-mounted="on_chart_ready" dj-value-chart-type="bar">
    <canvas id="my-chart"></canvas>
</div>
{% endif %}
```

```python
@event_handler()
def on_chart_ready(self, chart_type: str = "", **kwargs):
    self.chart_data = load_chart_data(chart_type)
```

Key behavior:
- Only fires after VDOM patches (not on initial page load)
- Includes `dj-value-*` attributes from the mounted element as event params
- Uses a WeakSet internally to prevent duplicate fires for the same DOM node
- Handlers should be idempotent (if the element is replaced by a patch, it fires again for the new node)

Use cases: trigger data loading when a tab becomes active, initialize third-party widgets, scroll new elements into view, animate elements on appearance.

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

For long-running operations (API calls, AI generation), use the `@background` decorator to run work in a background thread. See [Loading States & Background Work](../guides/loading-states.md) for details.

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
- [Template Cheat Sheet](../guides/template-cheatsheet.md) — quick reference for all `dj-*` attributes
- [Loading States](../guides/loading-states.md) — loading directives, `dj-disable-with`, background work
- [State Management](../state/index.md) — debounce, throttle, loading states, optimistic updates
- [Hooks](../guides/hooks.md) — client-side JavaScript lifecycle hooks
