# LiveForm Validation

djust provides a standalone `LiveForm` class for declarative form validation with real-time inline feedback — no Django Form required. Inspired by Phoenix LiveView changesets.

## Core Concepts

- **`LiveForm`** — Standalone validation with declarative rules
- **Built-in validators** — required, email, min_length, max_length, pattern, etc.
- **Custom validators** — Lambda functions for complex rules
- **Real-time validation** — Validate on blur/change with `dj-change`
- **`live_form_from_model()`** — Auto-generate forms from Django models

## Basic Usage

```python
from djust import LiveView
from djust.forms import LiveForm
from djust.decorators import event_handler

class ContactView(LiveView):
    template_name = "contact.html"

    def mount(self, request, **kwargs):
        self.form = LiveForm({
            "name": {"required": True, "min_length": 2},
            "email": {"required": True, "email": True},
            "message": {"required": True, "min_length": 10, "max_length": 500},
        })
        self.submitted = False

    @event_handler
    def validate(self, field=None, value=None, **kwargs):
        """Real-time validation on blur/change"""
        self.form.validate_field(field, value)

    @event_handler
    def submit_form(self, **kwargs):
        """Full form submission"""
        self.form.set_values(kwargs)
        if self.form.validate_all():
            # Process form data
            send_contact_email(
                self.form.data["name"],
                self.form.data["email"],
                self.form.data["message"]
            )
            self.submitted = True
            self.form.reset()
```

```html
<!-- contact.html -->
<form dj-submit="submit_form">
    <div class="field">
        <label>Name</label>
        <input name="name" 
               value="{{ form.data.name }}"
               dj-change="validate"
               dj-debounce="300"
               class="{% if form.errors.name %}error{% endif %}" />
        {% if form.errors.name %}
            <span class="error-text">{{ form.errors.name }}</span>
        {% endif %}
    </div>

    <div class="field">
        <label>Email</label>
        <input name="email" 
               type="email"
               value="{{ form.data.email }}"
               dj-change="validate"
               dj-debounce="300"
               class="{% if form.errors.email %}error{% endif %}" />
        {% if form.errors.email %}
            <span class="error-text">{{ form.errors.email }}</span>
        {% endif %}
    </div>

    <div class="field">
        <label>Message</label>
        <textarea name="message"
                  dj-change="validate"
                  dj-debounce="300"
                  class="{% if form.errors.message %}error{% endif %}"
        >{{ form.data.message }}</textarea>
        {% if form.errors.message %}
            <span class="error-text">{{ form.errors.message }}</span>
        {% endif %}
    </div>

    <button type="submit" {% if not form.valid %}disabled{% endif %}>
        Send Message
    </button>
</form>

{% if submitted %}
    <div class="success">Thank you! Your message has been sent.</div>
{% endif %}
```

## LiveForm API

### Constructor

```python
form = LiveForm(fields, initial=None)
```

| Argument | Type | Description |
|----------|------|-------------|
| `fields` | `dict` | Mapping of field names to rule dicts |
| `initial` | `dict` | Optional initial field values |

### Properties

| Property | Type | Description |
|----------|------|-------------|
| `form.data` | `dict` | Current field values |
| `form.errors` | `dict` | Current errors (field → message) |
| `form.valid` | `bool` | True if no errors and all required fields pass |

### Methods

| Method | Description |
|--------|-------------|
| `validate_field(name, value)` | Validate one field, update state |
| `validate_all()` | Validate all fields, returns bool |
| `set_values(values)` | Bulk set values (no validation) |
| `reset()` | Clear all values and errors |

## Built-in Validators

| Rule | Example | Description |
|------|---------|-------------|
| `required` | `{"required": True}` | Field must have a non-empty value |
| `min_length` | `{"min_length": 3}` | Minimum string length |
| `max_length` | `{"max_length": 100}` | Maximum string length |
| `pattern` | `{"pattern": r"^\d{5}$"}` | Regex pattern match |
| `email` | `{"email": True}` | Valid email format |
| `url` | `{"url": True}` | Valid URL format |
| `min` | `{"min": 18}` | Minimum numeric value |
| `max` | `{"max": 120}` | Maximum numeric value |
| `choices` | `{"choices": ["a", "b"]}` | Value must be in list |

### Example with All Validators

```python
self.form = LiveForm({
    "username": {
        "required": True,
        "min_length": 3,
        "max_length": 20,
        "pattern": r"^[a-zA-Z0-9_]+$",  # alphanumeric + underscore
    },
    "email": {
        "required": True,
        "email": True,
    },
    "website": {
        "url": True,  # Optional, but must be valid URL if provided
    },
    "age": {
        "required": True,
        "min": 18,
        "max": 120,
    },
    "role": {
        "required": True,
        "choices": ["admin", "user", "guest"],
    },
})
```

## Custom Validators

Add custom validation logic with the `validators` key — a list of functions that take a value and return an error string or `None`.

```python
def no_spam(value):
    if value and "buy now" in value.lower():
        return "Spam detected in message"
    return None

def unique_username(value):
    if value and User.objects.filter(username=value).exists():
        return "Username already taken"
    return None

self.form = LiveForm({
    "username": {
        "required": True,
        "min_length": 3,
        "validators": [unique_username],
    },
    "message": {
        "required": True,
        "validators": [no_spam],
    },
    "bio": {
        "max_length": 500,
        "validators": [
            lambda v: "No links allowed" if v and "http" in v else None,
        ],
    },
})
```

Custom validators run **after** built-in validators. The first error wins.

## Real-Time Validation

Use `dj-change` with `dj-debounce` for live inline validation:

```html
<input name="email" 
       dj-change="validate"
       dj-debounce="300" />
```

The `dj-change` event fires on:
- `blur` for text inputs (when user tabs away)
- `change` for selects/checkboxes

```python
@event_handler
def validate(self, field=None, value=None, **kwargs):
    # field = "email", value = "user@example.com"
    error = self.form.validate_field(field, value)
    # error is None if valid, or error message string
```

## Generate Form from Django Model

Use `live_form_from_model()` to auto-generate validation rules from a Django model's field definitions:

```python
from djust.forms import live_form_from_model
from myapp.models import Contact

class ContactFormView(LiveView):
    def mount(self, request, **kwargs):
        self.form = live_form_from_model(
            Contact,
            exclude=["id", "created_at", "updated_at"],
            initial={"status": "pending"}
        )
```

**Inferred rules:**

| Model Field | Generated Rules |
|-------------|-----------------|
| `blank=False` | `required: True` |
| `max_length=N` | `max_length: N` |
| `EmailField` | `email: True` |
| `URLField` | `url: True` |
| `IntegerField` with validators | `min`, `max` |
| `choices=[...]` | `choices: [values]` |

### Example Model

```python
# models.py
from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator

class Contact(models.Model):
    name = models.CharField(max_length=100)  # required, max_length=100
    email = models.EmailField()  # required, email=True
    age = models.IntegerField(
        validators=[MinValueValidator(18), MaxValueValidator(120)]
    )  # min=18, max=120
    status = models.CharField(
        max_length=20,
        choices=[("pending", "Pending"), ("approved", "Approved")],
        blank=True
    )  # choices=["pending", "approved"]
```

```python
# Generates equivalent to:
LiveForm({
    "name": {"required": True, "max_length": 100},
    "email": {"required": True, "email": True},
    "age": {"min": 18, "max": 120},
    "status": {"choices": ["pending", "approved"]},
})
```

## Django FormMixin (Alternative)

If you prefer Django's built-in forms, use `FormMixin`:

```python
from djust.forms import FormMixin
from django import forms

class ContactForm(forms.Form):
    name = forms.CharField(max_length=100)
    email = forms.EmailField()
    message = forms.CharField(widget=forms.Textarea)

class ContactView(FormMixin, LiveView):
    form_class = ContactForm
    template_name = "contact.html"

    def form_valid(self, form):
        # form.cleaned_data is available
        send_email(form.cleaned_data)
        self.success_message = "Sent!"

    def form_invalid(self, form):
        self.error_message = "Please fix the errors"
```

## Complete Example: Registration Form

```python
# views.py
from djust import LiveView
from djust.forms import LiveForm
from djust.decorators import event_handler
from django.contrib.auth.models import User
from django.contrib.auth.hashers import make_password

class RegistrationView(LiveView):
    template_name = "register.html"

    def mount(self, request, **kwargs):
        self.form = LiveForm({
            "username": {
                "required": True,
                "min_length": 3,
                "max_length": 30,
                "pattern": r"^[a-zA-Z0-9_]+$",
                "validators": [self.username_available],
            },
            "email": {
                "required": True,
                "email": True,
                "validators": [self.email_available],
            },
            "password": {
                "required": True,
                "min_length": 8,
                "validators": [self.password_strength],
            },
            "password_confirm": {
                "required": True,
                "validators": [self.passwords_match],
            },
        })
        self.registered = False

    def username_available(self, value):
        if value and User.objects.filter(username=value).exists():
            return "Username is already taken"
        return None

    def email_available(self, value):
        if value and User.objects.filter(email=value).exists():
            return "Email is already registered"
        return None

    def password_strength(self, value):
        if not value:
            return None
        if not any(c.isupper() for c in value):
            return "Must contain at least one uppercase letter"
        if not any(c.isdigit() for c in value):
            return "Must contain at least one number"
        return None

    def passwords_match(self, value):
        if value and value != self.form.data.get("password"):
            return "Passwords do not match"
        return None

    @event_handler
    def validate(self, field=None, value=None, **kwargs):
        self.form.validate_field(field, value)

    @event_handler
    def register(self, **kwargs):
        self.form.set_values(kwargs)
        if self.form.validate_all():
            User.objects.create(
                username=self.form.data["username"],
                email=self.form.data["email"],
                password=make_password(self.form.data["password"]),
            )
            self.registered = True
            self.form.reset()
```

```html
<!-- register.html -->
{% if registered %}
    <div class="success">Registration successful! You can now log in.</div>
{% else %}
<form dj-submit="register">
    {% for field in 'username,email,password,password_confirm'.split(',') %}
    <div class="field {% if form.errors|get:field %}has-error{% endif %}">
        <label>{{ field|title|replace:'_',' ' }}</label>
        <input name="{{ field }}"
               type="{% if 'password' in field %}password{% else %}text{% endif %}"
               value="{{ form.data|get:field }}"
               dj-change="validate"
               dj-debounce="500" />
        {% if form.errors|get:field %}
            <span class="error">{{ form.errors|get:field }}</span>
        {% endif %}
    </div>
    {% endfor %}

    <button type="submit" {% if not form.valid %}disabled{% endif %}>
        Create Account
    </button>
</form>
{% endif %}
```

## Tips

1. **Debounce validation** — Use `dj-debounce="300"` to avoid validating on every keystroke
2. **Show errors on blur** — `dj-change` fires on blur for text inputs
3. **Disable submit button** — Use `{% if not form.valid %}disabled{% endif %}`
4. **Clear form on success** — Call `self.form.reset()` after successful submission
5. **Async validators** — For database checks, consider debouncing longer (500ms+)
