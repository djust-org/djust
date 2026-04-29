---
title: "Forms & Validation"
slug: forms
section: guides
order: 3
level: beginner
description: "Handle form submissions, validation, and user input in djust LiveViews"
---

# Forms

djust handles forms over WebSocket. No page reloads, no JavaScript, no API layer. You write a Python handler, add `dj-submit` to your `<form>`, and it works.

## The Simplest Form

```python
from djust import LiveView
from djust.decorators import event_handler

class TodoView(LiveView):
    template_name = 'todos.html'

    def mount(self, request, **kwargs):
        self.items = []

    @event_handler()
    def add_item(self, title="", **kwargs):
        if title.strip():
            self.items.append(title.strip())
```

```html
<div dj-root dj-view="myapp.views.TodoView">
    <form dj-submit="add_item">
        <input type="text" name="title" placeholder="New item">
        <button type="submit">Add</button>
    </form>

    <ul>
    {% for item in items %}
        <li>{{ item }}</li>
    {% endfor %}
    </ul>
</div>
```

That's it. `dj-submit` prevents the default submit, collects all form fields via `FormData`, and sends them to your handler as keyword arguments. The view re-renders automatically.

## Adding Validation with Django Forms

When you need real validation -- required fields, email formats, custom rules -- use Django's forms system with `FormMixin`:

```python
from django import forms
from djust import LiveView
from djust.forms import FormMixin

class ContactForm(forms.Form):
    name = forms.CharField(max_length=100)
    email = forms.EmailField()
    message = forms.CharField(widget=forms.Textarea)

class ContactView(FormMixin, LiveView):
    template_name = 'contact.html'
    form_class = ContactForm

    def form_valid(self, form):
        send_email(form.cleaned_data)
        self.success_message = "Sent!"

    def form_invalid(self, form):
        self.error_message = "Please fix the errors below."
```

FormMixin gives you `submit_form()` (validates the form), `validate_field()` (validates one field on change), `reset_form()` (clears everything), and `form_valid()`/`form_invalid()` hooks.

### The Template

Write your HTML however you want. No CSS framework required:

```html
<div dj-root dj-view="myapp.views.ContactView">
    {% if success_message %}<p>{{ success_message }}</p>{% endif %}
    {% if error_message %}<p>{{ error_message }}</p>{% endif %}

    <form dj-submit="submit_form">
        {% csrf_token %}

        <label>Name</label>
        <input type="text" name="name" value="{{ form_data.name }}"
               dj-change="validate_field">
        {% if field_errors.name %}<span>{{ field_errors.name.0 }}</span>{% endif %}

        <label>Email</label>
        <input type="email" name="email" value="{{ form_data.email }}"
               dj-change="validate_field">
        {% if field_errors.email %}<span>{{ field_errors.email.0 }}</span>{% endif %}

        <label>Message</label>
        <textarea name="message"
                  dj-change="validate_field">{{ form_data.message }}</textarea>
        {% if field_errors.message %}<span>{{ field_errors.message.0 }}</span>{% endif %}

        <button type="submit">Send</button>
    </form>
</div>
```

`dj-change="validate_field"` validates that field when the user tabs away. Errors appear instantly without a full form submission.

### Or Skip the Manual HTML

If you don't want to write each field by hand, use `as_live()`:

```html
<form dj-submit="submit_form">
    {% csrf_token %}
    {{ form_instance.as_live }}
    <button type="submit">Send</button>
</form>
```

This auto-renders all fields with labels, error display, and validation bindings. Configure the output style in settings:

```python
DJUST_CSS_FRAMEWORK = "bootstrap5"  # or "tailwind", "plain"
```

You can also render individual fields: `{{ form_instance.as_live_field:"email" }}`

## How It Works

When a `dj-submit` form is submitted:

1. Browser default submit is prevented
2. All fields are collected via `FormData`
3. Data is sent to the server as event params: `{name: "...", email: "..."}`
4. If using FormMixin, `submit_form()` validates with your Django Form
5. `form_valid()` or `form_invalid()` is called
6. The view re-renders with updated state

## Real-Time Validation

`dj-change` fires on the `change` event (blur for text inputs, selection for dropdowns/checkboxes):

```html
<input type="email" name="email" value="{{ form_data.email }}"
       dj-change="validate_field">
```

When the user leaves the field, djust sends `validate_field(field_name="email", value="user@example.com")`. The field is validated against the Django Form, and errors update instantly.

For validation on focus loss specifically, use `dj-blur`:

```html
<input type="text" name="username" value="{{ form_data.username }}"
       dj-blur="validate_field">
```

## FormMixin State

FormMixin initializes these in `mount()`, all available in your template:

| Attribute       | Type   | Purpose                                    |
| --------------- | ------ | ------------------------------------------ |
| `form_data`     | `dict` | Current field values (keyed by field name) |
| `field_errors`  | `dict` | Per-field errors: `{field: [errors]}`      |
| `form_errors`   | `list` | Non-field errors from `clean()`            |
| `is_valid`      | `bool` | Result of last `submit_form()`             |
| `form_instance` | `Form` | Current Django Form instance               |

## Displaying Errors

Per-field errors:

```html
{% if field_errors.email %}
    {% for error in field_errors.email %}
        <span>{{ error }}</span>
    {% endfor %}
{% endif %}
```

