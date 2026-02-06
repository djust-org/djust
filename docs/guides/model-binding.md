# Two-Way Model Binding

djust's `dj-model` directive automatically syncs form input values with server-side view attributes, similar to Vue.js `v-model` or Angular `ngModel`.

## Overview

- **dj-model** - Bind any form input to a view attribute with real-time sync
- **dj-model.lazy** - Sync on `change` (blur) instead of `input` for expensive operations
- **dj-model.debounce-N** - Debounce by N milliseconds for search-as-you-type
- **ModelBindingMixin** - Server-side mixin that handles `update_model` events with type coercion and security checks
- **Supported inputs** - text, textarea, select, checkbox, radio, multi-select

## Quick Start

### 1. Define attributes on your view

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

### 2. Bind inputs with dj-model

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

That is it. Every time an input value changes, djust sends an `update_model` event to the server, the attribute is updated, and the view re-renders.

## API Reference

### ModelBindingMixin

Mix into your LiveView to enable the `update_model` event handler. This mixin is automatically available on all LiveViews.

#### Class Attributes

| Attribute | Type | Default | Description |
|-----------|------|---------|-------------|
| `allowed_model_fields` | `list` or `None` | `None` | Restrict which fields can be bound. `None` allows all non-forbidden fields. |

```python
class MyView(LiveView):
    allowed_model_fields = ['search_query', 'category', 'sort_by']
```

#### Security

The mixin enforces these security rules:
- **Private fields** - Attributes starting with `_` cannot be set.
- **Forbidden fields** - `template_name`, `request`, `kwargs`, `args`, `session`, `use_actors`, `temporary_assigns`, and internal fields are blocked.
- **Existing attributes only** - Only attributes that already exist on the view can be updated. New attributes cannot be created via `dj-model`.
- **allowed_model_fields** - If set, only listed fields can be bound.

#### Type Coercion

The mixin automatically coerces incoming string values to match the existing attribute's type:

| Existing Type | Input | Result |
|---------------|-------|--------|
| `bool` | `"true"`, `"1"`, `"yes"`, `"on"` | `True` |
| `bool` | `"false"`, `"0"`, `"no"`, `"off"` | `False` |
| `int` | `"42"` | `42` |
| `float` | `"3.14"` | `3.14` |
| `str` | any | unchanged |

### Template Directives

#### `dj-model="field_name"`

Bind an input to a server-side attribute. Syncs on every `input` event (each keystroke for text inputs).

```html
<input type="text" dj-model="username">
<textarea dj-model="description"></textarea>
<select dj-model="country">...</select>
<input type="checkbox" dj-model="agree_terms">
```

#### `dj-model.lazy="field_name"`

Sync on `change` event instead of `input`. For text inputs, this means the value is sent when the input loses focus (blur), not on every keystroke.

```html
<!-- Only sync when user tabs away or clicks elsewhere -->
<input type="text" dj-model.lazy="email">
<textarea dj-model.lazy="bio"></textarea>
```

#### `dj-model.debounce-N="field_name"`

Debounce by N milliseconds. The update is sent only after the user stops typing for N ms. Defaults to 300ms if no number is specified.

```html
<!-- Send after 300ms of inactivity -->
<input type="text" dj-model.debounce-300="search_query">

<!-- Send after 500ms of inactivity -->
<input type="text" dj-model.debounce-500="address">
```

### Supported Input Types

| Input Type | Value Sent | Notes |
|------------|-----------|-------|
| `<input type="text">` | `el.value` (string) | |
| `<textarea>` | `el.value` (string) | |
| `<select>` | `el.value` (string) | |
| `<select multiple>` | Array of selected values | |
| `<input type="checkbox">` | `el.checked` (boolean) | Also listens on `change` |
| `<input type="radio">` | Value of checked radio in group | Uses `name` attribute for grouping |
| `<input type="number">` | `el.value` (string, coerced server-side) | |
| `<input type="range">` | `el.value` (string, coerced server-side) | |

## Examples

### Search-as-you-Type

```python
class ProductSearch(LiveView):
    template_name = 'product_search.html'

    def mount(self, request, **kwargs):
        self.query = ""
        self.results = []

    def get_context_data(self, **kwargs):
        if self.query and len(self.query) >= 2:
            self.results = Product.objects.filter(
                name__icontains=self.query
            )[:20]
        else:
            self.results = []
        return {
            'query': self.query,
            'results': self.results,
        }
```

