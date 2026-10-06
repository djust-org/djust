---
title: "Template Requirements"
slug: template-requirements
section: guides
order: 16
level: beginner
description: "What djust needs from your templates — dj-root, dj-view, and the attributes djust reads."
---

# Template Requirements

Every djust LiveView template needs the `dj-view` attribute to identify the LiveView class. The VDOM root is automatically inferred from the `dj-view` element in most cases.

---

## Required Attributes

### Simple Case (99% of templates)

Just add `dj-view` to your root element:

```html
<div dj-view="myapp.views.CounterView">
    <p>Count: {{ count }}</p>
    <button dj-click="increment">+1</button>
</div>
```

The element with `dj-view` automatically becomes the VDOM root. No additional attributes needed.

### Explicit `dj-root`

If you add `dj-root` yourself, put it on the **same element** as `dj-view`:

```html
<div dj-root dj-view="myapp.views.CounterView">
    <p>Count: {{ count }}</p>
    <button dj-click="increment">+1</button>
</div>
```

A template with `dj-root` and no `dj-view` works too: on the initial page load djust adds `dj-view="<module>.<ClassName>"` to the `dj-root` element, whatever other attributes it has (`<div dj-root class="search">` included).

Don't put `dj-root` on a separate inner element to keep a wrapper out of VDOM diffing. The client stamps `dj-root` onto every `dj-view` element, while the server diffs from the inner `dj-root`, so the two sides disagree about the root; `djust.T005` warns about this layout. To keep static chrome out of updates, move it outside the `dj-view` element, or mark it `dj-update="ignore"`.

| Attribute | Required | Purpose |
|-----------|----------|---------|
| `dj-view` | ✅ Yes | Identifies the LiveView class for the WebSocket connection |
| `dj-root` | ❌ Optional | Explicitly marks VDOM root (auto-inferred from `dj-view` if omitted) |

### Which element?

The root can be any element inside `<body>`: a `<div>`, or the semantically better `<main>`, `<section>` or `<article>`. It can also be `<body>` itself: `<body dj-root>` (or `<body dj-view="myapp.views.Page">`) makes the page's top-level elements, say a `<header>`, a `<main>` and a `<footer>`, the view's content, with no wrapper element. It can't be `<html>` or `<head>` (`djust.T025`), or a table-section element such as `<tbody>` (`djust.T017`). With the root on `<html>` the HTTP render is complete, but the WebSocket mount cannot use it, so the live page loses its content. `manage.py check` warns about it, and djust logs a warning when a page declares its root there, because the first render can't be matched to the live updates that follow.

When a page has more than one candidate, one rule picks the root, in the Python render and in the Rust VDOM alike. First, elements that belong to another view are set aside: an embedded `{% live_render %}` child's wrapper and everything inside it, and a `dj-lazy` view's container. Then the first `dj-root` in document order, otherwise the first `dj-view`. Only when nothing inside `<body>` declares either does `<body>` itself count, if it carries `dj-root` or `dj-view`; `<html>` and `<head>` never do. So a page that already has a root keeps it, a `<body dj-view>` still yields to a `dj-root` on an element inside it (the layout `djust.T005` warns about), and a `dj-root` in a child's template never takes over its parent's page. Markup that only looks like a root (inside an attribute value, a comment, a `<script>` or `<style>` body) is text and is ignored.

#### A `<body>` root

With the root on `<body>`, the view owns the elements djust rendered for it and leaves the other children of `<body>` alone: djust's own scripts and debug panel, a dev toolbar, the nodes a browser extension adds. The page's own scripts run once, when the page loads. A few rules follow from sharing `<body>`:

- Put `dj-root` on `<body>` and djust adds the view path (`dj-view="..."`) for you, as it does for any `dj-root`. A valueless `<body dj-view>` has no view path and does not connect, as on any other element.
- The body is the root only while nothing inside it declares one: a `dj-root` (or `dj-view`) on an element inside `<body>` is the root, as it always was.
- A root can sit in a base template (`{% extends %}`): the `<body dj-root>` in the base, the content in the child's blocks.
- The server updates `<body>`'s own attributes only when their rendered value changes. A class your own JavaScript toggles (a theme switcher, say) survives updates as long as the template does not render a different `class` on `<body>`.
- A node another script puts at the very start of `<body>` is only recognized after the page has mounted over a WebSocket or SSE. On an HTTP-only page (no live connection) updates are applied by position, so keep such nodes at the end of `<body>`.
- `dj-lazy` views work inside the page: a lazy container belongs to its own view, so it does not take the root from `<body>`, and the client no longer drops the address it gave the container when it morphs the page. The guide on [multiple views](multiple-views.md) still recommends placing them beside the root where you can.

If you had `<body dj-view="...">` before: the page connected, then replaced `<body>` with its first element and stopped working. It now works as written; nothing in the template needs to change. If you worked around it with a wrapper (`<div dj-root>` around the content), that still works exactly as before.

---

## What Each Attribute Does

### `dj-view` (Required)

This attribute tells djust's client JavaScript which LiveView class to connect to over WebSocket.

```html
<div dj-view="myapp.views.CounterView">
```

When the page loads, the client JS finds this attribute, reads the view class path, and opens a WebSocket connection to mount that view. Without it, no WebSocket connection is established and the page behaves like a static HTML page.

The value must be the **full Python import path** to the LiveView class (e.g., `myapp.views.CounterView`).

**Important**: The element with `dj-view` is the VDOM root. If you add `dj-root`, put it on the same element (`<div dj-root dj-view="...">`); `djust.T005` warns when they are split.

