# Events

djust uses `dj-*` HTML attributes to bind server-side Python handlers to client-side DOM events.

## Event Bindings

| Attribute                     | Fires when                       | Handler receives                  |
| ----------------------------- | -------------------------------- | --------------------------------- |
| `dj-click="handler"`          | Element is clicked               | `dj-value-*` attrs as kwargs      |
| `dj-input="handler"`          | Input value changes (`input` event; text inputs debounced 300 ms by default) | `value=` string, `field=` name    |
| `dj-change="handler"`         | Input/select loses focus         | `value=` string, `field=` name    |
| `dj-submit="handler"`         | Form is submitted                | The named fields as kwargs        |
| `dj-keydown.enter="handler"`  | Enter key pressed                | `key`, `code` + `dj-value-*`      |
| `dj-keydown.escape="handler"` | Escape key pressed               | `key`, `code` + `dj-value-*`      |
| `dj-window-keydown="handler"` | Keydown on `window`              | `key`, `code` + `dj-value-*`     |
| `dj-document-click="handler"` | Click on `document`              | `clientX`, `clientY` + `dj-value-*` |
| `dj-click-away="handler"`     | Click outside element            | `dj-value-*` attrs as kwargs      |
| `dj-shortcut="bindings"`      | Keyboard shortcut matched        | `key`, `code`, `shortcut`         |
| `dj-mounted="handler"`        | Element enters DOM (after patch) | `dj-value-*` attrs as kwargs      |