```html
<!-- Debounce to avoid excessive server calls -->
<input type="text"
       dj-model.debounce-300="query"
       placeholder="Search products...">

<div class="results">
    {% for product in results %}
        <div class="result-item">
            <strong>{{ product.name }}</strong>
            <span>${{ product.price }}</span>
        </div>
    {% empty %}
        {% if query %}
            <p>No results for "{{ query }}"</p>
        {% endif %}
    {% endfor %}
</div>
```

### Form with Validation

```python
class RegistrationForm(LiveView):
    template_name = 'register.html'

    def mount(self, request, **kwargs):
        self.username = ""
        self.email = ""
        self.password = ""
        self.agree_terms = False
        self.errors = {}

    def get_context_data(self, **kwargs):
        self.validate()
        return {
            'username': self.username,
            'email': self.email,
            'agree_terms': self.agree_terms,
            'errors': self.errors,
            'is_valid': not self.errors and self.username and self.email,
        }

    def validate(self):
        self.errors = {}
        if self.username and len(self.username) < 3:
            self.errors['username'] = "Must be at least 3 characters"
        if self.email and '@' not in self.email:
            self.errors['email'] = "Invalid email address"
        if self.password and len(self.password) < 8:
            self.errors['password'] = "Must be at least 8 characters"
```

```html
<form dj-submit="register">
    <div class="field">
        <label>Username</label>
        <input type="text" dj-model.debounce-300="username"
               value="{{ username }}">
        {% if errors.username %}
            <span class="error">{{ errors.username }}</span>
        {% endif %}
    </div>

    <div class="field">
        <label>Email</label>
        <input type="email" dj-model.lazy="email"
               value="{{ email }}">
        {% if errors.email %}
            <span class="error">{{ errors.email }}</span>
        {% endif %}
    </div>

    <div class="field">
        <label>Password</label>
        <input type="password" dj-model="password">
        {% if errors.password %}
            <span class="error">{{ errors.password }}</span>
        {% endif %}
    </div>

    <label>
        <input type="checkbox" dj-model="agree_terms"
               {% if agree_terms %}checked{% endif %}>
        I agree to the terms
    </label>

    <button type="submit" {% if not is_valid %}disabled{% endif %}>
        Register
    </button>
</form>
```

### Multi-Select Filter

```python
class FilterView(LiveView):
    template_name = 'filter.html'

    def mount(self, request, **kwargs):
        self.selected_tags = []
        self.sort_by = "name"

    def get_context_data(self, **kwargs):
        items = Item.objects.all()
        if self.selected_tags:
            items = items.filter(tags__name__in=self.selected_tags)
        items = items.order_by(self.sort_by)
        return {
            'items': items,
            'selected_tags': self.selected_tags,
            'sort_by': self.sort_by,
        }
```

```html
<select dj-model="selected_tags" multiple>
    <option value="python">Python</option>
    <option value="django">Django</option>
    <option value="javascript">JavaScript</option>
</select>

<select dj-model="sort_by">
    <option value="name">Name</option>
    <option value="-created">Newest</option>
    <option value="price">Price</option>
</select>
```

## Best Practices

### Using .lazy for Expensive Operations

Use `dj-model.lazy` when the server operation triggered by the update is expensive (database queries, API calls). This avoids running the operation on every keystroke.

```html
<!-- Good: validates email only on blur -->
<input type="email" dj-model.lazy="email">

<!-- Good: default (input event) for cheap local state -->
<input type="checkbox" dj-model="show_details">
```

### Using .debounce for Search

Use `dj-model.debounce-N` for search inputs where you want real-time feedback but need to limit server calls.

```html
<!-- Good: 300ms debounce for search -->
<input type="text" dj-model.debounce-300="search_query">

<!-- Avoid: raw dj-model on search fires on every keystroke -->
<input type="text" dj-model="search_query">
```

### Restricting Bindable Fields

For security-sensitive views, explicitly list which fields can be bound:

```python
class AdminView(LiveView):
    allowed_model_fields = ['search_query', 'filter_status']

    # These cannot be set via dj-model even if they exist:
    is_admin = False
    user_role = "viewer"
```

### Combining with Event Handlers

`dj-model` works alongside `dj-click`, `dj-submit`, and other event directives. The model binding updates state, and event handlers trigger actions:

```html
<input type="text" dj-model.debounce-300="query">
<button dj-click="search">Search</button>
<form dj-submit="save">
    <input type="text" dj-model="title">
    <button type="submit">Save</button>
</form>
```
