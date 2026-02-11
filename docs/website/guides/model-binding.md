---
title: "Two-Way Model Binding"
slug: model-binding
section: guides
order: 6
level: intermediate
description: "Bind form inputs to server state with dj-model, .lazy, and .debounce modifiers"
---

# Two-Way Model Binding

djust's `dj-model` directive automatically syncs form input values with server-side view attributes. Every time an input changes, the server updates and re-renders -- no event handler boilerplate needed.

## What You Get

- **`dj-model`** -- Bind any form input to a view attribute with real-time sync
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

That is it. Every time an input changes, djust sends an `update_model` event, the attribute is updated, and the view re-renders.

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
- Use `allowed_model_fields` to restrict bindable fields explicitly

```python
class AdminView(LiveView):
    allowed_model_fields = ['search_query', 'filter_status']

    # These cannot be set via dj-model:
    is_admin = False
    user_role = "viewer"
```

## Example: Search-as-you-Type

```python
class ProductSearch(LiveView):
    template_name = 'product_search.html'

    def mount(self, request, **kwargs):
        self.query = ""

    def get_context_data(self, **kwargs):
        results = []
        if self.query and len(self.query) >= 2:
            results = Product.objects.filter(
                name__icontains=self.query
            )[:20]
        return {'query': self.query, 'results': results}
```

```html
<input type="text" dj-model.debounce-300="query" placeholder="Search products...">

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

## Combining with Event Handlers

`dj-model` works alongside `dj-click`, `dj-submit`, and other directives. The binding updates state; event handlers trigger actions.

```html
<input type="text" dj-model.debounce-300="query">
<button dj-click="search">Search</button>

<form dj-submit="save">
    <input type="text" dj-model="title">
    <button type="submit">Save</button>
</form>
```

## Best Practices

- Use **`dj-model.lazy`** for expensive operations (database queries, API calls) to avoid running on every keystroke.
- Use **`dj-model.debounce-300`** for search inputs where you want real-time feedback with limited server calls.
- Use plain **`dj-model`** for cheap local state like checkboxes and toggles.
- For security-sensitive views, always set `allowed_model_fields` to an explicit list.