### `dj-root` (Optional)

This attribute explicitly marks the root element of the VDOM tree. When an event handler updates state and triggers a re-render, djust:

1. Renders the template on the server with the new state
2. Diffs the new HTML against the old HTML using the Rust VDOM engine
3. Sends only the changed patches to the client
4. Applies the patches to the DOM

**You rarely need this attribute.** It is inferred from `dj-view`, and when you do write it, it belongs on the `dj-view` element itself. It is not a way to exclude wrapper elements from diffing: to keep static content out of updates, move it outside the `dj-view` element or use `dj-update="ignore"`.

---

## Common Patterns

### Pattern 1: Simple View (Recommended)

```html
<div dj-view="myapp.views.CounterView">
    <p>Count: {{ count }}</p>
    <button dj-click="increment">+1</button>
</div>
```

**When to use**: Almost always. The entire element updates on state changes.

### Pattern 2: Static Section Inside the View

```html
<div dj-view="myapp.views.DashboardView">
    <nav class="sidebar" dj-update="ignore">
        <!-- Rendered once, then left alone by patches -->
        <ul>...</ul>
    </nav>

    <h1>{{ title }}</h1>
    <p>{{ content }}</p>
</div>
```

**When to use**: when a large section inside the view never changes after the first render. Content that doesn't depend on view state at all can live outside the `dj-view` element instead (for example in `base.html`).

---

## Template Inheritance

When using Django template inheritance, the attributes must be on the **innermost root element** of your LiveView content, not in the base template.

### Base template (`base.html`)

The base template provides the overall page structure. It does **not** need djust attributes:

```html
<!DOCTYPE html>
<html>
<head>
    <title>{% block title %}My App{% endblock %}</title>
</head>
<body>
    <nav><!-- navigation --></nav>
    <main>
        {% block content %}{% endblock %}
    </main>
</body>
</html>
```

### LiveView template (`counter.html`)

The LiveView template extends the base and puts `dj-view` on its root element inside the `content` block:

```html
{% extends "base.html" %}

{% block content %}
<div dj-view="myapp.views.CounterView">
    <h1>Counter</h1>
    <p>Count: {{ count }}</p>
    <button dj-click="increment">+1</button>
</div>
{% endblock %}
```

### Important: State outside the VDOM root

Only content **inside** the VDOM root is tracked by the VDOM. If your event handler modifies state that is rendered in `base.html` (outside the root), the VDOM diff will not detect the change.

**Wrong** -- state rendered outside the root:

```html
<!-- base.html -->
<body>
    <!-- This is OUTSIDE dj-view -- VDOM cannot update it -->
    <nav>
        {% if show_admin_menu %}
            <a href="/admin">Admin</a>
        {% endif %}
    </nav>
    <main>{% block content %}{% endblock %}</main>
</body>
```

If an event handler sets `self.show_admin_menu = True`, the nav will not update because it is outside the VDOM root.

**Fix**: Move the conditional into the LiveView template (inside the root), or use `push_event` to handle UI changes outside the root on the client side.

---

## Validation

Run djust's system checks to catch template issues before they reach production:

```bash
python manage.py check --tag djust
```

Relevant checks:

| Check ID | What It Detects |
|----------|-----------------|
| `djust.T001` | Deprecated `@click` syntax (should be `dj-click`) |
| `djust.T002` | (Info) Template has `dj-view` but no `dj-root`. Harmless: `dj-root` is inferred from `dj-view`. Without either attribute the page never connects, which is T012 |
| `djust.T003` | Wrapper template using `{% include %}` instead of `{{ liveview_content\|safe }}` |
| `djust.T004` | `document.addEventListener` for djust events (should be `window`) |
| `djust.T005` | `dj-view` and `dj-root` on different elements (must be on same element) |
| `djust.T010` | `dj-click` used for navigation (should use `dj-patch` for URL updates) |

---

## Examples

### Minimal LiveView Template

```html
<div dj-view="myapp.views.HelloView">
    <p>Hello, {{ name }}!</p>
    <input dj-model="name" type="text" placeholder="Enter your name">
</div>
```

### With Template Inheritance

```html
{% extends "base.html" %}

{% block content %}
<div dj-view="myapp.views.TodoView">
    <h2>Todo List</h2>
    <ul>
        {% for item in items %}
        <li data-key="{{ item.id }}">
            <span>{{ item.text }}</span>
            <button dj-click="delete_item" data-dj-item-id="{{ item.id }}">
                Delete
            </button>
        </li>
        {% endfor %}
    </ul>
    <form dj-submit="add_item">
        <input dj-model="new_item_text" type="text">
        <button type="submit">Add</button>
    </form>
</div>
{% endblock %}
```

### Multiple LiveViews on One Page

The client mounts only **one** page-level `dj-view` element (the first one it finds), so two sibling `dj-view` divs do not give you two live views; the second is never mounted. Render one page-level view and embed the others with `{% live_render %}`, which emits the markers the client needs to mount an embedded child:

```html
{% extends "base.html" %}
{% load live_tags %}

{% block content %}
<div dj-view="myapp.views.DashboardView">
    <h1>Dashboard for {{ user.username }}</h1>

    {% live_render "myapp.views.MetricsView" %}
</div>
{% endblock %}
```

See [Sticky LiveViews](sticky-liveviews.md) for more on `{% live_render %}`.

---

## See Also

- [Quick Start Guide](../getting-started/installation.md) -- Getting started with djust
- [Error Codes](error-codes.md) -- DJE-053 and T002 details
- [Best Practices](BEST_PRACTICES.md) -- State management and lifecycle patterns
