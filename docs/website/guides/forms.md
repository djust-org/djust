---
title: "Forms & Validation"
slug: forms
section: guides
order: 3
level: beginner
description: "Build reactive forms with real-time validation, Django Forms integration, and automatic error handling"
---

# Forms & Validation

djust gives you reactive forms powered by Django's forms system. Fields validate as users type, errors appear instantly, and submissions are handled without page reloads -- all with standard Django Forms.

## What You Get

- **`dj-submit`** -- Handle form submission over WebSocket (no page reload)
- **`FormMixin`** -- Plug Django Forms into LiveView with real-time validation
- **`validate_field`** -- Per-field validation triggered on change
- **`form_valid` / `form_invalid`** -- Hooks called after submission
- **`as_live()`** -- Auto-render forms with Bootstrap or Tailwind styling
- **Form value preservation** -- User input survives server re-renders

## Quick Start

### 1. Create a Django Form

```python
from django import forms

class ContactForm(forms.Form):
    name = forms.CharField(max_length=100)
    email = forms.EmailField()
    message = forms.CharField(widget=forms.Textarea)
```

### 2. Create a LiveView with FormMixin

```python
from djust import LiveView
from djust.forms import FormMixin

class ContactView(FormMixin, LiveView):
    template_name = 'contact.html'
    form_class = ContactForm

    def form_valid(self, form):
        send_email(form.cleaned_data)
        self.success_message = "Message sent!"

    def form_invalid(self, form):
        self.error_message = "Please fix the errors below."
```

### 3. Build the Template

```html
<div dj-root dj-view="myapp.views.ContactView">
    {% if success_message %}
        <div class="alert alert-success">{{ success_message }}</div>
    {% endif %}

    {% if error_message %}
        <div class="alert alert-danger">{{ error_message }}</div>
    {% endif %}

    <form dj-submit="submit_form">
        {% csrf_token %}

        <div class="mb-3">
            <label for="id_name">Name</label>
            <input type="text" name="name" id="id_name"
                   value="{{ form_data.name }}"
                   dj-change="validate_field">
            {% if field_errors.name %}
                <div class="text-danger">{{ field_errors.name.0 }}</div>
            {% endif %}
        </div>

        <div class="mb-3">
            <label for="id_email">Email</label>
            <input type="email" name="email" id="id_email"
                   value="{{ form_data.email }}"
                   dj-change="validate_field">
            {% if field_errors.email %}
                <div class="text-danger">{{ field_errors.email.0 }}</div>
            {% endif %}
        </div>

        <div class="mb-3">
            <label for="id_message">Message</label>
            <textarea name="message" id="id_message"
                      dj-change="validate_field">{{ form_data.message }}</textarea>
            {% if field_errors.message %}
                <div class="text-danger">{{ field_errors.message.0 }}</div>
            {% endif %}
        </div>

        <button type="submit">Send</button>
    </form>
</div>
```

That is the complete pattern. `dj-submit` collects all form fields and sends them to `submit_form()`. `dj-change` triggers per-field validation as the user fills out the form.

## How `dj-submit` Works

When a form with `dj-submit` is submitted:

1. The browser's default submit is prevented
2. All form fields are collected via `FormData`
3. The data is sent to the server as event params: `{name: "...", email: "...", message: "..."}`
4. `FormMixin.submit_form()` validates using your Django Form
5. `form_valid()` or `form_invalid()` is called
6. The view re-renders with updated state

```html
<form dj-submit="submit_form">
    <input type="text" name="title">
    <input type="number" name="quantity">
    <select name="category">
        <option value="a">A</option>
        <option value="b">B</option>
    </select>
    <button type="submit">Create</button>
</form>
```

You can also use a custom handler name instead of the default `submit_form`:

```html
<form dj-submit="save_draft">
```

```python
from djust.decorators import event_handler

@event_handler()
def save_draft(self, **form_data):
    # form_data contains all input values: {title: "...", quantity: "5", ...}
    form = MyForm(data=form_data)
    if form.is_valid():
        form.save(commit=False)
        self.draft_saved = True
```

