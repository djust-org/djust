---
title: "Template Cheat Sheet"
slug: template-cheatsheet
section: guides
order: 10
level: beginner
description: "Quick reference for all djust template directives, event attributes, loading states, and common pitfalls."
---

# Template Cheat Sheet

Quick reference for every directive, attribute, and Django tag used in djust templates.

## Required Template Structure

Every LiveView template needs these two things:

```html
{% load live_tags %}
<!DOCTYPE html>
<html>
<head>
    {% djust_client_config %}   {# Emits client config meta tags; auto-injects ~61 KB gz client JavaScript #}
</head>
<body>
    <div dj-root>                    {# Reactive region — only this is diffed/patched #}
        {{ count }}
        <button dj-click="increment">+</button>
    </div>
</body>
</html>
```

| Attribute / Tag | Required | Description |
|---|---|---|
| `{% load live_tags %}` | Yes | Load djust template tag library |
| `{% djust_client_config %}` | Yes | Emits client config meta tags; djust auto-injects the client JavaScript (~61 KB gz) into every LiveView response |
| `dj-view="myapp.views.CounterView"` | No | Injected automatically onto `<div dj-root>` for LiveView pages; set it by hand only for non-LiveView pages that embed a view |
| `dj-root` | Yes | Marks the reactive subtree — only HTML inside is diffed |

---

## Event Directives

### Click & Submit

| Attribute | Fires On | Handler Receives |
|---|---|---|
| `dj-click="handler"` | Click | `data-*` attributes as kwargs |
| `dj-submit="handler"` | Form submit | All named form fields as kwargs |
| `dj-copy="text"` | Click | Client-only clipboard copy, no server round-trip |
| `dj-copy="#selector"` | Click | Copy `textContent` of matched element |

```html
<!-- Simple click -->
<button dj-click="increment">+</button>

<!-- Pass data to handler -->
<button dj-click="delete" data-item-id="{{ item.id }}">Delete</button>

<!-- Inline args (positional) -->
<button dj-click="set_period('month')">Monthly</button>

<!-- Confirmation dialog before sending -->
<button dj-click="delete" dj-confirm="Are you sure?">Delete</button>

<!-- Form submit -->
<form dj-submit="save_form">
    {% csrf_token %}
    <input name="title" value="{{ title }}" />
    <button type="submit">Save</button>
</form>

<!-- Client-side clipboard copy (literal text) -->
<button dj-copy="{{ share_url }}">Copy link</button>

<!-- Copy from another element -->
<button dj-copy="#code-block">Copy Code</button>

<!-- Copy with feedback and server event -->
<button dj-copy="{{ api_key }}" dj-copy-feedback="Done!" dj-copy-event="copied">Copy</button>
```

#### `dj-copy` options

| Attribute | Description |
|---|---|
| `dj-copy-feedback="text"` | Button text shown for 1.5s after copy (default: `"Copied!"`) |
| `dj-copy-class="class"` | CSS class added for 2s after copy (default: `dj-copied`) |
| `dj-copy-event="handler"` | Server event fired after successful copy |

### Input & Change

| Attribute | Fires On | Handler Receives |
|---|---|---|
| `dj-input="handler"` | Every keystroke | `value=` current field value |
| `dj-change="handler"` | Blur / select change | `value=` current field value |
| `dj-blur="handler"` | Focus leaves element | `value=` current field value |
| `dj-focus="handler"` | Focus enters element | `value=` current field value |
| `dj-model="field_name"` | Two-way binding | Auto-syncs `self.field_name` |

```html
<!-- Live search -->
<input type="text" dj-input="search" value="{{ query }}" />

<!-- Debounce via HTML attribute (preferred) -->
<input dj-input="search" dj-debounce="300" />

<!-- Throttle via HTML attribute -->
<button dj-click="poll" dj-throttle="500">Refresh</button>

<!-- Defer until blur -->
<input dj-input="validate" dj-debounce="blur" />

<!-- Disable default debounce on dj-input -->
<input dj-input="on_change" dj-debounce="0" />

<!-- Legacy data-* attributes (still supported) -->
<input dj-input="search" data-debounce="500" />
<input dj-input="on_resize" data-throttle="100" />

<!-- Select change -->
<select dj-change="filter_status">
    <option value="all">All</option>
    <option value="active">Active</option>
</select>

<!-- Two-way model binding -->
<input dj-model="username" type="text" />
```

### Keyboard

