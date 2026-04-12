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

`data-*` attributes are converted: `data-item-id` → `item_id`. See [Data Attribute Naming Convention](#data-attribute-naming-convention) for full details.

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

### HTML Attributes (`dj-debounce` / `dj-throttle`)

Apply debounce or throttle to any `dj-*` event attribute directly in HTML, giving per-element control without changing the Python handler:

```html
<!-- Debounce: wait 300ms after last keystroke before firing -->
<input dj-input="search" dj-debounce="300" />

<!-- Throttle: fire at most every 500ms -->
<button dj-click="poll_status" dj-throttle="500">Refresh</button>

<!-- Disable default debounce on dj-input (fires immediately) -->
<input dj-input="on_change" dj-debounce="0" />

<!-- Defer until blur (Phoenix parity) -->
<input dj-input="validate" dj-debounce="blur" />
```

`dj-debounce` and `dj-throttle` work with all event types: `dj-click`, `dj-change`, `dj-input`, `dj-keydown`, `dj-keyup`. Each element gets its own independent timer. HTML attributes take precedence over `data-debounce`/`data-throttle`.

### Python Decorators

Use decorators to control how often handlers fire server-side:

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

HTML attributes and Python decorators can be combined: the HTML attribute controls client-side timing, the decorator controls server-side timing.

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

## UI Feedback Attributes

### Connection State CSS Classes

djust automatically applies CSS classes to `<body>` based on transport state:

- `dj-connected` — WebSocket/SSE connection is open
- `dj-disconnected` — WebSocket/SSE connection is lost

Both classes are removed on intentional disconnect (TurboNav navigation). Use these for CSS-driven connection indicators:

```css
body.dj-disconnected [dj-root] { opacity: 0.5; }
.offline-banner { display: none; }
body.dj-disconnected .offline-banner { display: block; }
```

### `dj-cloak` (FOUC Prevention)

Hide elements until the WebSocket/SSE mount completes:

```html
<div dj-cloak>
    <button dj-click="increment">+</button>
</div>
```

The CSS rule is injected automatically by client.js. The `dj-cloak` attribute is removed when the mount response arrives.

### `dj-scroll-into-view`

Auto-scroll an element into view after it appears in the DOM (mount or VDOM patch):

```html
<div dj-scroll-into-view>New chat message</div>
<div dj-scroll-into-view="instant">Alert</div>
<div dj-scroll-into-view="center">Highlighted item</div>
```

One-shot per DOM node. Supports values: `""` (smooth/nearest), `"instant"`, `"center"`, `"start"`, `"end"`.

### Page Loading Bar

An NProgress-style loading bar appears automatically during TurboNav and `live_redirect` navigation. Control via `window.djust.pageLoading.start()` / `.finish()` or disable with `window.djust.pageLoading.enabled = false`.

**Navigation lifecycle events** are dispatched during `dj-navigate` transitions:

- `djust:navigate-start` — fires when navigation begins
- `djust:navigate-end` — fires when the new page renders

The `.djust-navigating` CSS class is added to `[dj-root]` during navigation, enabling CSS-only page transitions:

```css
[dj-root].djust-navigating main {
    opacity: 0.3;
    transition: opacity 0.15s ease;
    pointer-events: none;
}
```

For advanced use cases, listen for the events in JS:

```javascript
document.addEventListener('djust:navigate-start', () => showSkeleton());
document.addEventListener('djust:navigate-end', () => hideSkeleton());
```

## Clipboard Copy (`dj-copy`)

Copy text to the clipboard on click without a server round-trip:

```html
<!-- Copy literal text -->
<button dj-copy="{{ share_url }}">Copy Link</button>

<!-- Copy text content from another element -->
<button dj-copy="#code-block">Copy Code</button>

<!-- Custom feedback text (default: "Copied!") -->
<button dj-copy="{{ api_key }}" dj-copy-feedback="Done!">Copy Key</button>

<!-- Custom CSS class feedback (adds class for 2 seconds) -->
<button dj-copy="{{ token }}" dj-copy-class="btn-success">Copy</button>

<!-- Fire a server event after successful copy (e.g., for analytics) -->
<button dj-copy="#snippet" dj-copy-event="copied" dj-value-snippet-id="{{ snippet.id }}">Copy</button>
```

| Attribute | Description |
|---|---|
| `dj-copy="text"` | Literal text to copy |
| `dj-copy="#selector"` | Copy `textContent` of the matched element |
| `dj-copy-feedback="text"` | Button text shown for 2s after copy (default: `"Copied!"`) |
| `dj-copy-class="class"` | CSS class added for 2s after copy (default: `dj-copied`) |
| `dj-copy-event="handler"` | Server event fired after successful copy |

All enhancements are backward compatible with existing `dj-copy` usage.

## Reconnection Recovery (`dj-auto-recover`)

After a WebSocket reconnect, elements with `dj-auto-recover` automatically fire a server event with serialized DOM state, enabling the server to restore state that the default form-value replay cannot:

```html
<div dj-auto-recover="restore_canvas" dj-value-canvas-id="main">
    <canvas id="drawing-canvas"></canvas>
    <input name="brush_size" value="5" />
</div>
```

```python
@event_handler()
def restore_canvas(self, brush_size: str = "", canvas_id: str = "", **kwargs):
    """Called automatically after reconnect with DOM state from the container."""
    self.brush_size = int(brush_size) if brush_size else 5
    self.canvas_id = canvas_id
```

Key behavior:
- Does **not** fire on initial page load — only after reconnection
- Serializes form field values and `data-*` attributes from the container element
- Multiple independent `dj-auto-recover` elements can coexist on the same page
- Use for complex state (drag positions, canvas state, multi-step wizard progress) that form replay cannot restore

## Data Attribute Naming Convention

When you add `data-*` attributes to an element with a `dj-click` (or any `dj-*` event), djust automatically extracts them and passes them as keyword arguments to your Python handler. The conversion follows two rules:

1. **The `data-` prefix is stripped** — `data-item-id` becomes `item-id`
2. **Dashes become underscores** — `item-id` becomes `item_id`

So `data-item-id="5"` arrives in your handler as `item_id="5"`.

### Basic example

```html
<button dj-click="delete" data-item-id="{{ item.id }}" data-category="{{ item.category }}">
    Delete
</button>
```

```python
@event_handler()
def delete(self, item_id: int = 0, category: str = "", **kwargs):
    # data-item-id="5"      → item_id=5   (coerced to int via type hint)
    # data-category="draft"  → category="draft"
    self.items = [i for i in self.items if i["id"] != item_id]
```

### Type coercion

All `data-*` values are strings in HTML. djust provides two ways to coerce them:

**Python type hints (server-side)** — add a type annotation to the handler parameter. djust inspects the annotation and coerces the string value automatically:

```python
@event_handler()
def update(self, count: int = 0, price: float = 0.0, enabled: bool = False, **kwargs):
    # data-count="42"     → count=42
    # data-price="19.99"  → price=19.99
    # data-enabled="true" → enabled=True
    pass
```

Supported Python types: `int`, `float`, `bool`, `str`, `list`, `List[T]`, `Optional[T]`.

**Type-hint suffixes (client-side)** — append a colon and type to the attribute name to coerce *before* sending to the server:

```html
<button dj-click="update"
        data-count:int="42"
        data-price:float="19.99"
        data-enabled:bool="true"
        data-tags:json='["a", "b"]'
        data-items:list="a,b,c">
    Update
</button>
```

| Suffix | Converts to | Example |
|--------|------------|---------|
| `:int` / `:integer` | Number (integer) | `data-count:int="42"` → `42` |
| `:float` / `:number` | Number (float) | `data-price:float="19.99"` → `19.99` |
| `:bool` / `:boolean` | Boolean | `data-active:bool="true"` → `true` |
| `:json` / `:object` / `:array` | Parsed JSON | `data-config:json='{"a":1}'` → `{"a": 1}` |
| `:list` | Comma-separated array | `data-tags:list="a,b,c"` → `["a", "b", "c"]` |

Both approaches work together: client-side suffixes convert before sending, and Python type hints coerce on arrival. If you use both, the Python coercion acts on the already-converted value.

### The `dj-value-*` alternative

`dj-value-*` attributes work the same way as `data-*` but take precedence when both are present. They match Phoenix LiveView's `phx-value-*` convention:

```html
<button dj-click="select" dj-value-item-id:int="{{ item.id }}">
    Select
</button>
```

`dj-value-item-id` → `item_id` (same dash-to-underscore rule). The `dj-value-*` form is preferred for event parameters because it avoids collisions with third-party libraries that also use `data-*` attributes.

### Internal attributes are excluded

djust skips its own internal `data-*` attributes so they don't leak into your handler kwargs:

- `data-liveview-*`, `data-live-*`, `data-djust-*` — framework internals
- `data-loading`, `data-component-id` — component machinery
- `data-key` — VDOM list diffing key

### Quick reference

| HTML attribute | Python kwarg | Notes |
|---|---|---|
| `data-item-id="5"` | `item_id="5"` | Dashes → underscores |
| `data-item-id:int="5"` | `item_id=5` | Client-side coercion |
| `data-user-name="Jo"` | `user_name="Jo"` | Multi-word names |
| `data-x="1"` | `x="1"` | Single-char names work |
| `data-dj-preset="dark"` | `preset="dark"` | `dj_` prefix stripped |
| `dj-value-section="hero"` | `section="hero"` | `dj-value-*` form |

## Next Steps

- [Templates](./templates.md) — full template directives reference
- [Template Cheat Sheet](../guides/template-cheatsheet.md) — quick reference for all `dj-*` attributes
- [Document Metadata](../guides/document-metadata.md) — dynamic page titles and meta tags
- [Loading States](../guides/loading-states.md) — loading directives, `dj-disable-with`, background work
- [State Management](../state/index.md) — debounce, throttle, loading states, optimistic updates
- [Hooks](../guides/hooks.md) — client-side JavaScript lifecycle hooks
