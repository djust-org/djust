# Templates

djust uses a Rust-powered template engine that is **fully compatible with Django's template syntax**. All 57 built-in Django filters work. Rendering is 16-37x faster than Django's Python renderer.

## Required Template Structure

Every LiveView template needs two things:

```html
<!DOCTYPE html>
<html>
<head>
    {% load live_tags %}
    {% djust_client_config %}   {# Emits client config meta tags; djust auto-injects the ~74 KB gz client JS #}
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

djust's `striptags` is a port of Django's `strip_tags` over CPython's `html.parser` tokenizer, **pinned to one parser behaviour** so a template renders the same on every host. Django's own `strip_tags` runs whatever `html.parser` the interpreter ships, and CPython has changed that parser inside patch releases. On ordinary markup the two agree. They differ only on malformed or adversarial input (unterminated references, abruptly closed comments), and the figures below are measured over the repository's 4,000-value adversarial corpus, not over real-world HTML.

| CPython | corpus values where djust differs from Django | share |
|---|---|---|
| 3.12.13, 3.13.7 | 0 | 0% |
| 3.12.9 | 992 | 24.8% |
| 3.14.6 | 231 | 5.8% |
| 3.13.15, 3.14.7 | 239 | 6.0% |

- **Before 3.12.12.** The parser predates the HTML5 alignment rewrite on 3.12.9 through 3.12.11 (3.12.9 is measured here) and, on the older lines, up to 3.11.13 and 3.10.18; djust differs on a quarter of the corpus. 3.12.12, 3.11.14 and 3.10.19 carry the rewritten parser. Cells where Django itself raises are left out of these counts (86 on 3.12.9, where the old parser fails on malformed input; 2 on every other measured release, the depth-guard values below).
- **`&` and `&#` at the end of the input.** CPython 3.13.10 and 3.14.1 changed how an unterminated character reference is closed (231 of the 239 values above). djust keeps the earlier behaviour, so Django on those releases (measured through 3.14.7, the latest tested) returns, for example:

| input | djust (and Django 3.12.12 up to the change) | Django on 3.13.10+ / 3.14.1+ |
|---|---|---|
| `'<i>&a'` | `'a'` | `'&a;'` |
| `'<b>&#65'` | `'&#65'` | `'&#65;'` |
| `'&#x<br>'` | `'&#x<br>'` | `'&#x'` |
| `'&#xZZ<i>'` | `'&#xZZ<i>'` | `'&#xZZ'` |

- **Abruptly closed empty comments.** CPython 3.13.15 and 3.14.7 end `<!-->` and `<!--->` at their first `>`, as HTML5 does. Before, the comment ran on to a later `-->`. These are the other **eight** of the 239 values. djust keeps the earlier rule, so it differs from Django on those two releases (and, as far as measured, no later one) on exactly these inputs (shown before autoescaping, as `strip_tags` returns them):

| input | djust (and Django 3.12.12 up to the change) | Django on 3.13.15+ / 3.14.7+ |
|---|---|---|
| `'<!--->-->'` | `''` | `'-->'` |
| `'<!--->a<!--->'` | `''` | `'a'` |
| `'<!---><?pi>&#-->'` | `''` | `'&#-->'` |
| `'<!--><br/><0>--><br/>'` | `''` | `'<0>-->'` |
| `'price<!--->< b><i><!--a--!>'` | `'price'` | `'price< b>'` |
| `'<script><!--->&#65<<>><!-- c --></>'` | `''` | `'&#65;<<>>'` |
| `'<!--->< b><b x=\t<!DOCTYPE html><!--->'` | `''` | `'< b>'` |
| `'<script>a<b</script><<b>script><!--->&a b<!-- c --><!--->'` | `'a'` | `'a&a; b'` |

- **The depth guard.** Where Django raises `SuspiciousOperation` (an input that needs more than 50 stripping passes, or an unterminated tag of 1,002 or more characters (`<`, a letter, then 1,000 or more characters before any `>`) that contains 50 or more `<`), djust renders an empty string. Empty is the one refusal value that is safe in every context the output reaches.

djust does not follow the newer parser on purpose. A template filter whose output changes when the base image is bumped is a worse property than a documented, fixed difference, and the pinned behaviour is covered by a differential test against Django on every supported interpreter. Following the newer rule is a deliberate change to `htmlparser.rs`, not something the running interpreter decides.

`striptags` is not a sanitizer in either engine: Django documents that its output is not guaranteed safe. Keep autoescape on and do not pipe the result through `|safe`.

**If you need Django's exact result on the running interpreter**, call Django's own function from a project filter under a different name, so the same template behaves the same in both engines (Django's engine lets a loaded filter shadow a built-in of the same name, djust's Rust engine does not), and use that where the difference matters:

```python
# apps/shared/templatetags/django_strip.py
from django import template
from django.utils.html import strip_tags

register = template.Library()


@register.filter(name="django_striptags", is_safe=True)
def django_striptags(value):
    return strip_tags(value)
```

```html
{% load django_strip %}
{{ comment|django_striptags }}
```

That output follows the interpreter, so it changes when the interpreter's parser does; computing the value in the view with `strip_tags` has the same property.

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