## Real-Time Field Validation

FormMixin provides `validate_field()` which validates a single field when it changes. Wire it up with `dj-change`:

```html
<input type="email" name="email"
       value="{{ form_data.email }}"
       dj-change="validate_field">
```

When the user tabs away from the email field, djust sends `validate_field(field_name="email", value="user@example.com")` to the server. The field is validated against the Django Form, and any errors appear immediately.

### How It Works

1. `dj-change` fires on `change` event (blur for text inputs, selection for dropdowns)
2. The field name and value are sent automatically
3. `FormMixin.validate_field()` runs Django's field validators and custom `clean_<field>()` methods
4. Errors are stored in `self.field_errors[field_name]`
5. The view re-renders, showing the error next to the field

### Validation with `dj-blur`

For validation that fires specifically on focus loss:

```html
<input type="text" name="username"
       value="{{ form_data.username }}"
       dj-blur="validate_field">
```

## FormMixin State

FormMixin initializes these attributes in `mount()`:

| Attribute | Type | Purpose |
|-----------|------|---------|
| `form_data` | `dict` | Current field values (keyed by field name) |
| `field_errors` | `dict` | Per-field error messages: `{field: [errors]}` |
| `form_errors` | `list` | Non-field errors from `form.non_field_errors()` |
| `is_valid` | `bool` | Result of last `submit_form()` validation |
| `success_message` | `str` | Set in `form_valid()` for user feedback |
| `error_message` | `str` | Set in `form_invalid()` for user feedback |
| `form_instance` | `Form` | Current Django Form instance |

All of these are available in your template context.

## Displaying Errors

### Field Errors

```html
{% if field_errors.email %}
    <div class="invalid-feedback">
        {% for error in field_errors.email %}
            <div>{{ error }}</div>
        {% endfor %}
    </div>
{% endif %}
```

### Non-Field Errors

Non-field errors come from your form's `clean()` method:

```html
{% if form_errors %}
    <div class="alert alert-danger">
        {% for error in form_errors %}
            <div>{{ error }}</div>
        {% endfor %}
    </div>
{% endif %}
```

### Error Styling

Add conditional CSS classes based on validation state:

```html
<input type="text" name="email"
       class="form-control {% if field_errors.email %}is-invalid{% endif %}"
       value="{{ form_data.email }}"
       dj-change="validate_field">
```

## Edit Forms (ModelForm)

For editing existing records, set `_model_instance` before calling `super().mount()`:

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
        article = form.save()
        self.success_message = "Article updated!"
        self.redirect_url = f"/articles/{article.pk}/"
```

For the template, populate field values from the model instance:

```html
<form dj-submit="submit_form">
    {% csrf_token %}
    <input type="text" name="title"
           value="{{ form_data.title }}"
           dj-change="validate_field">

    <textarea name="body"
              dj-change="validate_field">{{ form_data.body }}</textarea>

    <select name="category" dj-change="validate_field">
        {% for value, label in categories %}
            <option value="{{ value }}"
                    {% if form_data.category == value %}selected{% endif %}>
                {{ label }}
            </option>
        {% endfor %}
    </select>

    <button type="submit">Save</button>
</form>
```

## Form Reset

Call `reset_form()` to clear the form back to its initial state:

```python
@event_handler()
def submit_and_reset(self, **kwargs):
    self.submit_form(**kwargs)
    if self.is_valid:
        self.reset_form()
```

```html
<button dj-click="reset_form" type="button">Clear Form</button>
```

`reset_form()` re-initializes all field keys with their default values, ensuring VDOM state stays consistent. It also sets `_should_reset_form = True` to signal the client to clear input values.

## Automatic Form Rendering with `as_live()`

Instead of writing each field manually, use `as_live()` to auto-render the entire form with your CSS framework:

```html
<form dj-submit="submit_form">
    {% csrf_token %}
    {{ form_instance.as_live }}
    <button type="submit">Submit</button>
