---
title: "Two-Way Model Binding"
slug: model-binding
section: guides
order: 6
level: intermediate
description: "Bind form inputs to server state with dj-model, .lazy, and .debounce modifiers"
---

# Two-Way Model Binding

djust's `dj-model` directive automatically syncs form input values with server-side view attributes. Every time an input changes, the server stores the new value on the view -- no event handler boilerplate needed. The sync itself does **not** re-render the page; see [When the page updates](#when-the-page-updates).

## What You Get

- **`dj-model`** -- Bind any form input to a view attribute; the value syncs on every change
- **`dj-model.lazy`** -- Sync on blur instead of every keystroke
- **`dj-model.debounce-N`** -- Debounce by N milliseconds for search-as-you-type
- **Automatic type coercion** -- Strings are converted to match the existing attribute type
- **Security checks** -- Private and forbidden fields cannot be set via binding

## Quick Start

### 1. Define Attributes on Your View

```python
from djust import LiveView

class SearchView(LiveView):
    template_name = 'search.html'

    def mount(self, request, **kwargs):
        self.search_query = ""
        self.category = "all"
        self.show_archived = False

    def get_context_data(self, **kwargs):
        results = Product.objects.all()
        if self.search_query:
            results = results.filter(name__icontains=self.search_query)
        if self.category != "all":
            results = results.filter(category=self.category)
        if not self.show_archived:
            results = results.exclude(archived=True)
        return {
            'results': results,
            'search_query': self.search_query,
            'category': self.category,
            'show_archived': self.show_archived,
        }
```

### 2. Bind Inputs with `dj-model`

```html
<input type="text" dj-model="search_query" placeholder="Search...">

<select dj-model="category">
    <option value="all">All Categories</option>
    <option value="electronics">Electronics</option>
    <option value="books">Books</option>
</select>

<label>
    <input type="checkbox" dj-model="show_archived">
    Show archived items
</label>

{% for product in results %}
    <div class="product">{{ product.name }}</div>
{% endfor %}
```

Every time an input changes, djust sends an `update_model` event and sets the attribute.

### When the page updates

`update_model` does **not** re-render. It skips the render on purpose, so the input the user is typing in isn't disturbed. The new value shows up in the next render that some *other* event triggers. In the example above, the `results` list only changes when another event (a button click, a form submit) runs.

To re-render on each change (live search, validation as you type), use an event directive instead of `dj-model`: `dj-input="handler"` (with `dj-debounce="N"`) or `dj-change="handler"`, and have the handler set the attribute. The examples below show both patterns.

## Modifiers

### `dj-model.lazy`

Sync on `change` (blur) instead of `input`. Use when the server operation is expensive.

```html
<input type="email" dj-model.lazy="email">
<textarea dj-model.lazy="bio"></textarea>
```

### `dj-model.debounce-N`

Debounce by N milliseconds. The update fires only after the user stops typing.

```html
<!-- 300ms debounce -->
<input type="text" dj-model.debounce-300="search_query">

<!-- 500ms debounce -->
<input type="text" dj-model.debounce-500="address">
```

> **Modifier fields must be listed in `allowed_model_fields`.** The automatic
> allowlist only recognises the plain `dj-model="<field>"` spelling. Fields bound
> with `dj-model.lazy` or `dj-model.debounce-N` are **not** picked up, so the
> server rejects their updates (it logs a "mass-assignment guard" warning) unless
> you list them explicitly:
>
> ```python
> class ProfileView(LiveView):
>     allowed_model_fields = ["email", "bio", "search_query", "address"]
> ```