Non-field errors (from your form's `clean()` method):

```html
{% if form_errors %}
    {% for error in form_errors %}
        <p>{{ error }}</p>
    {% endfor %}
{% endif %}
```

Style these however fits your app. djust has no opinion on your CSS.

## Editing Existing Records

For ModelForms, set `_model_instance` before `super().mount()`:

```python
from django import forms
from .models import Article

class ArticleForm(forms.ModelForm):
    class Meta:
        model = Article
        fields = ['title', 'body', 'category']

class ArticleEditView(FormMixin, LiveView):
    template_name = 'article_form.html'
    form_class = ArticleForm

    def mount(self, request, pk=None, **kwargs):
        if pk:
            self._model_instance = Article.objects.get(pk=pk)
        super().mount(request, **kwargs)

    def form_valid(self, form):
        form.save()
        self.success_message = "Saved!"
```

FormMixin populates `form_data` from the instance automatically. The template is the same pattern -- `value="{{ form_data.title }}"` etc.

## Form Reset

Clear the form back to its initial state:

```python
@event_handler()
def submit_and_reset(self, **kwargs):
    self.submit_form(**kwargs)
    if self.is_valid:
        self.reset_form()
```

Or let users reset manually:

```html
<button type="button" dj-click="reset_form">Clear</button>
```

## Confirmation Dialogs

Add `dj-confirm` to show a browser confirmation before the action fires:

```html
<form dj-submit="delete_account"
      dj-confirm="This will permanently delete your account. Are you sure?">
    <button type="submit">Delete Account</button>
</form>
```

## `dj-model` vs `dj-submit`

`dj-model` syncs a field value to a Python attribute on every change. `dj-submit` collects all fields and sends them on submit.

| Use Case                    | Approach                |
| --------------------------- | ----------------------- |
| Search / filters / toggles  | `dj-model`              |
| Data entry with validation  | `dj-submit` + FormMixin |
| Multi-field forms with save | `dj-submit`             |

Example with `dj-model` for a live filter:

```python
class FilterView(LiveView):
    template_name = 'filter.html'

    def mount(self, request, **kwargs):
        self.search = ""
        self.category = "all"

    def get_context_data(self, **kwargs):
        qs = Product.objects.all()
        if self.search:
            qs = qs.filter(name__icontains=self.search)
        if self.category != "all":
            qs = qs.filter(category=self.category)
        return {'products': qs}
```

```html
<input type="text" dj-model.debounce-300="search" placeholder="Search...">
<select dj-model="category">
    <option value="all">All</option>
    <option value="electronics">Electronics</option>
</select>
```

See the [Model Binding guide](model-binding) for details.

## Inline Radio Buttons

Django's default `RadioSelect` renders each choice on its own line (vertical list). For segmented controls, filter pills, toolbar choices, or short Yes/No fields, you usually want them inline. djust ships a small opt-in CSS helper that does this without you writing any new Python:

```python
class FilterForm(forms.Form):
    status = forms.ChoiceField(
        widget=forms.RadioSelect(attrs={"data-dj-inline": "true"}),
        choices=[("all", "All"), ("open", "Open"), ("closed", "Closed")],
    )
```

In your base template, after djust's `client.js`, link the form helper stylesheet:

```html
{% load static %}
<link rel="stylesheet" href="{% static 'djust/djust-forms.css' %}">
```

**That's it.** Django's widget mechanics put `attrs={...}` onto each `<input type="radio">`, so the rendered HTML looks like:

```html
<ul>
  <li><label><input type="radio" name="status" value="all" data-dj-inline="true"> All</label></li>
  <li><label><input type="radio" name="status" value="open" data-dj-inline="true"> Open</label></li>
  ...
</ul>
```

The bundled CSS uses the `:has()` parent selector to walk up from the marked input and lay out the containing `<ul>` (or `<div>` if you're using djust-theming's form templates) as inline-flex. Result: full keyboard navigation, native focus ring preserved, no extra Python required. Browser support: Chromium 105+, Safari 15.4+, Firefox 121+ — all stable since 2023.

### Why a `data-` attribute and not a custom widget?

Three reasons:

- **Zero new Python.** `RadioSelect(attrs={...})` is already supported by Django; we just document the attribute name.
- **Composes with anything.** Works with `forms.Form`, `LiveViewForm`, third-party form libraries, ModelForms, Django admin — anything that renders a `RadioSelect`.
- **Skip-able.** Don't want our CSS? Don't link the file. Want different styling? Write your own rule on `[data-dj-inline]` — the contract is the attribute name, not the visual treatment.

### Customizing the visual treatment

Override the bundled rules in your own stylesheet (loaded after `djust-forms.css`):

```css
ul[data-dj-inline] {
    /* Replace the default flex with a CSS Grid for fixed columns: */
    display: grid;
    grid-template-columns: repeat(3, 1fr);
}

/* Or turn it into a segmented-control: */
ul[data-dj-inline] > li > label {
    border: 1px solid #ccc;
    padding: 0.4em 0.8em;
    border-radius: 4px;
}
ul[data-dj-inline] > li > label:has(input:checked) {
    background: #1e88e5;
    color: white;
}
```

The `[data-dj-inline]` selector is the documented contract. The default styling is a starting point.

### Multiple inline fields on one form

Just add the attribute to each field's widget:

```python
class FilterForm(forms.Form):
    status = forms.ChoiceField(
        widget=forms.RadioSelect(attrs={"data-dj-inline": "true"}),
        choices=STATUS_CHOICES,
    )
    priority = forms.ChoiceField(
        widget=forms.RadioSelect(attrs={"data-dj-inline": "true"}),
        choices=PRIORITY_CHOICES,
    )
```

Each `<ul>` gets the attribute independently. No form-level config, no class hierarchy.

## Tips

- **Always include `{% csrf_token %}`** inside `dj-submit` forms (needed for HTTP fallback).
- **Use `dj-change="validate_field"`** on fields for instant feedback before submission.
- **Set `_model_instance` before `super().mount()`** when editing existing records.
- **Keep `form_data` keys consistent.** FormMixin initializes all field keys in `mount()`. Don't add or remove keys -- it breaks VDOM diffing.
- **Use `form_errors` for cross-field validation.** Errors from `clean()` go to `form_errors`, per-field errors go to `field_errors`.