</form>
```

Or render individual fields:

```html
<form dj-submit="submit_form">
    {% csrf_token %}
    {{ form_instance.as_live_field:"name" }}
    {{ form_instance.as_live_field:"email" }}
    <button type="submit">Submit</button>
</form>
```

### Supported Frameworks

djust includes adapters for Bootstrap 5, Tailwind CSS, and plain HTML. Configure in settings:

```python
# settings.py
DJUST_CSS_FRAMEWORK = "bootstrap5"  # or "tailwind", "plain"
```

The adapter handles labels, error display, help text, CSS classes, and validation event bindings automatically.

## Manual Forms (Without FormMixin)

You do not need FormMixin. Use `dj-submit` with a plain event handler for full control:

```python
from djust import LiveView
from djust.decorators import event_handler

class QuickAddView(LiveView):
    template_name = 'quick_add.html'

    def mount(self, request, **kwargs):
        self.items = []
        self.error = ""

    @event_handler()
    def add_item(self, name="", quantity="", **kwargs):
        if not name:
            self.error = "Name is required"
            return
        try:
            qty = int(quantity)
        except ValueError:
            self.error = "Quantity must be a number"
            return

        self.items.append({"name": name, "quantity": qty})
        self.error = ""
```

```html
<form dj-submit="add_item">
    {% if error %}<p class="text-danger">{{ error }}</p>{% endif %}

    <input type="text" name="name" placeholder="Item name">
    <input type="number" name="quantity" value="1">
    <button type="submit">Add</button>
</form>

<ul>
{% for item in items %}
    <li>{{ item.name }} (x{{ item.quantity }})</li>
{% endfor %}
</ul>
```

## Forms with `dj-model`

For simple forms where you want two-way binding without explicit submission, use `dj-model` (see the [Model Binding guide](model-binding)):

```python
class FilterView(LiveView):
    template_name = 'filter.html'

    def mount(self, request, **kwargs):
        self.search = ""
        self.category = "all"
        self.min_price = 0

    def get_context_data(self, **kwargs):
        qs = Product.objects.all()
        if self.search:
            qs = qs.filter(name__icontains=self.search)
        if self.category != "all":
            qs = qs.filter(category=self.category)
        if self.min_price:
            qs = qs.filter(price__gte=self.min_price)
        return {'products': qs}
```

```html
<input type="text" dj-model.debounce-300="search" placeholder="Search...">
<select dj-model="category">
    <option value="all">All</option>
    <option value="electronics">Electronics</option>
</select>
<input type="number" dj-model.lazy="min_price" placeholder="Min price">
```

**When to use `dj-model` vs `dj-submit`**:

| Use Case | Approach |
|----------|----------|
| Search / filter controls | `dj-model` |
| Toggle switches, checkboxes | `dj-model` |
| Data entry with validation | `dj-submit` + `FormMixin` |
| Multi-field forms with save | `dj-submit` |
| Forms requiring Django validation | `dj-submit` + `FormMixin` |

## Confirmation Dialogs

Add `dj-confirm` to show a browser confirmation dialog before submitting:

```html
<form dj-submit="delete_account" dj-confirm="This will permanently delete your account. Are you sure?">
    <button type="submit">Delete Account</button>
</form>
```

## Full Example: CRUD Form

A complete create/edit form with real-time validation and error handling:

```python
from django import forms
from djust import LiveView
from djust.forms import FormMixin
from djust.decorators import event_handler
from .models import Lease

class LeaseForm(forms.ModelForm):
    class Meta:
        model = Lease
        fields = ['property', 'tenant', 'start_date', 'end_date',
                  'monthly_rent', 'terms']

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get('start_date')
        end = cleaned.get('end_date')
        if start and end and end <= start:
            raise forms.ValidationError("End date must be after start date.")
        return cleaned

