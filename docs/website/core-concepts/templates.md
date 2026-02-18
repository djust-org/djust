# Templates

djust uses a Rust-powered template engine that is **fully compatible with Django's template syntax**. All 57 built-in Django filters work. Rendering is 16-37x faster than Django's Python renderer.

## Required Template Structure

Every LiveView template needs two things:

```html
<!DOCTYPE html>
<html>
<head>
    {% load djust_tags %}
    {% djust_scripts %}   {# Loads the ~5KB client JS #}
</head>
<body dj-view="{{ dj_view_id }}">   {# Connects page to WebSocket session #}
    <div dj-root>                    {# Reactive region — only this is patched #}
        {{ count }}
        <button dj-click="increment">+</button>
    </div>
</body>
</html>
```

- `{% djust_scripts %}` — injects the client JavaScript
- `dj-view="{{ dj_view_id }}"` — on `<body>`, binds the page to the session (use the context variable `dj_view_id`)
- `dj-root` — marks the reactive subtree; only HTML inside this element is diffed and patched

## Event Directives

```html
<!-- Click: data-* attributes become handler kwargs -->
<button dj-click="increment">+</button>
<button dj-click="delete" data-item-id="{{ item.id }}">Delete</button>

<!-- Input: fires on every keystroke, passes value= to handler -->
<input type="text" dj-input="search" value="{{ query }}" />

<!-- Change: fires on blur or select change -->
<select dj-change="filter_status">
    <option value="all" {% if status == "all" %}selected{% endif %}>All</option>
    <option value="active" {% if status == "active" %}selected{% endif %}>Active</option>
</select>

<!-- Form submit: all named fields arrive as handler kwargs -->
<form dj-submit="save_form">
    {% csrf_token %}
    <input name="title" value="{{ title }}" />
    <button type="submit">Save</button>
</form>

<!-- Keyboard shortcuts -->
<input dj-keydown.enter="submit" dj-keydown.escape="cancel" />
```

## Django Template Syntax

All standard Django template tags and filters work:

```html
<!-- Variables -->
{{ user.username }}
{{ count|default:"0" }}
{{ text|upper|truncatechars:50 }}

<!-- Conditionals -->
{% if user.is_authenticated %}
    Hello, {{ user.username }}!
{% else %}
    Please log in.
{% endif %}

<!-- Loops -->
{% for item in items %}
    <li data-key="{{ item.id }}">{{ item.name }}</li>
{% empty %}
    <li>No items.</li>
{% endfor %}

<!-- Template inheritance -->
{% extends "base.html" %}
{% block content %}
    <div dj-root>...</div>
{% endblock %}
```

## Keyed Lists

Add `data-key` on list items to enable optimal VDOM diffing when items reorder:

```html
{% for item in items %}
<div data-key="{{ item.id }}">
    {{ item.name }}
    <button dj-click="delete" data-item-id="{{ item.id }}">Delete</button>
</div>
{% endfor %}
```

Without `data-key`, djust diffs by position — correct but may produce more DOM mutations when items are inserted or reordered.

## Skipping Re-Renders

Prevent djust from patching a subtree that's managed by external JavaScript (charts, rich text editors, maps):

```html
<div dj-update="ignore" id="my-chart">
    <!-- Not touched by djust VDOM patching -->
</div>
```

## JavaScript Hooks

Attach client-side lifecycle handlers to elements:

```html
<div dj-hook="chart" id="my-chart"></div>
```

Then in JavaScript:

```javascript
djust.hooks.chart = {
    mounted(el) { initChart(el); },
    updated(el) { updateChart(el); },
    destroyed(el) { destroyChart(el); },
};
```

See [Hooks guide](../guides/hooks.md) for details.

## Template Filters

All 57 Django built-in filters are supported. Some notes:

- HTML-producing filters (`urlize`, `urlizetrunc`, `unordered_list`) handle their own escaping internally — do not pipe them through `|safe` or you'll double-escape
- `|safe` works as expected for pre-escaped HTML strings
- Custom template filters defined with `@register.filter` in Python work automatically

## Inline Templates

For small views, define the template directly on the class:

```python
class HelloView(LiveView):
    template = """
        <div>
            <h1>Hello {{ name }}!</h1>
            <input dj-input="update_name" value="{{ name }}" />
        </div>
    """
```

**Limitation:** Avoid `{% elif %}` in inline templates — use separate `{% if %}` blocks:

```html
<!-- Avoid: -->
{% if a %}...{% elif b %}...{% endif %}

<!-- Use instead: -->
{% if a %}...{% endif %}
{% if not a and b %}...{% endif %}
```

## Template Requirements (Legacy)

Some older setups used `dj-view` and `dj-root` differently. The required pattern is:
- `dj-view="{{ dj_view_id }}"` on the `<body>` tag (or outermost container)
- `dj-root` on the reactive region inside

See [error codes](../guides/error-codes.md) if you get a `DJUST_E001` or `DJUST_E002` error about missing template attributes.

## Next Steps

- [Events](./events.md) — event handler patterns
- [Hooks](../guides/hooks.md) — client-side JavaScript hooks
- [Components](./components.md) — reusable UI components
