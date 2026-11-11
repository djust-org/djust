# Templates

djust uses a Rust-powered template engine that is **fully compatible with Django's template syntax**. All 57 built-in Django filters work. Rendering is 16-37x faster than Django's Python renderer.

## Required Template Structure

Every LiveView template needs two things:

```html
<!DOCTYPE html>
<html>
<head>
    {% load live_tags %}
    {% djust_client_config %}   {# Emits client config meta tags; djust auto-injects the ~80 KB gz client JS #}
</head>
<body>
    <div dj-root>                    {# Reactive region — only this is patched #}
        {{ count }}
        <button dj-click="increment">+</button>
    </div>
</body>
</html>
```

- `{% djust_client_config %}` — emits client config meta tags; djust auto-injects the client JavaScript into every LiveView response (no manual `<script>` tag needed)
- `dj-root` — marks the reactive subtree; only HTML inside this element is diffed and patched. It is the only root attribute you write: djust stamps `dj-view` onto it server-side with the dotted path of the view rendering the page
- `dj-view="myapp.views.MyView"` — optional; write it only to name a specific view (embedded/sticky, or a template shared by several views). Literal path — there is no `dj_view_id` context variable

## Event Directives

```html
<!-- Click: data-* attributes become handler kwargs -->
<button dj-click="increment">+</button>
<button dj-click="delete" data-item-id="{{ item.id }}">Delete</button>

<!-- Input: fires after typing pauses (300 ms default debounce), passes value= and _target= -->
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
window.djust.hooks.chart = {
    mounted()   { initChart(this.el); },
    updated()   { updateChart(this.el); },
    destroyed() { destroyChart(this.el); },
};
```

Callbacks take no arguments; the element is `this.el`.

See [Hooks guide](../guides/hooks.md) for details.

## Template Filters

All 57 Django built-in filters are supported. Some notes:

- HTML-producing filters (`urlize`, `urlizetrunc`, `unordered_list`) are in the Rust engine's `safe_output_filters` whitelist — they're automatically marked as safe without requiring `|safe`. They don't need `|safe`; adding it is redundant, since the filter already escaped its input. *(Standard Django achieves this via `SafeData` type-checking; djust uses an explicit whitelist instead.)*
- `|safe` works as expected for pre-escaped HTML strings

### `striptags` and the Python version

In 2.0, `striptags` runs Django's own `strip_tags`, so its output follows the running interpreter's `html.parser`, exactly as Django's does. CPython has changed that parser inside patch releases (3.12.12, 3.13.10, 3.14.1, 3.13.15 and 3.14.7), and on malformed input (unterminated references, abruptly closed comments) the result can differ between hosts on different releases. An input that needs more than 50 stripping passes raises Django's `SuspiciousOperation`.

`striptags` is not a sanitizer: Django documents that its output is not guaranteed safe. Keep autoescape on and do not pipe the result through `|safe`.

### Custom filters (`@register.filter`)

Project-defined custom filters work in the Rust render path the same way they work in Django's Python renderer. djust walks each Django ``Library`` registered by your apps' ``templatetags/`` modules at the first LiveView render and forwards every filter callable to the Rust engine. Both ``filter.is_safe`` and ``filter.needs_autoescape`` are honoured.

```python
# apps/shared/templatetags/dict_lookup.py
from django import template

register = template.Library()


@register.filter(name="lookup")
def lookup(mapping, key):
    return mapping.get(key, "") if isinstance(mapping, dict) else ""
```

```html
{# templates/foo.html #}
{% load dict_lookup %}

<a href="{{ sort_urls|lookup:col }}">Sort by {{ col }}</a>
```

Custom filters can take 0 or 1 argument:

- Quoted args (``|prefix:"hello"``) are passed as literal strings.
- Bare-identifier args (``|prefix:greeting``) are resolved against the
  template context first, then passed to the filter.

