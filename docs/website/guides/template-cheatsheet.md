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
{% load djust_tags %}
<!DOCTYPE html>
<html>
<head>
    {% djust_scripts %}   {# Loads ~5KB client JavaScript #}
</head>
<body dj-view="{{ dj_view_id }}">   {# Binds page to WebSocket session #}
    <div dj-root>                    {# Reactive region — only this is diffed/patched #}
        {{ count }}
        <button dj-click="increment">+</button>
    </div>
</body>
</html>
```

| Attribute / Tag | Required | Description |
|---|---|---|
| `{% load djust_tags %}` | Yes | Load djust template tag library |
| `{% djust_scripts %}` | Yes | Injects client JavaScript (~5KB) |
| `dj-view="{{ dj_view_id }}"` | Yes | On `<body>` — identifies the WebSocket session |
| `dj-root` | Yes | Marks the reactive subtree — only HTML inside is diffed |

---

## Event Directives

### Click & Submit

| Attribute | Fires On | Handler Receives |
|---|---|---|
| `dj-click="handler"` | Click | `data-*` attributes as kwargs |
| `dj-submit="handler"` | Form submit | All named form fields as kwargs |
| `dj-copy="text"` | Click | Client-only clipboard copy, no server round-trip |

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

<!-- Client-side clipboard copy -->
<button dj-copy="{{ share_url }}">Copy link</button>
```

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

<!-- Debounce override (default: 300ms for text inputs) -->
<input dj-input="search" data-debounce="500" />

<!-- Throttle instead of debounce -->
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

Supported key modifiers: `.enter`, `.escape`, `.space`

### Window & Document Events

| Attribute | Target | Event |
|---|---|---|
| `dj-window-keydown="handler"` | `window` | `keydown` |
| `dj-window-keyup="handler"` | `window` | `keyup` |
| `dj-window-scroll="handler"` | `window` | `scroll` (150ms throttle) |
| `dj-window-click="handler"` | `window` | `click` |
| `dj-window-resize="handler"` | `window` | `resize` (150ms throttle) |
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
| `dj-patch="url"` | Replace `dj-root` content via AJAX (no full reload) |
| `dj-navigate="url"` | Client-side navigation (history push) |

```html
<!-- Patch: replace reactive region only -->
<a dj-patch="{% url 'my_view' page=2 %}">Next page</a>

<!-- Navigate: full client-side navigation with history -->
<a dj-navigate="{% url 'dashboard' %}">Dashboard</a>
```

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

### Lifecycle

| Attribute | Fires On | Handler Receives |
|---|---|---|
| `dj-mounted="handler"` | Element enters DOM (after VDOM patch) | `dj-value-*` attrs as kwargs |

```html
<!-- Fire event when element appears after a VDOM patch -->
<div dj-mounted="on_widget_ready" dj-value-widget-id="{{ widget.id }}">
    ...
</div>
```

Does not fire on initial page load — only after subsequent VDOM patches insert the element.

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
body.dj-disconnected dj-root { opacity: 0.5; }

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
.djust-page-loading-bar { display: none !important; }
```

---

## Loading States

Loading state directives apply CSS classes or show/hide elements while a server round-trip is in progress.

| Directive | Description |
|---|---|
| `dj-loading` | Toggle `djust-loading` class on the element itself |
| `dj-loading.class:foo` | Add class `foo` while loading |
| `dj-loading.hide` | Hide element while loading |
| `dj-loading.show` | Show element only while loading (spinner pattern) |
| `dj-loading.disable` | Disable element while loading |
| `dj-loading.target=#id` | Apply loading state to `#id` instead of current element |

```html
<!-- Button disables itself while request is in flight -->
<button dj-click="save" dj-loading.disable>Save</button>

<!-- Spinner appears only during loading -->
<button dj-click="generate">Generate</button>
<div dj-loading.show.target=#gen-btn id="spinner">Loading...</div>

<!-- Loading overlay on a card -->
<div dj-loading.class:opacity-50>
    {{ content }}
</div>
```

---

## Passing Data to Handlers

### `data-*` attributes

```html
<!-- data-* attributes are coerced to their natural type -->
<button dj-click="select_item"
        data-item-id="{{ item.id }}"
        data-price="{{ item.price }}"
        data-active="true">
    Select
</button>
```

Handler receives: `select_item(self, item_id=42, price=9.99, active=True)`

Type coercion rules:
- `"true"` / `"false"` → `bool`
- Numeric strings → `int` or `float`
- Everything else → `str`

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
<body dj-view="{{ dj_view_id }}">
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
djust.hooks.chart = {
    mounted(el)   { initChart(el); },
    updated(el)   { updateChart(el); },
    destroyed(el) { destroyChart(el); },
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

### Filters (all 57 Django built-ins)

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
| `urlize` | `{{ text\|urlize }}` — do **not** add `\|safe` (handles own escaping) |

**Number**

| Filter | Example |
|---|---|
| `floatformat:N` | `{{ price\|floatformat:2 }}` → `"9.99"` |
| `intcomma` | `{{ count\|intcomma }}` → `"1,234"` |
| `filesizeformat` | `{{ bytes\|filesizeformat }}` → `"1.2 MB"` |
| `pluralize` | `{{ count }} item{{ count\|pluralize }}` |

**Date/Time**

| Filter | Example |
|---|---|
| `date:"Y-m-d"` | `{{ created\|date:"Y-m-d" }}` |
| `time:"H:i"` | `{{ ts\|time:"H:i" }}` |
| `timesince` | `{{ created\|timesince }}` → `"3 days ago"` |
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

### One-sided `{% if %}` in class attributes

**Problem:** Using `{% if %}` without `{% else %}` inside an HTML attribute can confuse djust's branch-aware div-depth counter, causing VDOM patching misalignment.

```html
<!-- WRONG: one-sided if inside class attribute -->
<div class="card {% if active %}active{% endif %}">
```

**Fix:** Use a separate attribute or a full `{% if/else %}` expression:

```html
<!-- CORRECT: full if/else -->
<div class="card {% if active %}active{% else %}{% endif %}">

<!-- ALSO CORRECT: move the conditional outside -->
{% if active %}
<div class="card active">
{% else %}
<div class="card">
{% endif %}
    ...
</div>
```

This limitation applies specifically to class and other attribute values — `{% if %}` blocks in element content work fine.

### Form field values during VDOM patch

djust's VDOM preserves text input values during patches by default. However, if the server re-renders a field with a different `value=` attribute, the new server value wins. To preserve a field that the user is actively editing, use `dj-update="ignore"` on its container:

```html
<div dj-update="ignore">
    <input type="text" name="draft" />
</div>
```

### Double-escaping HTML filters

`urlize`, `urlizetrunc`, and `unordered_list` are in djust's `safe_output_filters` whitelist — the Rust engine automatically marks their output as safe without requiring `|safe`. **Do not** pipe them through `|safe` or you'll double-escape:

```html
<!-- WRONG: double-escapes the output -->
{{ text|urlize|safe }}

<!-- CORRECT: djust's Rust engine auto-marks urlize output as safe -->
{{ text|urlize }}
```

*Note:* Standard Django achieves this via `SafeData` type-checking. djust implements it as an explicit whitelist, so users coming from Django don't need `|safe` with these filters.

### `{% elif %}` in inline templates

`{% elif %}` is not supported in `template_string` / `template =` inline templates. Use separate `{% if %}` blocks:

```html
<!-- WRONG in inline templates -->
{% if a %}...{% elif b %}...{% endif %}

<!-- CORRECT -->
{% if a %}...{% endif %}
{% if not a and b %}...{% endif %}
```

---

## Quick Reference Card

```
Event attributes:
  dj-click        dj-submit       dj-change       dj-input
  dj-blur         dj-focus        dj-keydown      dj-keyup
  dj-poll         dj-patch        dj-navigate     dj-copy
  dj-confirm      dj-model        dj-mounted
  dj-click-away   dj-shortcut

Window/document scoping:
  dj-window-keydown               (keydown on window)
  dj-window-keyup                 (keyup on window)
  dj-window-scroll                (scroll on window, 150ms throttle)
  dj-window-click                 (click on window)
  dj-window-resize                (resize on window, 150ms throttle)
  dj-document-keydown             (keydown on document)
  dj-document-keyup               (keyup on document)
  dj-document-click               (click on document)

Submit protection:
  dj-disable-with="text"          (disable + replace text during submit)
  dj-lock                         (block event until server responds)

Loading directives:
  dj-loading                      (toggle djust-loading class)
  dj-loading.class:foo            (add class foo)
  dj-loading.hide                 (hide while loading)
  dj-loading.show                 (show only while loading)
  dj-loading.disable              (disable while loading)
  dj-loading.target=#id           (apply to target element)

UI feedback:
  dj-cloak                        (hide until WS/SSE mount completes)
  dj-scroll-into-view             (auto-scroll on render, smooth default)
  dj-scroll-into-view="instant"   (auto-scroll, no animation)
  dj-scroll-into-view="center"    (auto-scroll to viewport center)

Connection state (auto on <body>):
  .dj-connected                   (body class when connected)
  .dj-disconnected                (body class when disconnected)

Page loading bar:
  Always active for TurboNav / live_redirect
  window.djust.pageLoading.start/finish  (manual control)

VDOM identity:
  dj-view="{{ dj_view_id }}"      (on body — required)
  dj-root                         (reactive region — required)
  data-key / dj-key               (stable list identity)
  dj-update="ignore"              (opt out of patching)
  dj-hook="name"                  (JS lifecycle hooks)

Data passing:
  data-*                          (typed kwargs to handlers)
  dj-value-*                      (extra value kwargs)
  dj-target="#selector"           (scoped DOM updates)
```