A handler receives the `dj-value-*` arguments of its element, always, and the
values the browser generates for the event (`value`, `field`, form fields,
`key`, `code`) only when it declares a parameter of that name or a `**`
catch-all. That is the strict parameter policy, which the examples in these
guides use; see [Typed event parameters](#typed-event-parameters-strict-policy).

## Defining Handlers

All event handlers **must** use the `@event_handler()` decorator. djust blocks any method not decorated with it (security):

```python
from djust import state
from djust.decorators import event_handler

class MyView(LiveView):
    count = state(0, persist="server")
    query = state("")
    items = state(default_factory=list)

    @event_handler()
    def increment(self):
        self.count += 1

    @event_handler()
    def search(self, value: str):
        """dj-input/dj-change: current input value arrives as 'value'."""
        self.query = value

    @event_handler()
    def delete(self, item_id: int):
        """dj-value-item-id="{{ item.id }}" becomes item_id=5 (converted by the annotation)."""
        self.items = [i for i in self.items if i["id"] != item_id]

    @event_handler()
    def save(self, **form_data: str):
        """dj-submit: every named form field arrives through the typed catch-all."""
        name = form_data.get("name", "")
        email = form_data.get("email", "")
```

**Rules** (the strict parameter policy; see
[Typed event parameters](#typed-event-parameters-strict-policy)):

- Annotate every parameter: the signature is the event's contract, and an
  event that does not fit it is rejected before the handler runs
- Declare the parameters the binding sends; `manage.py check` reports a binding
  its handler would reject (`djust.T020`)
- Pass arguments with `dj-value-*` attributes; `data-*` attributes are not
  arguments
- Give a parameter a default only when a binding may omit it
- Annotations convert the value (`item_id: int` converts `"5"` → `5`)
- `value` is the parameter name for the current value of a `dj-input` or
  `dj-change` element, and `field` for its `name`
- A form handler declares the fields it uses, or takes them all with a typed
  catch-all (`**form_data: str`)

## Template Bindings

### Click

```html
<!-- Simple click -->
<button dj-click="increment">+</button>

<!-- Pass an argument via a dj-value-* attribute -->
<button dj-click="delete" dj-value-item-id="{{ item.id }}">Delete</button>
```

`dj-value-*` attributes are converted: `dj-value-item-id` → `item_id`. See [Event arguments (`dj-value-*`)](#event-arguments-dj-value) for full details.

### Inline handler arguments

For literal one-shot values, you can pass arguments to the handler
inline using function-call syntax in the directive — no `dj-value-*`
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

Use `dj-value-*` attributes when the value comes from a template
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

<!-- dj-value-* attributes and inline args work like every other directive -->
<div dj-mouseenter="preview" dj-value-item-id="{{ item.id }}">…</div>
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

`dj-window-scroll` and `dj-window-resize` fire on every browser event. They are not throttled by default, and they ignore `dj-throttle` on the element. Scroll handlers receive `scrollX`/`scrollY`; resize handlers receive `innerWidth`/`innerHeight`. Rate-limit them with the handler decorator:

```python
from djust.decorators import event_handler, throttle

@event_handler()
@throttle(interval=0.15)
def on_scroll(self, scrollY: int = 0):
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

## Form Field Targeting (`field`)

When multiple form fields share one handler (e.g., `dj-change="validate"`), the `field` parameter tells the server which field triggered the event. This enables efficient per-field validation without needing a separate handler per field.

```html
<form>
    <input name="email" dj-change="validate" />
    <input name="username" dj-change="validate" />
</form>
```

```python
@event_handler()
def validate(self, value: str, field: str):
    if field == "email":
        self.email_error = "" if "@" in value else "Invalid email"
    elif field == "username":
        self.username_error = "" if len(value) >= 3 else "Too short"
```

`field` names the triggering element: its `data-field` attribute, else its `name`, else its `id` without a leading `id_` (Django's prefix), else `null`. The browser sends it with a `dj-change` or `dj-input` event when the handler declares it, as it does `value`.

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
def on_chart_ready(self, chart_type: str = ""):
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

`dj-debounce` and `dj-throttle` work with all event types: `dj-click`, `dj-change`, `dj-input`, `dj-keydown`, `dj-keyup`. Each element gets its own independent timer. `data-debounce`/`data-throttle` are older spellings honoured only on `dj-input`; use `dj-debounce`/`dj-throttle` everywhere.

### Python Decorators

Use decorators to rate-limit a handler for every element that triggers it (applied in the browser before the event is sent):

```python
from djust.decorators import event_handler, debounce, throttle

# Wait 500ms after the last keystroke before firing
@event_handler()
@debounce(wait=0.5)
def search(self, value: str):
    self.query = value
    self._refresh()

# Fire at most once per second
@event_handler()
@throttle(interval=1.0)
def on_scroll(self, scrollY: int = 0):
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
    <button dj-click="delete" dj-value-item-id="{{ item.id }}">Delete</button>
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
- `djust:path-changed` — fires on `document` whenever the rendered pathname moves (`live_redirect`, `live_patch` to a new path, an auto-navigate click, back/forward), right after history is updated, whether or not the loading bar is enabled; `detail` is `{pathname, search}`. Use it, not `navigate-end`, to keep chrome outside `[dj-root]` in sync with the URL

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

After a WebSocket or SSE reconnect, elements with `dj-auto-recover` automatically fire a server event with serialized DOM state, enabling the server to restore state that the default form-value replay cannot:

```html
<div dj-auto-recover="restore_canvas" data-canvas-id="main">
    <canvas id="drawing-canvas"></canvas>
    <input name="brush_size" value="5" />
</div>
```

```python
@event_handler(parameter_policy="strict")
def restore_canvas(self, form_values: dict[str, Any], data_attrs: dict[str, str]):
    """Called automatically after reconnect with DOM state from the container."""
    self.brush_size = int(form_values.get("brush_size") or 5)
    self.canvas_id = data_attrs.get("canvas-id", "")
```

Key behavior:
- Does **not** fire on initial page load — only after reconnection
- Serializes the container's form field values and `data-*` attributes; the handler declares `form_values` and/or `data_attrs` and receives the ones it declares, validated like any argument. `data-*` keys keep their dashes (`canvas-id`). The container's `dj-value-*` attributes arrive as ordinary arguments
- A 1.x handler that reads `_form_values` / `_data_attrs` keeps working only under the legacy policy; check `djust.V031` names the new signature (see the [reconnection guide](../guides/reconnection.md#custom-recovery-with-dj-auto-recover))
- Multiple independent `dj-auto-recover` elements can coexist on the same page
- Use for complex state (drag positions, canvas state, multi-step wizard progress) that form replay cannot restore

## Event arguments (`dj-value-*`)

A `dj-value-*` attribute on an element with a `dj-click` (or any `dj-*` event)
becomes a keyword argument of the handler. The name follows two rules:

1. **The `dj-value-` prefix is stripped** — `dj-value-item-id` becomes `item-id`
2. **Dashes become underscores** — `item-id` becomes `item_id`

So `dj-value-item-id="5"` arrives in your handler as `item_id`. The form
matches Phoenix LiveView's `phx-value-*`.

### Basic example

```html
<button dj-click="delete" dj-value-item-id="{{ item.id }}" dj-value-category="{{ item.category }}">
    Delete
</button>
```

```python
@event_handler()
def delete(self, item_id: int, category: str):
    # dj-value-item-id="5"      → item_id=5   (converted by the annotation)
    # dj-value-category="draft" → category="draft"
    self.items = [i for i in self.items if i["id"] != item_id]
```

### Type conversion

Attribute values are text. The handler's annotation converts them, so no
`int()` call is needed:

```python
@event_handler()
def update(self, count: int, price: float, enabled: bool):
    # dj-value-count="42"     → count=42
    # dj-value-price="19.99"  → price=19.99
    # dj-value-enabled="true" → enabled=True
    pass
```

The accepted annotations and what each one accepts are listed under
[Conversion rules](#conversion-rules).

**Wire hints (client-side)** — a colon and a type after the attribute name
convert the value in the browser, before it is sent:

```html
<button dj-click="update"
        dj-value-count:int="42"
        dj-value-price:float="19.99"
        dj-value-enabled:bool="true"
        dj-value-tags:list='["a", "b"]'>
    Update
</button>
```

| Suffix | Converts to | Example |
|--------|------------|---------|
| `:int` / `:integer` | Number (integer) | `dj-value-count:int="42"` → `42` |
| `:float` / `:number` | Number (float) | `dj-value-price:float="19.99"` → `19.99` |
| `:bool` / `:boolean` | Boolean (`true`/`false`, `1`/`0`, `yes`/`no`, `on`/`off`) | `dj-value-active:bool="true"` → `true` |
| `:list` / `:array` | A JSON array | `dj-value-tags:list='["a","b"]'` → `["a", "b"]` |
| `:json` | Any JSON value | `dj-value-ids:json='[1, 2]'` → `[1, 2]` |

A hint is optional, because the annotation converts the text anyway. A hint
that contradicts the annotation (`:int` on a `str` parameter), and a value the
hint cannot parse (`dj-value-n:int="12abc"`), are rejected in the browser
before anything is sent.

### `data-*` attributes are not arguments

`data-*` attributes on an event element are not sent to the handler. Keep
them for your own JavaScript and CSS. A few are djust markers that change how
the event is sent, not what it carries: `data-component-id` routes the event
to a component, and `data-debounce` / `data-throttle` on a `dj-input` element
are older spellings of `dj-debounce` / `dj-throttle`.

### Quick reference

| HTML attribute | Python kwarg | Notes |
|---|---|---|
| `dj-value-item-id="5"` | `item_id` | Dashes → underscores; `item_id: int` receives `5` |
| `dj-value-item-id:int="5"` | `item_id=5` | Client-side conversion |
| `dj-value-user-name="Jo"` | `user_name="Jo"` | Multi-word names |
| `dj-value-x="1"` | `x` | Single-char names work |
| `dj-value-section="hero"` | `section="hero"` | |

## Typed event parameters (strict policy)

Under the strict parameter policy, the handler's Python signature is the
event's contract. Named parameters, their annotations and their defaults
decide what an event may carry. Values are converted exactly, and an event
that doesn't fit is rejected before your code runs. The examples in these
guides are written for it. In 2.0.0rc1 it is still an opt-in; it becomes the
only behaviour before 2.0.0 final
([#4171](https://github.com/djust-org/djust/issues/4171)). Turn it on for the
whole project:

<!-- djust-example: skip -- settings fragment: the project-wide policy switch -->
<!-- doc-snippet-check: skip -->
```python
LIVEVIEW_CONFIG = {"event_parameter_policy": "strict"}
```

or for one handler with `@event_handler(parameter_policy="strict")`.

### Example: typed click arguments

<!-- djust-example: item-selection scenario=strict-policy -->
```python
from djust import LiveView, state
from djust.decorators import event_handler


class ItemSelectionView(LiveView):
    template_name = "items/select.html"
    selected_id = state(0)
    active = state(False)

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
from djust import LiveView, state
from djust.decorators import event_handler


class NoteView(LiveView):
    template_name = "notes/edit.html"
    query = state("")
    saved = state(default_factory=dict)

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
`dj-value-*` names become arguments: `data-*` attributes are ignored for strict
handlers. (The JSON `dj-params` attribute is not read at all since 2.0.)

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
| `dict[str, T]` | A JSON object whose values are valid `T` (keys are strings). Not query-string text |
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
- `dj-auto-recover` handlers declare the generated `form_values` /
  `data_attrs` dictionaries like any argument (check `V031` reports one that
  still reads the 1.x `_form_values` / `_data_attrs`).
- `dj-model` sends the fixed `field`/`value` pair its handler declares.

### Migrating a handler

Converting a 1.x handler (its `data-*` arguments, `**kwargs` and defaults) is
covered step by step in
[Migrating to djust 2.0](../guides/migrating-to-2.0.md#2-make-event-handlers-strict).

## Next Steps

- [Templates](./templates.md) — full template directives reference
- [Template Cheat Sheet](../guides/template-cheatsheet.md) — quick reference for all `dj-*` attributes
- [Document Metadata](../guides/document-metadata.md) — dynamic page titles and meta tags
- [Loading States](../guides/loading-states.md) — loading directives, `dj-disable-with`, background work
- [State Management](../state/index.md) — debounce, throttle, loading states, optimistic updates
- [Hooks](../guides/hooks.md) — client-side JavaScript lifecycle hooks