``is_safe`` means exactly what it means in Django: the filter does not
introduce HTML of its own, so a *safe* input may stay safe after it. It
never makes an unsafe input safe — ``{{ user_text|shout }}`` is escaped
whether or not ``shout`` is ``is_safe=True`` (#2548). A filter that
*produces* markup must mark its own output, with ``mark_safe`` or
``format_html``; the flag does nothing for it:

```python
@register.filter(name="bold_html")
def bold_html(value):
    return format_html("<b>{}</b>", value)   # escapes value, output is safe
```

Filters that need to know whether the surrounding template is in
auto-escape mode declare ``needs_autoescape=True`` and accept
``autoescape`` as a kwarg — same as Django.

**One library per tag or filter name.** Django scopes ``{% load %}`` to the
template, so two libraries may both define ``at``. djust's Rust engine keeps one
registry for the process: each ``{% load %}`` of either library takes the name
from the other, which drops and re-parses every cached template, and a template
renders whichever version loaded last. Give each name to one library.
``manage.py check`` reports a name that two libraries your templates load both
register (``djust.T026``), and djust logs a warning the first time a
``{% load %}`` takes a name from another library.

### What a custom filter or tag receives for a model

A custom filter, a `simple_tag` (with or without `as`), an `inclusion_tag` and a `takes_context` tag
are application code, and the model they are handed is **never a live Django instance** on any djust
entry: `isinstance(value, Model)` is `False`. What they are handed depends on the entry:

- **`DjustTemplateBackend` and `djust._rust.render_template`** hand a *floor proxy*
  (`djust.serialization._SidecarModelProxy`). It answers attribute reads: fields, properties and
  zero-argument methods (`value.username`, `value.owner_code`, `value.get_note_text()`). It refuses
  `password`, any field the model lists in `djust_exclude_fields`, and names starting with `_`
  (`value._meta`) with `AttributeError`. It is not subscriptable (`value["username"]` raises
  `TypeError`) and not iterable, and `str(value)` is the model's own text.
- **A LiveView** hands a plain `dict`: the model is turned into its serialized mapping in Python before
  the Rust engine sees it (ADR-027 keeps models eager on the state path). Read it with `value["name"]`
  or `value.get("name")`.

What each entry hands over (measured; every cell is pinned by
`python/tests/test_custom_callable_model_argument_3949.py`):

| the template's value | LiveView state | `DjustTemplateBackend` | `render_template` |
|---|---|---|---|
| one model | `dict` | floor proxy | floor proxy |
| a list of models | `list` of `dict` | `list` of floor proxies | `list` of floor proxies |
| a row of `{% for r in rows %}` | `dict` | floor proxy | floor proxy |
| a `QuerySet`, to a **filter** | `list` of `dict` | `list` of `dict` | `list` of floor proxies |
| a `QuerySet`, to a **tag** | `list` of `dict` | queryset proxy | queryset proxy |
| a row of `{% for r in qs %}` | `dict` | `dict` | floor proxy |
| a `dict` holding a model, or a list of models | a `dict` holding `dict`s | a `dict` holding floor proxies | a `dict` holding floor proxies |

A filter and every tag form are handed the same thing in every row; the one place they differ is a
`QuerySet` passed whole (the two `QuerySet` rows). A field the template reads (`{{ user.username|my_filter }}`) arrives as that
field's plain value, a `str`, on every entry.

**What holds on every entry** (the floor): the value carries neither `password` nor a field the model
lists in `djust_exclude_fields`, as a value or as a key, and `str(value)` does not write them. The
guarantee is per *field*: a method or property that computes from an excluded field is a result like any
other. With `last_name` excluded, `value.get_full_name()` on a proxy, and the `get_full_name` value of a
LiveView `dict`, still contain the last name, so do not rely on `djust_exclude_fields` to hide what a
method derives from the field.

**What differs today.** The LiveView `dict` is not a fixed shape: its keys are what the template names.
A single model the template names nothing on is the identity keys (`id`, `pk`, `__str__`, `__model__`);
if the template prints `{{ x.owner_code }}` elsewhere, the dict a filter receives has `owner_code` too;
and the rows of a list or queryset the template names nothing on carry their fields, properties and
zero-argument `get_*()` results. So the same filter sees different keys when an unrelated expression is
added to the template, and a filter that prints that dict writes values the page never named
(`password` and excluded fields are still not among them). A Django-library tag that prints its operand
is the same case: `{% blocktranslate with u=x %}[{{ u }}]{% endblocktranslate %}` prints `[Account:a1]`
on the backend and the mapping on a LiveView.

Code that must behave the same on every entry takes the value it needs, not the model:

```python
# apps/shared/templatetags/display.py
from django import template

register = template.Library()


@register.filter
def initial(text):
    return str(text)[:1].upper()
```

```html
{# pass the field, not the model: a str on every entry #}
{% load display %}
{{ user.username|initial }}
{% blocktranslate with u=user.username %}Hello {{ u }}{% endblocktranslate %}
```

Do not depend on the number or names of the keys of a LiveView `dict`, and do not branch on
`isinstance(value, dict)` or `isinstance(value, Model)`.

The two shapes are a known inconsistency, not a contract. Handing custom code on a LiveView the same
floor proxy the backend hands it is the render-time handle overlay decided together with the LiveView
reverse-relation read in [#3936](https://github.com/djust-org/djust/issues/3936). It is tracked in
[#3949](https://github.com/djust-org/djust/issues/3949) for 1.4.0, behind an ADR-027 addendum, and will
change which of the cells above is a `dict`. Python's template engine hands a filter the live instance,
password hash included; no djust entry reproduces that.

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

## Conditional Class Attributes

While `{% if %}` inside attribute values works, **inline conditionals are recommended** because they produce cleaner VDOM output with no comment anchors:

```html
<!-- Works, but not recommended — may produce unnecessary VDOM anchors -->
<a class="nav-link {% if active %}active{% endif %}">

<!-- Recommended: inline conditional -->
<a class="nav-link {{ 'active' if active else '' }}">

<!-- Recommended: full ternary -->
<div class="{{ 'card-active' if selected else 'card' }}">
```

`{{ expr if condition else fallback }}` is resolved entirely in the template engine — no DOM comment anchors are inserted, so VDOM path indices stay correct. The `else` branch is optional and defaults to empty string.

## Template Requirements (Legacy)

Some older setups used `dj-view` and `dj-root` differently. The pattern is:

- `dj-root` on the reactive region — the one attribute you write; djust stamps `dj-view` onto it server-side
- `dj-view="myapp.views.MyView"` only when you need to name a specific view (embedded/sticky, or a shared template); a literal dotted path, never `{{ dj_view_id }}`

See [error codes](../guides/error-codes.md) if you get a `T002`- or
`T012`-family system-check warning about missing template attributes. (`T001`
is a different check — the deprecated `@click` syntax.)

## Next Steps

- [Events](./events.md) — event handler patterns
- [Hooks](../guides/hooks.md) — client-side JavaScript hooks
- [Components](./components.md) — reusable UI components