```html
<!-- Fire on Enter key -->
<input dj-keydown.enter="submit" />

<!-- Fire on Escape key -->
<input dj-keydown.escape="cancel" />

<!-- Fire on any keydown -->
<div dj-keydown="on_key" tabindex="0"></div>
```

Supported key modifiers: `.enter`, `.escape`, `.space`, `.tab`, `.backspace`, `.delete`, `.arrowup`/`.up` (and the same for down, left and right), or any single character (e.g. `.k`)

### Window & Document Events

| Attribute | Target | Event |
|---|---|---|
| `dj-window-keydown="handler"` | `window` | `keydown` |
| `dj-window-keyup="handler"` | `window` | `keyup` |
| `dj-window-scroll="handler"` | `window` | `scroll` (unthrottled — fires on every event; keep the handler cheap or rate-limit server-side) |
| `dj-window-click="handler"` | `window` | `click` |
| `dj-window-resize="handler"` | `window` | `resize` (unthrottled — fires on every event) |
| `dj-document-keydown="handler"` | `document` | `keydown` |
| `dj-document-keyup="handler"` | `document` | `keyup` |
| `dj-document-click="handler"` | `document` | `click` |

```html
<!-- Close modal on Escape anywhere -->
<div dj-window-keydown.escape="close_modal">

<!-- Track scroll position -->
<div dj-window-scroll="on_scroll">

<!-- Detect background clicks -->
<div dj-document-click="on_click">
```

Key modifier filtering works: `dj-window-keydown.escape="handler"`. The element provides context (`dj-value-*`, component ID) but the listener attaches to `window`/`document`.

### Click Away

```html
<!-- Fire event when user clicks outside this element -->
<div dj-click-away="close_dropdown" class="dropdown">
    ...
</div>
```

Uses capture-phase document listener (works even if inner elements call `stopPropagation()`). Supports `dj-confirm` and `dj-value-*`.

### Keyboard Shortcuts

```html
<!-- Single shortcut -->
<div dj-shortcut="escape:close_modal">

<!-- Multiple shortcuts, modifier keys -->
<div dj-shortcut="ctrl+k:open_search:prevent, escape:close_modal">

<!-- Modifiers: ctrl, alt, shift, meta (cmd on Mac) -->
<div dj-shortcut="ctrl+shift+s:save:prevent">
```

Syntax: `[modifier+...]key:handler[:prevent]` (comma-separated for multiple). The `prevent` suffix calls `preventDefault()`. Shortcuts skip form inputs by default; add `dj-shortcut-in-input` to override.

### Navigation

| Attribute | Description |
|---|---|
| `dj-patch="url"` | Update the URL (pushState) and send it to the current view's `handle_params` over the WebSocket — no page reload, same view |
| `dj-navigate="url"` | Client-side navigation (history push) |
| `dj-prefetch` | Prefetch link target on hover / touch — warms HTTP cache before click (v0.7.0) |

```html
<!-- Patch: replace reactive region only -->
<a dj-patch="{% url 'my_view' page=2 %}">Next page</a>

<!-- Navigate: full client-side navigation with history -->
<a dj-navigate="{% url 'dashboard' %}">Dashboard</a>

<!-- Prefetch on hover (65ms debounce) / touchstart (immediate) -->
<a dj-prefetch href="{% url 'dashboard' %}">Dashboard</a>

<!-- Opt out of prefetch on a specific link -->
<a dj-prefetch="false" href="/logout/">Log out</a>
```

See the [prefetch guide](prefetch.md) for same-origin / data-saver /
dedupe semantics.

### Polling

```html
<!-- Poll every 5 seconds (default) -->
<div dj-poll="refresh"></div>

<!-- Poll every 10 seconds -->
<div dj-poll="refresh" dj-poll-interval="10000"></div>
```

### Submit Protection

| Attribute | Description |
|---|---|
| `dj-disable-with="text"` | Disable button + replace text during submission |
| `dj-lock` | Block event until server responds (prevents double-fire) |

```html
<!-- Disable + replace text while submitting -->
<button type="submit" dj-disable-with="Saving...">Save</button>

<!-- Lock to prevent concurrent events -->
<button dj-click="save" dj-lock>Save</button>

<!-- Combined: lock + visual feedback -->
<button dj-click="save" dj-lock dj-disable-with="Saving...">Save</button>
```

### Lifecycle & Reconnection

