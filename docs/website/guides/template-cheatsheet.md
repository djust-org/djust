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

`urlize`, `urlizetrunc`, and `unordered_list` handle their own HTML escaping. **Do not** pipe them through `|safe`:

```html
<!-- WRONG: double-escapes the output -->
{{ text|urlize|safe }}

<!-- CORRECT: urlize already produces safe HTML -->
{{ text|urlize }}
```

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
  dj-confirm      dj-model

Loading directives:
  dj-loading                      (toggle djust-loading class)
  dj-loading.class:foo            (add class foo)
  dj-loading.hide                 (hide while loading)
  dj-loading.show                 (show only while loading)
  dj-loading.disable              (disable while loading)
  dj-loading.target=#id           (apply to target element)

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
