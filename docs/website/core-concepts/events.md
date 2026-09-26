# Events

djust uses `dj-*` HTML attributes to bind server-side Python handlers to client-side DOM events.

## Event Bindings

| Attribute                     | Fires when                       | Handler receives                  |
| ----------------------------- | -------------------------------- | --------------------------------- |
| `dj-click="handler"`          | Element is clicked               | `data-*` attrs as kwargs          |
| `dj-input="handler"`          | Input value changes (`input` event; text inputs debounced 300 ms by default) | `value=` string, `_target=` name  |
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

**Rules** (the default, legacy parameter policy; for closed, typed signatures
see [Typed event parameters](#typed-event-parameters-strict-policy)):

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

### Inline handler arguments

For literal one-shot values, you can pass arguments to the handler
inline using function-call syntax in the directive — no `data-*`
attribute needed:

```html
<button dj-click="set_period('month')">Monthly</button>
<button dj-click="set_period('year')">Yearly</button>

<!-- Multiple positional args -->
<button dj-click="add_to_cart('SKU-42', 3)">Add 3</button>

<!-- Mixed literal types: strings, numbers, booleans, null -->
<button dj-click="apply_filters('active', 18, true, null)">Adults only</button>
```

The handler receives the args positionally:

```python
@event_handler
def set_period(self, period: str):
    self.period = period

@event_handler
def add_to_cart(self, sku: str, quantity: int):
    ...
```

Use `data-*` attributes when the value comes from a template
expression (`{{ item.id }}`); use inline args when the value is a
literal known at template-author time. (Available since v0.1.7.)

### Input

```html
<!-- Fires 300 ms after the user stops typing (override with dj-debounce) -->
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

### Mouse Enter / Leave

```html
<!-- Fires when the pointer enters the element -->
<div dj-mouseenter="preview">Hover me</div>

<!-- Fires when the pointer leaves the element -->
<div dj-mouseenter="preview" dj-mouseleave="hide_preview">Hover me</div>

<!-- data-* attributes and inline args work like every other directive -->
<div dj-mouseenter="preview({{ item.id }})" data-item-id="{{ item.id }}">…</div>
```

`mouseenter` and `mouseleave` do **not bubble**: moving the pointer from an
element into one of its children fires neither the element's
`dj-mouseleave` nor a second `dj-mouseenter` — entering a child is not
leaving the parent. (This is what distinguishes them from `mouseover` /
`mouseout`, which fire on every descendant crossing.) Because they do not
bubble, djust attaches these listeners directly to the declaring element
rather than delegating on the root; `dj-debounce` / `dj-throttle` and
`dj-confirm` are honoured as on other directives.

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

`dj-window-scroll` and `dj-window-resize` fire on every browser event. They are not throttled by default, and they ignore `dj-throttle`/`data-throttle` on the element. Scroll handlers receive `scrollX`/`scrollY`; resize handlers receive `innerWidth`/`innerHeight`. Rate-limit them with the handler decorator:

```python
from djust.decorators import event_handler, throttle

@event_handler()
@throttle(interval=0.15)
def on_scroll(self, scrollY: int = 0, **kwargs):
    self.scroll_pos = scrollY
```

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
<button dj-shortcut="ctrl+shift+S:save_draft:prevent">Save</button>
```

With `shift`, write a letter key as the browser reports it (`S`, not `s`): the key is compared case-sensitively against `KeyboardEvent.key`.

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

`_target` is sent to legacy-policy handlers only. A strict handler uses `field` or an explicit `dj-value-*` argument instead.

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

`dj-debounce` and `dj-throttle` work with all event types: `dj-click`, `dj-change`, `dj-input`, `dj-keydown`, `dj-keyup`. Each element gets its own independent timer. `data-debounce`/`data-throttle` are legacy spellings honoured only on `dj-input`; use `dj-debounce`/`dj-throttle` everywhere.

### Python Decorators

Use decorators to rate-limit a handler for every element that triggers it (applied in the browser before the event is sent):

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
def on_scroll(self, scrollY: int = 0, **kwargs):
    self.scroll_pos = scrollY
```

HTML attributes and Python decorators can be combined: the HTML attribute wraps one element's listener, and the decorator gates every send of that handler. Both run client-side and compose.

## Loading States

Show feedback while a handler is running — use the `dj-loading.*` attributes
(there is no `loading` decorator in `djust.decorators`; the client manages the
pending state automatically while the event is in flight):

```html
<button dj-click="save" dj-loading.disable>
    <span dj-loading.hide dj-loading.for="save">Save</span>
    <span dj-loading.show dj-loading.for="save" style="display:none">Saving…</span>
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
<!-- The handler receives one param, text (the copied string) -->
<button dj-copy="#snippet" dj-copy-event="copied">Copy</button>
```

| Attribute | Description |
|---|---|
| `dj-copy="text"` | Literal text to copy |
| `dj-copy="#selector"` | Copy `textContent` of the matched element |
| `dj-copy-feedback="text"` | Button text shown for 1.5s after copy (default: `"Copied!"`) |
| `dj-copy-class="class"` | CSS class added for 2s after copy (default: `dj-copied`) |
| `dj-copy-event="handler"` | Server event fired after successful copy; receives `text` |

All enhancements are backward compatible with existing `dj-copy` usage.

## Reconnection Recovery (`dj-auto-recover`)

After a WebSocket reconnect, elements with `dj-auto-recover` automatically fire a server event with serialized DOM state, enabling the server to restore state that the default form-value replay cannot:

```html
<div dj-auto-recover="restore_canvas" data-canvas-id="main">
    <canvas id="drawing-canvas"></canvas>
    <input name="brush_size" value="5" />
</div>
```

```python
@event_handler()
def restore_canvas(self, _form_values=None, _data_attrs=None, **kwargs):
    """Called automatically after reconnect with DOM state from the container."""
    form = _form_values or {}
    data = _data_attrs or {}
    self.brush_size = int(form.get("brush_size") or 5)
    self.canvas_id = data.get("canvas-id", "")
```

Key behavior:
- Does **not** fire on initial page load — only after reconnection
- Serializes the container's form field values and `data-*` attributes; they arrive as the `_form_values` and `_data_attrs` dicts. `data-*` keys keep their dashes (`canvas-id`), and `dj-value-*` attributes are not included
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

`data-key` is **not** skipped. If the keyed element itself carries a `dj-*` event, the handler receives `key=`, so accept `**kwargs` or use `dj-key` (also a VDOM list diffing key) instead.

### Quick reference

| HTML attribute | Python kwarg | Notes |
|---|---|---|
| `data-item-id="5"` | `item_id="5"` | Dashes → underscores |
| `data-item-id:int="5"` | `item_id=5` | Client-side coercion |
| `data-user-name="Jo"` | `user_name="Jo"` | Multi-word names |
| `data-x="1"` | `x="1"` | Single-char names work |
| `data-dj-preset="dark"` | `preset="dark"` | `dj_` prefix stripped |
| `dj-value-section="hero"` | `section="hero"` | `dj-value-*` form |

## Typed event parameters (strict policy)

Under the strict parameter policy, the handler's Python signature is the
event's contract. Named parameters, their annotations and their defaults
decide what an event may carry. Values are converted exactly, and an event
that doesn't fit is rejected before your code runs. Legacy remains the
default. Opt in per handler, or for the whole project:

<!-- djust-example: skip -- settings fragment: the project-wide policy switch -->
<!-- doc-snippet-check: skip -->
```python
LIVEVIEW_CONFIG = {"event_parameter_policy": "strict"}
```

`@event_handler(parameter_policy="strict")` opts one handler in, and
`parameter_policy="legacy"` opts one out of a strict project.

### Example: typed click arguments

<!-- djust-example: item-selection scenario=strict-policy -->
```python
from djust import LiveView
from djust.decorators import event_handler


class ItemSelectionView(LiveView):
    template_name = "items/select.html"

    def mount(self, request, **kwargs):
        self.selected_id = 0
        self.active = False

    @event_handler(parameter_policy="strict")
    def select_item(self, item_id: int, active: bool = False) -> None:
        # A demonstration allowlist, not a substitute for database authorization.
        if item_id not in {42, 87}:
            raise ValueError("Unknown item")
        self.selected_id = item_id
        self.active = active
```

```html
<button type="button" dj-click="select_item"
        dj-value-item-id="42" dj-value-active="true">Select item</button>
```

`dj-value-item-id` arrives as `item_id`. No `:int` suffix is needed, because
the annotation converts `"42"` to `42`. `item_id` is required; `active`
keeps its default when the attribute is absent. A converted ID is not an
authorized object: look the record up and check permissions as usual.

### Example: inputs and forms

<!-- djust-example: note-view scenario=strict-policy -->
```python
from djust import LiveView
from djust.decorators import event_handler


class NoteView(LiveView):
    template_name = "notes/edit.html"

    def mount(self, request, **kwargs):
        self.query = ""
        self.saved = {}

    @event_handler(parameter_policy="strict")
    def search(self, value: str) -> None:
        self.query = value

    @event_handler(parameter_policy="strict")
    def save(self, title: str, **fields: str) -> None:
        self.saved = {"title": title, **fields}
```

```html
<input name="q" dj-input="search">
<form dj-submit="save">
    <input name="title" value="Draft">
    <input name="notes" value="Hello">
    <button type="submit">Save</button>
</form>
```

The browser sends a generated value only when the handler declares a
parameter with that name, or has a `**` catch-all:
- `value` and `field` for `dj-input` and `dj-change`;
- the form's fields for `dj-submit`;
- `key` and `code` for keyboard events.

`search(value)` therefore receives just `value`, and `save` receives every
field through `**fields`. `dj-value-*` arguments are always sent, and one that
reuses a generated name is rejected. `_target` is never sent. Only
`dj-value-*` names become arguments: `data-*` attributes and `dj-params` are
ignored for strict handlers.

### Conversion rules

| Annotation | Accepts |
| --- | --- |
| `str` | Text, unchanged (no trimming) |
| `int` | An integer, or complete decimal-integer text. Not `true`, blank text, `4.2`, `1_000` or `12abc` |
| `float` | A finite number or numeric text. Not a bool, NaN or infinity |
| `bool` | `true`/`false`, `1`/`0`, `yes`/`no`, `on`/`off` (any case), or a JSON bool |
| `Decimal` | A decimal string or an integer. Not a binary float |
| `UUID` | A valid UUID string |
| `date` | ISO `YYYY-MM-DD` only |
| `Optional[T]` | JSON `null` or a valid `T`. The argument is still required unless it has a default |
| `list[T]` | A JSON array whose members are valid `T`. Not comma-separated text |
| `Any` | Anything, unchecked: an explicit escape hatch |

Other annotations, and named parameters without one, are rejected at startup
(check `djust.V016`). `coerce_types=False` keeps validation but turns
conversion off, so only already-typed values pass. Positional-only and
keyword-only parameters keep their Python meaning:
`dj-click="choose(3)"` fills the leading positional parameters, and the same
value given by name as well is an error.

### What a rejection looks like

The browser checks strict arguments before sending: malformed typed literals
such as `dj-value-n:int="12abc"`, wire hints that contradict the annotation,
and duplicate names. On a failure it applies no loading, disable or
optimistic effect. It logs a fixed message and dispatches `djust:error`,
which the DEBUG overlay shows. The server validates every event again,
including hand-crafted messages. It rejects missing, extra, duplicate and
invalid arguments without calling the handler, and its error names the
parameter and expected type, never the submitted value.

### Framework context and exceptions

- Routing keys (`view_id`, `component_id`) and client bookkeeping are never
  handler arguments. Names starting with `_` are reserved (check `V016`).
- `dj-auto-recover` handlers always run under the legacy policy (check
  `V019` warns about a strict declaration).
- `dj-model` sends the fixed `field`/`value` pair its handler declares.

### Migrating a handler

1. Annotate every named parameter, removing defaults that only existed to
   hide missing input.
2. Replace `data-*` and `dj-params` arguments with `dj-value-*`, and
   `_target` with `field` or a `dj-value-*` argument.
3. Add `parameter_policy="strict"`, drop `**kwargs` unless the handler
   really takes an open payload, and run `manage.py check`: C022 and
   V016–V019 report what strict dispatch would reject.

## Next Steps

- [Templates](./templates.md) — full template directives reference
- [Template Cheat Sheet](../guides/template-cheatsheet.md) — quick reference for all `dj-*` attributes
- [Document Metadata](../guides/document-metadata.md) — dynamic page titles and meta tags
- [Loading States](../guides/loading-states.md) — loading directives, `dj-disable-with`, background work
- [State Management](../state/index.md) — debounce, throttle, loading states, optimistic updates
- [Hooks](../guides/hooks.md) — client-side JavaScript lifecycle hooks