| Attribute | Fires On | Handler Receives |
|---|---|---|
| `dj-mounted="handler"` | Element enters DOM (after VDOM patch) | `dj-value-*` attrs as kwargs |
| `dj-auto-recover="handler"` | WebSocket reconnects | Form values + `data-*` from container |
| `dj-no-recover` | — | Opts field out of automatic form recovery on reconnect |

```html
<!-- Fire event when element appears after a VDOM patch -->
<div dj-mounted="on_widget_ready" dj-value-widget-id="{{ widget.id }}">
    ...
</div>

<!-- Restore complex state after reconnection -->
<div dj-auto-recover="restore_state" dj-value-canvas-id="main">
    <input name="brush_size" value="5" />
</div>

<!-- Opt out of automatic form recovery -->
<input name="scratch" dj-change="on_change" dj-no-recover />
```

`dj-mounted` does not fire on initial page load — only after subsequent VDOM patches insert the element.

`dj-auto-recover` does not fire on initial page load — only after WebSocket reconnection. Serializes form field values and `data-*` attributes from the container.

`dj-no-recover` prevents a field from being auto-recovered on reconnect. Useful for ephemeral search fields or fields where server state is the source of truth. Fields inside `dj-auto-recover` containers are automatically skipped (custom handler takes precedence).

---

## UI Feedback Attributes

### Connection State CSS Classes

djust automatically applies CSS classes to `<body>` based on WebSocket/SSE connection state:

| Class | Applied when |
|---|---|
| `dj-connected` | WebSocket/SSE connection is open |
| `dj-disconnected` | WebSocket/SSE connection is lost |

Both classes are removed on intentional disconnect (e.g., TurboNav navigation). Use these for CSS-driven connection feedback:

```css
/* Dim content when disconnected */
body.dj-disconnected [dj-root] { opacity: 0.5; }

/* Show an offline banner */
.offline-banner { display: none; }
body.dj-disconnected .offline-banner { display: block; }
```

### `dj-cloak` (FOUC Prevention)

Hide elements until the WebSocket/SSE connection is established, preventing flash of unconnected content:

```html
<!-- Hidden until mount response is received -->
<div dj-cloak>
    <button dj-click="increment">+</button>
</div>
```

The CSS rule `[dj-cloak] { display: none !important; }` is injected automatically by client.js. The `dj-cloak` attribute is removed from all elements when the mount response arrives.

**Note:** If the WebSocket never connects, cloaked elements stay hidden. Only cloak elements that are WebSocket-dependent.

### `dj-scroll-into-view` (Auto-scroll on Render)

Automatically scroll an element into view after it appears in the DOM (via mount or VDOM patch):

```html
<!-- Smooth scroll (default) -->
<div dj-scroll-into-view>New message</div>

<!-- Instant scroll (no animation) -->
<div dj-scroll-into-view="instant">Alert</div>

<!-- Scroll to center of viewport -->
<div dj-scroll-into-view="center">Highlighted item</div>

<!-- Scroll to start or end -->
<div dj-scroll-into-view="start">Section header</div>
<div dj-scroll-into-view="end">Latest entry</div>
```

| Value | Behavior |
|---|---|
| `""` (default) | `{ behavior: 'smooth', block: 'nearest' }` |
| `"instant"` | `{ behavior: 'instant', block: 'nearest' }` |
| `"center"` | `{ behavior: 'smooth', block: 'center' }` |
| `"start"` | `{ behavior: 'smooth', block: 'start' }` |
| `"end"` | `{ behavior: 'smooth', block: 'end' }` |

One-shot per DOM node: each element scrolls only once. VDOM-replaced elements (fresh nodes) scroll again correctly.

### Page Loading Bar

An NProgress-style thin loading bar at the top of the page during TurboNav and `live_redirect` navigation. Always active by default -- no opt-in attribute needed.

Control programmatically:

```javascript
// Manual control
window.djust.pageLoading.start();
window.djust.pageLoading.finish();

// Disable entirely
window.djust.pageLoading.enabled = false;
```

Or hide via CSS:

```css
#djust-page-loading-bar { display: none !important; }
```

Navigation lifecycle events and CSS class for page transitions:

```css
/* CSS-only page transition (zero JS) */
[dj-root].djust-navigating main {
    opacity: 0.3;
    transition: opacity 0.15s ease;
    pointer-events: none;
}
```

```javascript
// JS hooks for advanced use cases
document.addEventListener('djust:navigate-start', () => showSkeleton());
document.addEventListener('djust:navigate-end', () => hideSkeleton());
```