class LeaseFormView(FormMixin, LiveView):
    template_name = 'lease_form.html'
    form_class = LeaseForm

    def mount(self, request, lease_id=None, **kwargs):
        if lease_id:
            self._model_instance = Lease.objects.get(pk=lease_id)
        super().mount(request, **kwargs)
        self.properties = list(Property.objects.values('pk', 'name'))
        self.tenants = list(Tenant.objects.values('pk', 'name'))

    def form_valid(self, form):
        lease = form.save()
        self.success_message = "Lease saved!"

    def form_invalid(self, form):
        self.error_message = "Please correct the errors below."

    @event_handler()
    def cancel(self, **kwargs):
        self.redirect_url = "/leases/"
```

```html
<div dj-root dj-view="leases.views.LeaseFormView">
    {% if success_message %}
        <div class="alert alert-success">{{ success_message }}</div>
    {% endif %}
    {% if error_message %}
        <div class="alert alert-danger">{{ error_message }}</div>
    {% endif %}
    {% if form_errors %}
        <div class="alert alert-danger">
            {% for error in form_errors %}
                <p>{{ error }}</p>
            {% endfor %}
        </div>
    {% endif %}

    <form dj-submit="submit_form">
        {% csrf_token %}

        <div class="row">
            <div class="col-md-6 mb-3">
                <label>Property</label>
                <select name="property" dj-change="validate_field"
                        class="form-select {% if field_errors.property %}is-invalid{% endif %}">
                    <option value="">Select...</option>
                    {% for prop in properties %}
                        <option value="{{ prop.pk }}"
                                {% if form_data.property == prop.pk %}selected{% endif %}>
                            {{ prop.name }}
                        </option>
                    {% endfor %}
                </select>
                {% if field_errors.property %}
                    <div class="invalid-feedback">{{ field_errors.property.0 }}</div>
                {% endif %}
            </div>

            <div class="col-md-6 mb-3">
                <label>Monthly Rent</label>
                <input type="number" step="0.01" name="monthly_rent"
                       value="{{ form_data.monthly_rent }}"
                       dj-change="validate_field"
                       class="form-control {% if field_errors.monthly_rent %}is-invalid{% endif %}">
                {% if field_errors.monthly_rent %}
                    <div class="invalid-feedback">{{ field_errors.monthly_rent.0 }}</div>
                {% endif %}
            </div>
        </div>

        <div class="mb-3">
            <label>Terms</label>
            <textarea name="terms" rows="4"
                      dj-change="validate_field"
                      class="form-control {% if field_errors.terms %}is-invalid{% endif %}"
                      >{{ form_data.terms }}</textarea>
            {% if field_errors.terms %}
                <div class="invalid-feedback">{{ field_errors.terms.0 }}</div>
            {% endif %}
        </div>

        <div class="d-flex gap-2">
            <button type="submit" class="btn btn-primary">Save Lease</button>
            <button type="button" dj-click="cancel" class="btn btn-outline-secondary">Cancel</button>
            <button type="button" dj-click="reset_form" class="btn btn-outline-danger">Reset</button>
        </div>
    </form>
</div>
```

## Best Practices

- **Always include `{% csrf_token %}`** inside `dj-submit` forms. The CSRF token is sent automatically in the HTTP fallback path.
- **Use `dj-change="validate_field"`** on fields for instant feedback. Users see errors before they submit.
- **Set `_model_instance` before `super().mount()`** when editing. FormMixin uses it to populate initial `form_data`.
- **Keep form_data keys consistent.** FormMixin initializes all field keys to empty strings in `mount()`. If you modify `form_data` manually, keep the same keys to avoid VDOM diff issues.
- **Use `dj-model` for filters, `dj-submit` for data entry.** `dj-model` is great for live search and toggles. `dj-submit` with FormMixin is better for forms that need Django validation.
- **Use `form_errors` for cross-field validation.** Errors from `Form.clean()` go into `form_errors` (non-field errors), while per-field errors go into `field_errors`.
