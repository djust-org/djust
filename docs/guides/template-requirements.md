# Template Requirements

Every djust LiveView template needs two attributes on its root element. This guide explains what they are, why both are required, and how to fix common mistakes.

---

## Required Attributes

A LiveView template's root element must have **both** of these attributes:

```html
<div data-djust-view="myapp.views.CounterView" data-djust-root>
    <!-- Your template content here -->
</div>
```

| Attribute | Required | Purpose |
|-----------|----------|---------|
| `data-djust-view` | Yes | Identifies the LiveView class for the WebSocket connection |
| `data-djust-root` | Yes | Marks the root element for VDOM diffing |

---

## What Each Attribute Does

### `data-djust-view`

This attribute tells djust's client JavaScript which LiveView class to connect to over WebSocket.

```html
<div data-djust-view="myapp.views.CounterView">
```

When the page loads, the client JS finds this attribute, reads the view class path, and opens a WebSocket connection to mount that view. Without it, no WebSocket connection is established and the page behaves like a static HTML page.

The value must be the **full Python import path** to the LiveView class (e.g., `myapp.views.CounterView`).

### `data-djust-root`

This attribute marks the root element of the VDOM tree. When an event handler updates state and triggers a re-render, djust:

1. Renders the template on the server with the new state
2. Diffs the new HTML against the old HTML using the Rust VDOM engine
3. Sends only the changed patches to the client
4. Applies the patches to the DOM

The VDOM engine needs to know which element is the root to diff against. That element is the one with `data-djust-root`.

```html
<div data-djust-view="myapp.views.CounterView" data-djust-root>
    <p>Count: {{ count }}</p>
    <button dj-click="increment">+1</button>
</div>
```

---

## Why Both Are Needed

| Without | What Happens |
|---------|-------------|
| Missing `data-djust-view` | No WebSocket connection. Page is static HTML. Clicks on `dj-click` buttons do nothing. |
| Missing `data-djust-root` | WebSocket connects, events fire, state updates on server. But the client cannot apply patches to the DOM. You will see **DJE-053: No DOM changes** warnings in the server logs. |

Both are required for the full reactive cycle: events -> server state change -> VDOM diff -> DOM patches.

---

## Common Error: Missing `data-djust-root`

This is the most common template mistake. The symptom: clicking buttons fires events (you can see WebSocket traffic in browser DevTools), but the UI never updates. In your server logs, you will see:

```
WARNING [djust] Event 'increment' on CounterView produced no DOM changes (DJE-053).
The modified state may be outside <div data-djust-root>.
```

**Wrong** -- missing `data-djust-root`:

```html
<!-- WRONG: missing data-djust-root -->
<div data-djust-view="myapp.views.CounterView">
    <p>Count: {{ count }}</p>
    <button dj-click="increment">+1</button>
</div>
```

**Correct**:

```html
<!-- CORRECT: both attributes present -->
<div data-djust-view="myapp.views.CounterView" data-djust-root>
    <p>Count: {{ count }}</p>
    <button dj-click="increment">+1</button>
</div>
```

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

The LiveView template extends the base and puts both attributes on its root element inside the `content` block:

```html
{% extends "base.html" %}

{% block content %}
<div data-djust-view="myapp.views.CounterView" data-djust-root>
    <h1>Counter</h1>
    <p>Count: {{ count }}</p>
    <button dj-click="increment">+1</button>
</div>
{% endblock %}
```

### Important: State outside the VDOM root

Only content **inside** the `data-djust-root` element is tracked by the VDOM. If your event handler modifies state that is rendered in `base.html` (outside the root), the VDOM diff will not detect the change.

**Wrong** -- state rendered outside the root:

```html
<!-- base.html -->
<body>
    <!-- This is OUTSIDE data-djust-root -- VDOM cannot update it -->
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
| `djust.T002` | LiveView template missing `data-djust-root` attribute |
| `djust.T001` | Deprecated `@click` syntax (should be `dj-click`) |
| `djust.T003` | Wrapper template using `{% include %}` instead of `{{ liveview_content\|safe }}` |
| `djust.T004` | `document.addEventListener` for djust events (should be `window`) |

The T002 check looks for templates that contain `dj-click`, `dj-input`, `dj-change`, `dj-submit`, or `dj-model` attributes but are missing `data-djust-root`. Templates that use `{% extends %}` are excluded from this check since the root is typically in a parent template.

---

## Examples

### Minimal LiveView Template

```html
<div data-djust-view="myapp.views.HelloView" data-djust-root>
    <p>Hello, {{ name }}!</p>
    <input dj-model="name" type="text" placeholder="Enter your name">
</div>
```

### With Template Inheritance

```html
{% extends "base.html" %}

{% block content %}
<div data-djust-view="myapp.views.TodoView" data-djust-root>
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

Each LiveView needs its own root element with both attributes:

```html
{% extends "base.html" %}

{% block content %}
<div data-djust-view="myapp.views.HeaderView" data-djust-root>
    <h1>Dashboard for {{ user.username }}</h1>
</div>

<div data-djust-view="myapp.views.MetricsView" data-djust-root>
    <p>Active users: {{ active_count }}</p>
</div>
{% endblock %}
```

---

## See Also

- [Quick Start Guide](QUICKSTART.md) -- Getting started with djust
- [Error Codes](error-codes.md) -- DJE-053 and T002 details
- [Best Practices](BEST_PRACTICES.md) -- State management and lifecycle patterns
