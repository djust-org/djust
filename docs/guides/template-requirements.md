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

### Advanced Case (1% of templates)

Only use explicit `dj-root` when you need to exclude wrapper elements from VDOM diffing:

```html
<div dj-view="myapp.views.CounterView">
    <header class="static-metadata">
        <!-- This is OUTSIDE the VDOM root -->
        <span>Connected to CounterView</span>
    </header>

    <div dj-root>
        <!-- Only THIS section gets VDOM updates -->
        <p>Count: {{ count }}</p>
        <button dj-click="increment">+1</button>
    </div>
</div>
```

| Attribute | Required | Purpose |
|-----------|----------|---------|
| `dj-view` | ✅ Yes | Identifies the LiveView class for the WebSocket connection |
| `dj-root` | ❌ Optional | Explicitly marks VDOM root (auto-inferred from `dj-view` if omitted) |

---

## What Each Attribute Does

### `dj-view` (Required)

This attribute tells djust's client JavaScript which LiveView class to connect to over WebSocket.

```html
<div dj-view="myapp.views.CounterView">
```

When the page loads, the client JS finds this attribute, reads the view class path, and opens a WebSocket connection to mount that view. Without it, no WebSocket connection is established and the page behaves like a static HTML page.

The value must be the **full Python import path** to the LiveView class (e.g., `myapp.views.CounterView`).

**Important**: The element with `dj-view` is automatically the VDOM root unless you explicitly add `dj-root` elsewhere.

### `dj-root` (Optional)

This attribute explicitly marks the root element of the VDOM tree. When an event handler updates state and triggers a re-render, djust:

1. Renders the template on the server with the new state
2. Diffs the new HTML against the old HTML using the Rust VDOM engine
3. Sends only the changed patches to the client
4. Applies the patches to the DOM

**You rarely need this attribute.** Only use it when:
- You have static wrapper elements that should not be diffed (performance optimization)
- You need to exclude metadata or navigation from the update boundary

In 99% of cases, just use `dj-view` and let the framework infer the root automatically.

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

### Pattern 2: Explicit Root (Rare)

```html
<div dj-view="myapp.views.DashboardView">
    <nav class="sidebar">
        <!-- Static navigation, never changes -->
        <ul>...</ul>
    </nav>

    <div dj-root>
        <!-- Only dashboard content updates -->
        <h1>{{ title }}</h1>
        <p>{{ content }}</p>
    </div>
</div>
```

**When to use**: Only when profiling shows wrapper diffing is expensive, or when you have large static sections that never change.

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
| `djust.T002` | LiveView template missing `dj-view` or `dj-root` attribute |
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

Each LiveView needs its own root element with `dj-view`:

```html
{% extends "base.html" %}

{% block content %}
<div dj-view="myapp.views.HeaderView">
    <h1>Dashboard for {{ user.username }}</h1>
</div>

<div dj-view="myapp.views.MetricsView">
    <p>Active users: {{ active_count }}</p>
</div>
{% endblock %}
```

---

## See Also

- [Quick Start Guide](QUICKSTART.md) -- Getting started with djust
- [Error Codes](error-codes.md) -- DJE-053 and T002 details
- [Best Practices](BEST_PRACTICES.md) -- State management and lifecycle patterns