> **The `.debounce-N` / `.lazy` in-name modifier is `dj-model`-only.** Only
> `dj-model` parses these suffixes from the attribute *name*. Event directives —
> `dj-input`, `dj-change`, `dj-click`, … — do **not**; they debounce via the
> separate standalone [`dj-debounce`](declarative-ux-attrs.md) attribute. Because
> a dot is a legal attribute-name character, `dj-input.debounce-200` is parsed as
> one literal attribute that no `[dj-input]` selector matches, so the input
> **silently never binds** (no handler, no event, no error). Spell a debounced
> live input the two-attribute way instead:
>
> ```html
> <!-- dj-model: in-name modifier -->
> <input dj-model.debounce-200="query">
>
> <!-- dj-input (and dj-click / dj-change / …): standalone dj-debounce -->
> <input dj-input="search" dj-debounce="200">
> ```
>
> In debug mode (`window.djustDebug = true`) djust logs a `console.warn` when it
> sees a `.debounce` / `.lazy` suffix on a non-`dj-model` directive, so the trap
> is no longer silent.

## Supported Input Types

| Input Type | Value Sent | Notes |
|------------|-----------|-------|
| `<input type="text">` | `el.value` (string) | |
| `<textarea>` | `el.value` (string) | |
| `<select>` | `el.value` (string) | |
| `<select multiple>` | Array of selected values | |
| `<input type="checkbox">` | `el.checked` (boolean) | Listens on `change` |
| `<input type="radio">` | Value of checked radio | Groups by `name` attribute |
| `<input type="number">` | String, coerced server-side | |
| `<input type="range">` | String, coerced server-side | |

## Type Coercion

The mixin automatically coerces incoming string values to match the existing attribute's type:

| Existing Type | Truthy Values | Falsy Values |
|---------------|---------------|--------------|
| `bool` | `"true"`, `"1"`, `"yes"`, `"on"` | `"false"`, `"0"`, `"no"`, `"off"` |
| `int` | `"42"` becomes `42` | |
| `float` | `"3.14"` becomes `3.14` | |

## Security

The ModelBindingMixin enforces these rules:

- Attributes starting with `_` cannot be set
- Fields like `template_name`, `request`, `session`, and other internals are blocked
- Only attributes that already exist on the view can be updated
- `allowed_model_fields` is **fail-closed**: a field is bindable only if it
  appears as plain `dj-model="<field>"` in the rendered template source, or is
  listed here. The modifier spellings (`dj-model.lazy=…`, `dj-model.debounce-N=…`)
  are not auto-detected, so list those fields here.
  Leaving it as `None` means "template-derived only" — never "everything"
- The blocked set is explicit, not a heuristic: `template_name`, `request`,
  `session`, `kwargs`, `args`, `use_actors` and `temporary_assigns`

```python
class AdminView(LiveView):
    allowed_model_fields = ['search_query', 'filter_status']

    # These cannot be set via dj-model:
    is_admin = False
    user_role = "viewer"
```

## Example: Search-as-you-Type

Live results need a re-render on each (debounced) change, so this uses
`dj-input` with `dj-debounce` rather than `dj-model`:

```python
from djust import LiveView
from djust.decorators import event_handler

class ProductSearch(LiveView):
    template_name = 'product_search.html'

    def mount(self, request, **kwargs):
        self.query = ""

    @event_handler()
    def search(self, value: str = "", **kwargs):
        self.query = value

    def get_context_data(self, **kwargs):
        results = []
        if self.query and len(self.query) >= 2:
            results = Product.objects.filter(
                name__icontains=self.query
            )[:20]
        return {'query': self.query, 'results': results}
```

```html
<input type="text" dj-input="search" dj-debounce="300" value="{{ query }}" placeholder="Search products...">

<div class="results">
    {% for product in results %}
        <div class="result-item">
            <strong>{{ product.name }}</strong>
            <span>${{ product.price }}</span>
        </div>
    {% empty %}
        {% if query %}<p>No results for "{{ query }}"</p>{% endif %}
    {% endfor %}
</div>
```

## Example: Form with Validation

`dj-model` stores the values; validation runs in `get_context_data`, so it
reflects the values as of the latest render. Because `dj-model` does not
re-render, the errors appear when another event renders the page -- here,
the form submit:

```python
from djust import LiveView
from djust.decorators import event_handler

class RegistrationForm(LiveView):
    template_name = "register.html"
    # Modifier bindings are not auto-allowlisted; list them.
    allowed_model_fields = ["username", "email", "password", "agree_terms"]

    def mount(self, request, **kwargs):
        self.username = ""
        self.email = ""
        self.password = ""
        self.agree_terms = False
        self.errors = {}

    def validate(self):
        self.errors = {}
        if self.username and len(self.username) < 3:
            self.errors["username"] = "Must be at least 3 characters"
        if self.email and "@" not in self.email:
            self.errors["email"] = "Invalid email address"
        if self.password and len(self.password) < 8:
            self.errors["password"] = "Must be at least 8 characters"

    @event_handler()
    def register(self, **kwargs):
        self.validate()
        if not self.errors and self.agree_terms:
            ...  # create the account

    def get_context_data(self, **kwargs):
        self.validate()
        return {
            "username": self.username,
            "email": self.email,
            "agree_terms": self.agree_terms,
            "errors": self.errors,
        }
```

```html
<form dj-submit="register">
    <label for="u">Username</label>
    <input id="u" type="text" dj-model.debounce-300="username" value="{{ username }}">
    {% if errors.username %}<span class="error">{{ errors.username }}</span>{% endif %}

    <label for="e">Email</label>
    <input id="e" type="email" dj-model.lazy="email" value="{{ email }}">
    {% if errors.email %}<span class="error">{{ errors.email }}</span>{% endif %}

    <label><input type="checkbox" dj-model="agree_terms"> I agree</label>
    <button type="submit">Register</button>
</form>
```

`debounce-300` waits for a pause in typing before syncing the value, and `lazy`
syncs on blur instead of on input. Neither re-renders by itself; the submit
does. For errors that update while typing, use `dj-input="…" dj-debounce="300"`
with a handler that sets the field.

## Example: Multi-Select Filter

A `<select multiple>` binds to a list. The list arrives as one value and is
coerced to the attribute's existing type. `dj-model` doesn't re-render, so the
filtered list refreshes when the user clicks **Apply** (any event would do):

```python
from djust import LiveView
from djust.decorators import event_handler

class FilterView(LiveView):
    template_name = "filter.html"

    def mount(self, request, **kwargs):
        self.selected_tags = []
        self.sort_by = "name"

    @event_handler()
    def apply_filters(self, **kwargs):
        pass  # the bound values are already set; this event re-renders

    def get_context_data(self, **kwargs):
        items = Item.objects.all()
        if self.selected_tags:
            items = items.filter(tags__name__in=self.selected_tags)
        return {"items": items.order_by(self.sort_by)}
```

```html
<select dj-model="selected_tags" multiple>
    <option value="python">Python</option>
    <option value="django">Django</option>
</select>

<select dj-model="sort_by">
    <option value="name">Name</option>
    <option value="-created">Newest</option>
</select>

<button dj-click="apply_filters">Apply</button>
```

## Combining with Event Handlers

`dj-model` works alongside `dj-click`, `dj-submit`, and other directives. The binding updates state; event handlers trigger actions and the re-render.

```html
<input type="text" dj-model.debounce-300="query">  <!-- list "query" in allowed_model_fields -->
<button dj-click="search">Search</button>

<form dj-submit="save">
    <input type="text" dj-model="title">
    <button type="submit">Save</button>
</form>
```

## Best Practices

- Use **`dj-model.lazy`** for expensive operations (database queries, API calls) to avoid running on every keystroke.
- For search inputs with live results, use **`dj-input="…"` with `dj-debounce="300"`**: `dj-model` alone never re-renders.
- Use plain **`dj-model`** for cheap local state like checkboxes and toggles.
- For security-sensitive views, always set `allowed_model_fields` to an explicit list. It is required anyway for fields bound with `.lazy` / `.debounce-N`.