---

## Loading States

Loading state directives apply CSS classes or show/hide elements while a server round-trip is in progress.

| Directive | Description |
|---|---|
| `dj-loading="event_name"` | Shorthand for `.for` + `.show`: the element is hidden until `event_name` is in flight |
| `dj-loading.for="event_name"` | Tie any element's loading modifiers to a named event (otherwise the element's own `dj-*` event is used) |
| `dj-loading.show` | Show element while loading (optionally `="flex"` etc.). Add `style="display: none"` so it starts hidden |
| `dj-loading.hide` | Hide element while loading |
| `dj-loading.disable` | Disable element while loading |
| `dj-loading.class="opacity-50"` | Add the class(es) in the value while loading |

The element that triggered the event also gets the `djust-loading` class automatically while
its request is in flight. An element with no `dj-*` event attribute needs `dj-loading.for`
(or the `dj-loading="event_name"` shorthand), or it is never registered.

```html
<!-- Button disables itself while request is in flight -->
<button dj-click="save" dj-loading.disable>Save</button>

<!-- Spinner appears only while "generate" is running -->
<button dj-click="generate">Generate</button>
<div dj-loading="generate">Loading...</div>

<!-- Loading overlay on a card -->
<div dj-loading.for="refresh" dj-loading.class="opacity-50">
    {{ content }}
</div>
```

---

## Passing Data to Handlers

### `data-*` attributes

```html
<!-- data-* values arrive as strings unless you add a type suffix -->
<button dj-click="select_item"
        data-item-id:int="{{ item.id }}"
        data-price:float="{{ item.price }}"
        data-active:bool="true">
    Select
</button>
```

Handler receives: `select_item(self, item_id=42, price=9.99, active=True)`

Type coercion rules:
- Without a suffix, a `data-*` value is passed as a `str` (`data-item-id="42"` → `item_id="42"`).
- Suffixes coerce on the client: `:int`, `:float`, `:bool`, `:json`, `:list`.
- Alternatively, annotate the handler (`def select_item(self, item_id: int = 0, price: float = 0.0, active: bool = False, **kwargs)`) and djust coerces by the type hints.

### `dj-value-*` attributes

```html
<!-- Pass extra values without data- prefix -->
<button dj-click="handler" dj-value-mode="edit" dj-value-row="{{ row.id }}">
    Edit
</button>
```

### `_target` (automatic)

For `dj-change` and `dj-input`, the `_target` parameter is included automatically with the triggering element's `name` attribute. Useful when multiple fields share one handler:

```html
<input name="email" dj-change="validate" />
<input name="username" dj-change="validate" />
```

Handler receives `_target="email"` or `_target="username"`.

---

## VDOM Identity

### Reactive Region

```html
<body>
    <div dj-root>
        <!-- Everything inside dj-root is managed by djust's VDOM -->
        <!-- Only this region is diffed and patched after events -->
    </div>
</body>
```

**Rule:** `dj-root` must contain all dynamic content. Static headers, navbars, and footers outside `dj-root` are never touched.

### Keyed Lists

```html
<!-- Without key: diffed by position (may produce extra DOM mutations) -->
{% for item in items %}
<div>{{ item.name }}</div>
{% endfor %}

<!-- With data-key: djust detects moves/inserts/removes optimally -->
{% for item in items %}
<div data-key="{{ item.id }}">{{ item.name }}</div>
{% endfor %}

<!-- With dj-key: same as data-key -->
{% for item in items %}
<li dj-key="{{ item.id }}">{{ item.name }}</li>
{% endfor %}
```

Use `data-key` or `dj-key` on list items whenever the list can reorder or items can be inserted/deleted. Analogous to React `key`.

### Opt Out of Patching

```html
<!-- External JS owns this subtree (charts, rich text editors, maps) -->
<div dj-update="ignore" id="my-chart"></div>
```

---

## JavaScript Hooks

```html
<div dj-hook="chart" id="my-chart"></div>
```

```javascript
window.djust.hooks.chart = {
    // Callbacks take no arguments; the element is this.el
    mounted()   { initChart(this.el); },
    updated()   { updateChart(this.el); },
    destroyed() { destroyChart(this.el); },
};
```

---

## Django Template Tags & Filters

### Supported Tags

