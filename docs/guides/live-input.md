# `{% live_input %}` — Standalone State-Bound Form Fields

djust's `FormMixin` and `WizardMixin` render form fields with proper CSS
classes, correct `dj-input` / `dj-change` bindings, and framework-aware
styling — but only for views backed by a Django `Form` class. The
`{% live_input %}` template tag is the lightweight alternative for the
**80% of UI state that doesn't need a full `forms.Form`**: modals, inline
panels, search boxes, settings pages, toggles — anywhere state lives
directly on view attributes.

## When to use what

| Scenario | Use |
|----------|-----|
| A multi-field form backed by `forms.Form` with validation | `{% live_field %}` (via `FormMixin`) |
| A multi-step wizard with per-step validation | `{% live_field %}` (via `WizardMixin`) |
| A single input bound to a view attribute (`self.query`, `self.notifications_enabled`) | **`{% live_input %}`** |
| A search box with debounced input | **`{% live_input %}`** with `debounce=300` |
| A modal with a subject + body + type-select | **`{% live_input %}`** for each field |
| Raw HTML that binds a `dj-input`/`dj-change` event | Prefer `{% live_input %}` so you don't forget the framework CSS class |

## Quick examples

```html
{% load live_tags %}

<!-- Search box with 300ms debounce -->
{% live_input "text" handler="search" value=query placeholder="Search..." debounce="300" %}

<!-- Note body -->
{% live_input "textarea" handler="set_body" value=body rows=5 placeholder="Your note..." %}

<!-- Status dropdown -->
{% live_input "select" handler="set_status" value=status choices=status_choices %}

<!-- Notification toggle -->
{% live_input "checkbox" handler="toggle_notifications" checked=notifications_enabled %}

<!-- Plan selector (radio group) -->
{% live_input "radio" handler="set_plan" value=plan choices=plan_choices %}
```

With the matching LiveView:

```python
from djust import LiveView
from djust.decorators import event_handler


class NotesView(LiveView):
    template_name = "notes.html"

    def mount(self, request, **kwargs):
        self.query = ""
        self.body = ""
        self.status = "open"
        self.notifications_enabled = True
        self.plan = "free"
        self.status_choices = [("open", "Open"), ("closed", "Closed")]
        self.plan_choices = [("free", "Free"), ("pro", "Pro")]

    @event_handler()
    def search(self, value: str = "", **kwargs):
        self.query = value

    @event_handler()
    def set_body(self, value: str = "", **kwargs):
        self.body = value

    @event_handler()
    def set_status(self, value: str = "", **kwargs):
        self.status = value

    @event_handler()
    def toggle_notifications(self, **kwargs):
        self.notifications_enabled = not self.notifications_enabled

    @event_handler()
    def set_plan(self, value: str = "", **kwargs):
        self.plan = value
```

## Supported field types

| Type | Default event | Notes |
|------|---------------|-------|
| `text` | `dj-input` | Per-keystroke. Pair with `debounce=` for search. |
| `textarea` | `dj-input` | Multi-line text. Supports `rows=` / `cols=`. |
| `password` | `dj-input` | |
| `email` | `dj-input` | Browser enforces email format. |
| `url` | `dj-input` | Browser enforces URL format. |
| `tel` | `dj-input` | Browser keyboard hint on mobile. |
| `search` | `dj-input` | |
| `number` | `dj-input` | Supports `min=` / `max=` / `step=`. |
| `hidden` | — | No event. For carrying server state. |
| `select` | `dj-change` | Requires `choices=`. Selected option based on `value=`. |
| `checkbox` | `dj-change` | Requires `checked=` (bool). Single checkbox only; for multi-checkbox lists, repeat the tag. |
| `radio` | `dj-change` | Requires `choices=`. Renders a set of `<label>` wrappers. |

## Kwargs reference

| Kwarg | Type | Description |
|-------|------|-------------|
| `handler` | `str` | **Required** (except for `hidden`). The name of the `@event_handler` method to invoke on change. |
| `value` | any | Current value (from a view attribute). |
| `name` | `str` | HTML `name` attribute. Defaults to `handler`. |
| `event` | `str` | Override the default event. One of `input`, `change`, `blur` (with or without the `dj-` prefix). |
| `css_class` | `str` | Override the framework CSS class. Defaults to `config.get_framework_class('field_class')`. |
| `debounce` | `str` | Forwarded as `dj-debounce="..."`. Milliseconds. |
| `throttle` | `str` | Forwarded as `dj-throttle="..."`. Milliseconds. |
| `choices` | list | `[(value, label), ...]` or `[label, ...]` for `select` and `radio`. |
| `checked` | `bool` | For `checkbox` — whether the box is checked. |
| `**kwargs` | | Any other kwarg is forwarded as an HTML attribute. Keys with `_` are normalised to `-` (e.g. `aria_label="Search"` → `aria-label="Search"`). |

## XSS safety

Every attribute value and every text content node passes through
`django.utils.html.escape` exactly once, via the shared
`djust._html.build_tag` helper. The test suite includes an XSS matrix
that injects `"><script>alert(1)</script>` into:

- every field type's `value=`
- every field type's `placeholder=`, `aria_label=`
- `choices` values and labels on `select` and `radio`
- `checkbox` values
- `hidden` values

All test cases assert no unescaped `<script>` tag reaches the rendered
output. This is the primary defense against future regressions — **any
change to the rendering code that introduces a new escape path will
break the XSS matrix**.

## Differences from `{% live_field %}`

| | `{% live_field %}` | `{% live_input %}` |
|---|---|---|
| Requires a Django `Form` class | **Yes** | No |
| Requires `FormMixin` or `WizardMixin` | **Yes** | No |
| Error display integration | **Yes** (via `form_errors`) | No (caller handles errors) |
| `data-field_name` attribute | **Yes** (for shared validate handlers) | **No** (one handler per field) |
| Per-field validation on blur | **Yes** (auto) | No (caller wires it up) |
| Lightweight for single-input state | Heavy | **Yes** |

Use `{% live_field %}` when you have a Django `Form` class and want
validation. Use `{% live_input %}` when state lives directly on view
attributes and you just need a clean HTML widget bound to a handler.

## Migrating from raw HTML

**Before:**

```html
<!-- Forgot the framework class; used wrong event binding -->
<input type="text" value="{{ query }}" dj-change="search" placeholder="Search...">
```

**After:**

```html
{% load live_tags %}

{% live_input "text" handler="search" value=query placeholder="Search..." %}
```

The tag picks up the framework CSS class automatically, uses the right
event binding (`dj-input` for text), and HTML-escapes the value and
placeholder.

## Error messages

If you pass an unsupported `field_type` or forget `handler=`, the tag
renders an HTML comment describing the error:

```html
<!-- ERROR: {% live_input %} unknown field_type 'pineapple' — supported: text, textarea, select, password, email, number, url, tel, search, hidden, checkbox, radio -->
```

This is visible in the page source but renders as nothing — caller gets
immediate feedback in DevTools without a template crash.

## See also

- [#650](https://github.com/djust-org/djust/issues/650) — the original feature request
- `{% live_field %}` in `live_tags.py` — the Form-backed equivalent
- `WizardMixin` — multi-step forms with per-step validation
- `docs/guides/forms.md` — the full form rendering guide
