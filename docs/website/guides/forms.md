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
<div dj-root>
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

> **Strict parameter policy.** Under the opt-in strict parameter policy (`@event_handler(parameter_policy="strict")`, ADR-036), the browser sends a generated value only if the handler declares a parameter with that name, or has a `**` catch-all. Generated values are `value` and `field` for `dj-input`/`dj-change`, the form fields for `dj-submit`, and `key`/`code` for keyboard events. `dj-value-*` arguments are always sent, and one that reuses a generated name is rejected. `_target` is never sent: use `field` or a `dj-value-*` argument. Legacy handlers (the default) receive every value as before. A strict `dj-submit` handler therefore gets only the fields it names, or all of them through `**form_data`.


## HTTP fallback does not require a second view

Use semantic HTML forms such as `<form dj-submit="submit_form">` with Django
Form classes and `FormMixin`. Validation and form state do not require a separate
traditional Django POST view: `FormMixin` supplies the decorated `submit_form`
event handler.

When WebSocket is unavailable, the djust browser client can send a
CSRF-protected JSON event to the current LiveView URL using `fetch`. The inherited
`LiveView.post()` dispatches the decorated event handler and returns JSON. Include
`{% csrf_token %}` inside the form for this native HTTP event transport. No
application `post()` override or duplicate form endpoint is needed.

This transport still requires JavaScript. Supporting a browser with JavaScript
disabled through ordinary URL-encoded or multipart form submissions is a separate,
optional product requirement. If you deliberately support both protocols, ensure
JSON events still reach `super().post()`: an override that returns HTML for them
breaks the client's JSON response handling.

File uploads have their own transport and lifecycle; JSON event fallback does not
by itself provide every binary-upload capability. See the [upload guide](uploads.md) for the
transport supported by your upload configuration.


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
<div dj-root>
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

If you don't want to write each field by hand, use the view's `as_live()` method. It belongs to `FormMixin` (not to the Django form), so render it in `get_context_data` and put the HTML in the context:

```python
from django.utils.safestring import mark_safe

class ContactView(FormMixin, LiveView):
    form_class = ContactForm
    template_name = "contact.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_html"] = mark_safe(self.as_live())
        return context
```

```html
<form dj-submit="submit_form">
    {% csrf_token %}
    {{ form_html }}
    <button type="submit">Send</button>
</form>
```

This auto-renders all fields with labels, error display, and validation bindings. Configure the output style in settings:

```python
LIVEVIEW_CONFIG = {"css_framework": "bootstrap5"}  # or "bootstrap4", "tailwind", "plain", None
```

You can also render a single field with `self.as_live_field("email")` the same way.

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

When the user leaves the field, djust sends `validate_field(field="email", value="user@example.com")`. The field is validated against the Django Form, and errors update instantly.

> **Overriding `validate_field`?** The field name arrives as **`field`**, not `field_name` — the client hardcodes that key and reads it from `data-field`, the element's `name`, then its `id`. `field_name` is accepted as a backwards-compatible alias, so `def validate_field(self, field="", value=None, **kwargs)` is the shape to write. This guide previously documented `field_name` as the parameter, which produced overrides that ran on every keystroke and silently received nothing (#2137).

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
| `form_errors`   | `dict \| ErrorList` | Non-field errors from `clean()`: an empty dict until an invalid submit, then a list |
| `is_valid`      | `bool` | Result of last `submit_form()`             |
| `success_message` | `str` | Success message your view sets (empty by default) |
| `error_message` | `str`  | Error message your view sets (empty by default) |
| `form_choices`  | `dict` | `(value, label)` pairs per choice field, keyed by field name |

The Django form instance is available in Python as `self.form_instance`; it is not in the template context.

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

## Editing one record with `ModelFormMixin`

**Available from djust 1.3** (not in the 1.3.0rc1 pre-release).

`djust.forms.ModelFormMixin` edits one existing record with no `mount()`
override. The URL route supplies the record's `pk` (or `slug`). djust looks
the record up and checks access before it builds the form, and checks again
on every event:

<!-- djust-example: article-edit scenario=model-form-edit -->
```python
from django import forms
from djust import LiveView
from djust.forms import ModelFormMixin
from .models import Article

class ArticleForm(forms.ModelForm):
    class Meta:
        model = Article
        fields = ["title", "body"]

class ArticleEditView(ModelFormMixin[Article], LiveView):
    template_name = "article_edit.html"
    model = Article
    form_class = ArticleForm
    login_required = True

    def get_queryset(self):
        # Only the signed-in user's articles can be opened.
        return super().get_queryset().filter(author=self.request.user)

    def form_valid(self, form):
        self.object = form.save()
        self.success_message = "Saved!"
```

Route it with the record's id:

<!-- djust-example: skip -- the URL route for article-edit; its scenario reads it from the section -->
```python
# urls.py
path("articles/<int:pk>/edit/", ArticleEditView.as_view())
```

The template shows the record as `object` and the form as usual:

```html
<div dj-root>
    <h1>Editing {{ object.title }}</h1>
    <form dj-submit="submit_form">
        {% csrf_token %}
        <input name="title" value="{{ form_data.title }}" dj-change="validate_field">
        {% if field_errors.title %}<span>{{ field_errors.title.0 }}</span>{% endif %}
        <textarea name="body">{{ form_data.body }}</textarea>
        <button type="submit">Save</button>
    </form>
    {% if success_message %}<p>{{ success_message }}</p>{% endif %}
</div>
```

What happens:

- **The record comes from the route, never from the browser.** The lookup
  uses `self.kwargs`: the route's URL kwargs, resolved on the server for
  every transport.
  `pk_url_kwarg`, `slug_url_kwarg`, `slug_field` and `query_pk_and_slug`
  work as in Django's `UpdateView`.
  Navigating to another record's URL without a page load (a `dj-patch`
  link, back/forward, or `live_patch(path=...)`) remounts the view there, so
  the form always edits the record in the address bar.
- **Access is checked before the form exists.** A record missing from
  `get_queryset()` and one refused by `has_object_permission(request, obj)`
  get the same "Access denied" response: HTTP 403, or a `permission_denied`
  error on a live connection. No form is built and no hook runs, so a user
  cannot tell a missing record from a forbidden one.
- **Every event checks again.** If access is revoked or the record is deleted
  while the page is open, the next event is denied before your code runs.
- **`self.object` is the checked record for the current request or event.**
  It renders as `object`; set `context_object_name = "article"` to add a
  second name. It is never saved to the session: only the route identifies
  the record. `self.object = form.save()` keeps it current. Assigning a
  different record raises `ValueError`; link to that record's URL instead.
- **You decide what saving means.** `form_valid()` runs only for a valid
  form, and djust never saves on its own.
- **Scope the records.** Override `get_queryset()` or `has_object_permission()`.
  With neither, any signed-in user who can open the view can edit any record by
  changing the id in the URL; system check `djust.S013` warns about it.

`form_class` must be a `ModelForm` that lists its editable fields. Keep
fields such as `author` out of it, so a user cannot reassign them. For a
create form, keep using `FormMixin` with a `ModelForm`.

### Moving from `_model_instance`

The pattern in [Editing Existing Records](#editing-existing-records) still
works. To move a view to `ModelFormMixin`:

1. Replace `FormMixin` with `ModelFormMixin[YourModel]` and set `model`.
2. Delete the `mount()` code that loads `_model_instance`. The route's `pk`
   selects the record.
3. Move any ownership filter into `get_queryset()`, or a per-record rule into
   `has_object_permission()`.
4. In `form_valid()`, assign the saved record with `self.object = form.save()`.

Don't set `_model_instance` on a `ModelFormMixin` view: that is a
configuration error.

## Form Reset

Clear the form back to its initial state:

```python
@event_handler()
def submit_and_reset(self, **kwargs):
    self.submit_form(**kwargs)
    if self.is_valid:
        self.reset_form()
```

Calling `reset_form()` inside `form_valid()` works too: `submit_form()` does not
copy the submitted values back over a reset. `reset_form()` also clears
`success_message`, so set the message after the reset:

```python
def form_valid(self, form):
    save(form.cleaned_data)
    self.reset_form()
    self.success_message = "Saved!"
```

Or let users reset manually. `reset_form` is an `@event_handler`, so a template can call it directly:

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

**That's it.** Django's widget mechanics put `attrs={...}` onto each `<input type="radio">`, so the rendered HTML (Django 4.0+) looks like:

```html
<div id="id_status">
  <div><label><input type="radio" name="status" value="all" data-dj-inline="true"> All</label></div>
  <div><label><input type="radio" name="status" value="open" data-dj-inline="true"> Open</label></div>
  ...
</div>
```

The attribute sits on each `<input>`, never on the wrapper. The bundled CSS uses the `:has()` parent selector to walk up from the marked input and lay out the containing `<div>` (or `<ul>`, for templates that render a list) as inline-flex. Result: full keyboard navigation, native focus ring preserved, no extra Python required. Browser support: Chromium 105+, Safari 15.4+, Firefox 121+ — all stable since 2023.

### Why a `data-` attribute and not a custom widget?

Three reasons:

- **Zero new Python.** `RadioSelect(attrs={...})` is already supported by Django; we just document the attribute name.
- **Composes with anything.** Works with `forms.Form`, `LiveViewForm`, third-party form libraries, ModelForms, Django admin — anything that renders a `RadioSelect`.
- **Skip-able.** Don't want our CSS? Don't link the file. Want different styling? Write your own rule on `[data-dj-inline]` — the contract is the attribute name, not the visual treatment.

### Customizing the visual treatment

Override the bundled rules in your own stylesheet (loaded after `djust-forms.css`):

```css
:is(ul, div):has(> :is(li, div) > label > input[data-dj-inline]) {
    /* Replace the default flex with a CSS Grid for fixed columns: */
    display: grid;
    grid-template-columns: repeat(3, 1fr);
}

/* Or turn it into a segmented-control: */
label:has(> input[data-dj-inline]) {
    border: 1px solid #ccc;
    padding: 0.4em 0.8em;
    border-radius: 4px;
}
label:has(> input[data-dj-inline]:checked) {
    background: #1e88e5;
    color: white;
}
```

The `[data-dj-inline]` attribute on the radio input is the documented contract. The default styling is a starting point.

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

Each radio input carries the attribute; the CSS finds its wrapper with `:has()`. No form-level config, no class hierarchy.

## Tips

- **Always include `{% csrf_token %}`** inside `dj-submit` forms (needed for the native, CSRF-protected JSON event fallback).
- **Use `dj-change="validate_field"`** on fields for instant feedback before submission.
- **Set `_model_instance` before `super().mount()`** when editing existing records, or from djust 1.3 use [`ModelFormMixin`](#editing-one-record-with-modelformmixin).
- **Keep `form_data` keys consistent.** FormMixin initializes all field keys in `mount()`. Don't add or remove keys -- it breaks VDOM diffing.
- **Use `form_errors` for cross-field validation.** Errors from `clean()` go to `form_errors`, per-field errors go to `field_errors`.

## Markdown editor

See the [Markdown Editor guide](markdown-editor.md) for optional Visual/Markdown editing,
native form integration, asset loading, theme variables and editing limitations.