| Tag | Notes |
|---|---|
| `{{ variable }}` | Variable output (auto-escaped) |
| `{% if %} / {% elif %} / {% else %} / {% endif %}` | Conditionals |
| `{% for %} / {% empty %} / {% endfor %}` | Loops |
| `{% url 'name' arg=val %}` | URL resolution |
| `{% include "partial.html" %}` | Template includes |
| `{% extends "base.html" %}` | Template inheritance |
| `{% block %} / {% endblock %}` | Block overrides |
| `{% load tag_library %}` | Load template tag library |
| `{% csrf_token %}` | CSRF token |
| `{% static 'file' %}` | Static file URL |
| `{% with var=value %}` | Local variable assignment |
| `{% dj_activity "name" visible=expr eager=expr %}...{% enddj_activity %}` | Pre-rendered hidden panel with preserved local state (React 19.2 parity). See [Activity guide](activity.md). |
| `{% djust_markdown expr [kwargs] %}` | Render Markdown to sanitised HTML in the Rust parser — raw HTML and `javascript:` URLs are neutralised; trailing-line provisional wrap makes streaming LLM output flicker-free. See [Streaming Markdown guide](streaming-markdown.md). |

### Callables are auto-called (Django parity)

Variable resolution calls callables with no arguments, exactly like
Django's engine (ADR-024):

```django
{{ user.get_full_name }}              {# calls get_full_name() #}
{{ workspace.memberships.count }}     {# calls the manager method #}
{{ obj.get_settings.theme }}          {# mid-path calls work too #}
```

Django's safety attributes are honored: a callable with
`do_not_call_in_templates = True` is used as-is (Model classes, `Choices`
enums), and one with `alters_data = True` is **never called** — the
expression renders empty (so `{{ user.delete }}` cannot destroy data).
Set `alters_data = True` on your own mutating model methods, as in Django.

**LiveView performance note**: djust re-renders on every WebSocket
event, so `{{ qs.count }}` in a template is a DB query *per event* —
not once per request like classic Django. For hot views, precompute in
`get_context_data()` (djust warns once per path in `DEBUG` when a
template auto-calls an ORM method). Kill-switch:
`LIVEVIEW_CONFIG["template_auto_call"] = False` restores the old
no-call behavior.

### Objects resolve like Django (ADR-027)

Since djust 1.2.0 a dotted lookup on an ordinary Python object resolves
against the **live object**, one segment at a time, exactly as Django's
`Variable._resolve_lookup` does. Before that, djust converted the whole
object up front and walked the conversion, which is where a family of
divergences lived.

Four things follow, all of them Django's answers:

```django
{{ presenter }}          {# str(presenter), not a dict of its attributes #}
{{ SomeClass }}          {# the class is INSTANTIATED, as Django does #}
{{ obj.method }}         {# called; do_not_call_in_templates / alters_data honoured #}
{% for r in rows|slice:":3" %}{{ r.label }}{% endfor %}   {# reaches attributes #}
```

The third one is the same rule the section above describes — the
difference is only that it is now applied by the segment walk rather
than by a conversion that ran before it, so it holds at every segment
and for values bound by `{% for %}` and `{% with %}`.

**What to check when upgrading.**

1. **A class placed in a context is now instantiated — its `__init__`
   runs.** This is the headline. `{"MyForm": MyForm}` in a context is a
   common spelling, and a class that previously rendered as an inert
   repr now runs its constructor on every render. Django has always done
   this; djust did not. If a class in one of your contexts has a
   constructor with side effects or required arguments, either pass an
   instance or set `do_not_call_in_templates = True` on the class.
2. **`{{ obj }}` now renders `str(obj)`.** A template that relied on an
   object rendering its attribute mapping will show its `__str__`
   instead. Give such a class a `__str__`, or spell the attribute you
   meant.
3. **`{{ obj|json_script }}` changes shape for the same reason — and the
   direction depends on the object, so audit rather than assume.** It
   now emits `str(obj)` rather than a JSON object built from every
   public instance attribute. The old dump filtered underscore-prefixed
   attributes and `str(obj)` filters nothing, so an object with no
   `__str__` discloses *less* (`<Foo object at 0x…>`) while a
   `@dataclass` — whose generated repr prints every field, `_private`
   included — or any object whose `__str__` names private state
   discloses *more*. Check what the `__str__` of anything you place bare
   in a template, or pass to `json_script`, actually says.

Django **models** are unaffected by all of this: they stay on djust's
eager, floored path, and the serialization floor keeps
`{{ user.password }}` empty.

**There is no kill-switch.** The release candidates carried
`LIVEVIEW_CONFIG["template_resolve_lazy"] = False` as a rollback to the
pre-1.2.0 behaviour; the setting and the machinery it kept alive were
removed before 1.2.0 final (ADR-027 Step 5, #2628). Setting the key now
does nothing — fix the template instead.

### Comparison operators inside `{% if %}`

The Rust template engine accepts the full set of Python comparison
operators inside `{% if %}` and `{% elif %}` conditions — not just
`==` / `!=`:

```django
{% if cart.total > 100 %}
  <span class="badge">free shipping</span>
{% endif %}

{% if user.age >= 18 and user.age < 65 %}…{% endif %}
{% if rating <= 2 %}{% elif rating < 5 %}{% else %}{% endif %}
```

`>`, `<`, `>=`, `<=`, `==`, `!=`, `in`, `not in` — all work as you'd
expect. Combine with `and` / `or` / `not`. (Available since v0.1.6.)

### `{{ model.pk }}` for Django model context

Pass a Django model instance into the template context and you can
read its primary key directly:

```python
class ArticleView(LiveView):
    article = state(default=None)

    def mount(self, request, slug):
        self.article = Article.objects.get(slug=slug)
```

```django
<a href="{% url 'article-edit' pk=article.pk %}">Edit</a>
```

The Rust serializer auto-includes a `pk` key on every model instance
regardless of the field name (`id`, `uuid`, custom). You can still
read the underlying field by its real name (`article.id`,
`article.uuid`) — `pk` is just the cross-model alias.

### Custom Tag Handlers (`register_tag_handler` / `register_block_tag_handler` / `register_assign_tag_handler`)

Three registration entrypoints let you wire Python callbacks into the
Rust template engine without forking the parser:

| Variety | Returns | Use when |
|---|---|---|
| `register_tag_handler(name, handler)` | String (escaped unless `mark_safe`) | The tag emits content (`{% url %}`, `{% static %}`) |
| `register_block_tag_handler(name, end_tag, handler)` | HTML wrapping the inner block; handler is `.render(args, content, context)` | The tag wraps content (`{% upper %}…{% endupper %}`) |
| `register_assign_tag_handler(name, handler)` | `dict[str, Any]` merged into the context | The tag mutates the context for sibling nodes (`{% assign x=expr %}`) |

Each handler is an object with a `render` method, not a bare function. `args` is a
list of the tag's arguments, already resolved against the context. A plain `str`
return is HTML-escaped, so wrap markup you've made safe in `mark_safe`:

```python
from django.utils.html import escape
from django.utils.safestring import mark_safe
from djust._rust import register_tag_handler

class HelloTag:
    def render(self, args, context):
        name = args[0] if args else "world"
        return mark_safe(f"<p>Hello, {escape(name)}!</p>")

register_tag_handler("hello", HelloTag())
```

```django
{% hello "Alice" %}
```

Overhead is ~100–500 ns per call (PyO3 boundary). Built-in tags
(`if`, `for`, `block`, …) stay in pure Rust with zero overhead. See
ADR-005 in the djust repo for the architecture rationale.

### Auto-serialization for Django types

Django types pass through the Rust template engine without manual
`.isoformat()` / `.hex` conversion:

| Django type | Renders as |
|---|---|
| `datetime.datetime` / `datetime.date` / `datetime.time` | Rendered as Django does (localized `DATETIME_FORMAT` / `DATE_FORMAT` / `TIME_FORMAT`); use `\|date:"Y-m-d H:i"` / `\|time` for explicit formats |
| `decimal.Decimal` | string (preserves precision; pair with `\|floatformat`) |
| `uuid.UUID` | string |
| `FieldFile` (FileField / ImageField) | object — call `.url`, `.name`, `.size` directly |

Pass them via `context` / `self.*`; the serializer handles the rest.

### Filters (all 57 Django built-ins)

> **Filter arguments are Django's, and a wrong one is an error.** Quoted is a
> literal (`|default:"x"`); bare is a context variable (`|default:x`) and raises
> if it does not resolve — so a typo fails loudly rather than rendering the typo.
> Numeric arguments are `int(arg)`, so `" 5 "`, `"+5"`, `"1_0"` and `True` all
> work, an unquoted `2.7` truncates to `2`, and a quoted `"2.7"` raises. An
> unparseable argument raises for `center`, `ljust`, `rjust`, `wordwrap`,
> `urlizetrunc` and `divisibleby`, and returns the value unchanged for the
> `truncate*` family, `get_digit` and `floatformat` — matching each filter's
> Django source. The **value** follows the same rule where a filter coerces it:
> `divisibleby`, `get_digit`, `add` and `filesizeformat` call `int(value)`, and
> each raises for exactly the exceptions its own Django `except` misses — so
> `{{ "abc"|divisibleby:"2" }}` refuses the template as Django does, while
> `{{ "abc"|get_digit:"1" }}` renders `abc`. Full rules:
> `docs/RUST_TEMPLATE_API.md`, "Filter arguments" and "Filter values".

**String**

| Filter | Example |
|---|---|
| `upper` | `{{ name\|upper }}` → `"ALICE"` |
| `lower` | `{{ name\|lower }}` |
| `title` | `{{ name\|title }}` |
| `capfirst` | `{{ text\|capfirst }}` |
| `truncatechars:N` | `{{ text\|truncatechars:50 }}` |
| `truncatewords:N` | `{{ text\|truncatewords:20 }}` |
| `wordcount` | `{{ text\|wordcount }}` |
| `slugify` | `{{ title\|slugify }}` |
| `urlencode` | `?q={{ query\|urlencode }}` |
| `linebreaks` | `{{ bio\|linebreaks }}` |
| `linebreaksbr` | `{{ bio\|linebreaksbr }}` |
| `urlize` | `{{ text\|urlize }}` — no `\|safe` needed (handles own escaping) |

**Number**

| Filter | Example |
|---|---|
| `floatformat:N` | `{{ price\|floatformat:2 }}` → `"9.99"` |
| `filesizeformat` | `{{ bytes\|filesizeformat }}` → `"1.2 MB"` |
| `pluralize` | `{{ count }} item{{ count\|pluralize }}` |

`intcomma` (`{{ count|intcomma }}` → `"1,234"`) is not a built-in: it comes from
`django.contrib.humanize`. Add that app to `INSTALLED_APPS` and `{% load humanize %}`.

**Date/Time**

| Filter | Example |
|---|---|
| `date:"Y-m-d"` | `{{ created\|date:"Y-m-d" }}` |
| `time:"H:i"` | `{{ ts\|time:"H:i" }}` |
| `timesince` | `{{ created\|timesince }}` → `"3 days"` (write `{{ created\|timesince }} ago`) |
| `timeuntil` | `{{ expires\|timeuntil }}` |

**List/Dict**

| Filter | Example |
|---|---|
| `length` | `{{ items\|length }}` |
| `first` | `{{ items\|first }}` |
| `last` | `{{ items\|last }}` |
| `join:", "` | `{{ tags\|join:", " }}` |
| `dictsort:"key"` | `{{ items\|dictsort:"name" }}` |
| `slice:":3"` | `{{ items\|slice:":3" }}` |

**Logic**

| Filter | Example |
|---|---|
| `default:"fallback"` | `{{ value\|default:"—" }}` |
| `default_if_none:"N/A"` | `{{ value\|default_if_none:"N/A" }}` |
| `yesno:"yes,no,maybe"` | `{{ flag\|yesno:"enabled,disabled" }}` |

**Escaping**

| Filter | Example | Notes |
|---|---|---|
| `safe` | `{{ html\|safe }}` | Mark pre-escaped HTML safe |
| `escape` | `{{ text\|escape }}` | Force HTML escaping |
| `force_escape` | `{{ text\|force_escape }}` | Escape even in `{% autoescape off %}` |
| `striptags` | `{{ html\|striptags }}` | Remove all HTML tags |

---

## Common Pitfalls

### Form field values during VDOM patch

djust's VDOM preserves text input values during patches by default. However, if the server re-renders a field with a different `value=` attribute, the new server value wins. To preserve a field that the user is actively editing, use `dj-update="ignore"` on its container:

```html
<div dj-update="ignore">
    <input type="text" name="draft" />
</div>
```

### `|safe` after HTML-producing filters

`urlize`, `urlizetrunc`, and `unordered_list` are in djust's `safe_output_filters` whitelist — the Rust engine automatically marks their output as safe without requiring `|safe`. Adding `|safe` after them is redundant but harmless (it does not double-escape):

```html
<!-- Enough: djust's Rust engine auto-marks urlize output as safe -->
{{ text|urlize }}

<!-- Redundant, same output -->
{{ text|urlize|safe }}
```

*Note:* Standard Django achieves this via `SafeData` type-checking. djust implements it as an explicit whitelist, so users coming from Django don't need `|safe` with these filters.

---

## Quick Reference Card

```
Event attributes:
  dj-click        dj-submit       dj-change       dj-input
  dj-blur         dj-focus        dj-keydown      dj-keyup
  dj-poll         dj-patch        dj-navigate     dj-copy
  dj-confirm      dj-model        dj-mounted      dj-auto-recover
  dj-click-away   dj-shortcut     dj-no-recover
  dj-mouseenter   dj-mouseleave   (non-bubbling mouse enter/leave)

Window/document scoping:
  dj-window-keydown               (keydown on window)
  dj-window-keyup                 (keyup on window)
  dj-window-scroll                (scroll on window, unthrottled)
  dj-window-click                 (click on window)
  dj-window-resize                (resize on window, unthrottled)
  dj-document-keydown             (keydown on document)
  dj-document-keyup               (keyup on document)
  dj-document-click               (click on document)

Rate limiting (HTML attributes):
  dj-debounce="300"               (debounce ms, per element)
  dj-debounce="blur"              (defer until blur)
  dj-debounce="0"                 (disable default debounce)
  dj-throttle="500"               (throttle ms, per element)

Copy enhancements:
  dj-copy="#selector"             (copy element textContent)
  dj-copy-feedback="Done!"        (custom feedback text, 1.5s)
  dj-copy-class="btn-success"     (custom CSS class, 2s)
  dj-copy-event="handler"         (server event after copy)

Submit protection:
  dj-disable-with="text"          (disable + replace text during submit)
  dj-lock                         (block event until server responds)

Loading directives:
  dj-loading="event"              (hidden; shown while event runs)
  dj-loading.for="event"          (tie modifiers to a named event)
  dj-loading.class="foo"          (add class foo)
  dj-loading.hide                 (hide while loading)
  dj-loading.show                 (show while loading; start hidden)
  dj-loading.disable              (disable while loading)
  .djust-loading                  (auto class on the triggering element)

UI feedback:
  dj-cloak                        (hide until WS/SSE mount completes)
  dj-scroll-into-view             (auto-scroll on render, smooth default)
  dj-scroll-into-view="instant"   (auto-scroll, no animation)
  dj-scroll-into-view="center"    (auto-scroll to viewport center)

Connection state (auto on <body>):
  .dj-connected                   (body class when connected)
  .dj-disconnected                (body class when disconnected)

Reconnection UI (auto on <body>):
  data-dj-reconnect-attempt       (current attempt number)
  --dj-reconnect-attempt          (CSS custom property, attempt number)
  .dj-reconnecting-banner         (auto-shown banner with attempt count)

Page loading bar:
  Always active for TurboNav / live_redirect
  window.djust.pageLoading.start/finish  (manual control)
  .djust-navigating             (on [dj-root] during navigation)
  djust:navigate-start          (CustomEvent on document)
  djust:navigate-end            (CustomEvent on document)

Document metadata (Python-side, no template directive):
  self.page_title = "..."              (update document.title)
  self.page_meta = {"key": "value"}    (update/create <meta> tags)

VDOM identity:
  dj-view="myapp.views.CounterView"      (auto-injected onto <div dj-root>)
  dj-root                         (reactive region — required)
  data-key / dj-key               (stable list identity)
  dj-update="ignore"              (opt out of patching)
  dj-hook="name"                  (JS lifecycle hooks)

Data passing:
  data-*                          (string kwargs; data-x:int etc. to coerce)
  dj-value-*                      (extra value kwargs)
  dj-target="#selector"           (scoped DOM updates)
```

## Gotchas

### Don't put `{%` or `%}` inside `{# … #}` comments

djust's Rust template engine handles this correctly — it treats `{# … #}` as opaque. **Django's stock template parser does not.** When a template flows through Django (e.g., the non-LiveView HTTP path, or any third-party tool that re-parses your templates), a comment that contains a partial tag string will trip Django's tokenizer:

```django
{# d-none (not {% if %}) so the VDOM ... #}        <!-- ❌ Django will choke -->
{# d-none keeps the DOM stable so the VDOM ... #}  <!-- ✅ both engines OK -->
```

The Django error is `TemplateSyntaxError: Unexpected end of expression in if tag` from `django/template/smartif.py`. Workaround: rewrite the comment without `{%` / `%}`. (Reference: [#1423](https://github.com/djust-org/djust/issues/1423).)
