# LiveForm Validation

djust provides two approaches to form validation:

1. **`FormMixin`** — Integrates with Django's Form classes for real-time validation
2. **`LiveForm`** — Standalone declarative validation without Django Forms

Both provide live inline feedback as users fill out forms.

## LiveForm — Standalone Validation

`LiveForm` is a lightweight, declarative form validation system inspired by Phoenix LiveView changesets. It doesn't require Django's Form classes.

### Basic Usage

```python
from djust import LiveView, event_handler
from djust.forms import LiveForm

class ContactView(LiveView):
    template_name = "contact.html"

    def mount(self, request, **kwargs):
        self.form = LiveForm({
            "name": {"required": True, "min_length": 2},
            "email": {"required": True, "email": True},
            "message": {"required": True, "max_length": 1000},
        })
        self.success = False

    @event_handler
    def validate(self, field="", value="", **kwargs):
        """Validate a single field on change/blur."""
        self.form.validate_field(field, value)

    @event_handler
    def submit_form(self, **form_data):
        """Validate all and submit."""
        self.form.set_values(form_data)
        if self.form.validate_all():
            # Process the form
            send_contact_email(self.form.data)
            self.success = True
            self.form.reset()
        # If invalid, errors are already in self.form.errors
```

```html
<!-- contact.html -->
<form dj-submit="submit_form">
    {% if success %}
        <div class="alert success">Message sent!</div>
    {% endif %}

    <div class="field">
        <label>Name</label>
        <input name="name" 
               value="{{ form.data.name }}"
               dj-change="validate"
               class="{% if form.errors.name %}error{% endif %}">
        {% if form.errors.name %}
            <span class="error-text">{{ form.errors.name }}</span>
        {% endif %}
    </div>

    <div class="field">
        <label>Email</label>
        <input name="email" type="email"
               value="{{ form.data.email }}"
               dj-change="validate"
               class="{% if form.errors.email %}error{% endif %}">
        {% if form.errors.email %}
            <span class="error-text">{{ form.errors.email }}</span>
        {% endif %}
    </div>

    <div class="field">
        <label>Message</label>
        <textarea name="message"
                  dj-change="validate"
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
```

### Built-in Validators

| Validator | Description | Example |
|-----------|-------------|---------|
| `required` | Field cannot be empty | `{"required": True}` |
| `min_length` | Minimum string length | `{"min_length": 3}` |
| `max_length` | Maximum string length | `{"max_length": 100}` |
| `email` | Valid email format | `{"email": True}` |
| `url` | Valid URL format | `{"url": True}` |
| `pattern` | Regex pattern match | `{"pattern": r"^\d{5}$"}` |
| `min` | Minimum numeric value | `{"min": 0}` |
| `max` | Maximum numeric value | `{"max": 100}` |
| `choices` | Value must be in list | `{"choices": ["a", "b", "c"]}` |

### Combining Validators

Combine multiple rules in a single field definition:

```python
self.form = LiveForm({
    "username": {
        "required": True,
        "min_length": 3,
        "max_length": 20,
        "pattern": r"^[a-zA-Z0-9_]+$",  # alphanumeric + underscore
    },
    "age": {
        "required": True,
        "min": 18,
        "max": 120,
    },
    "country": {
        "required": True,
        "choices": ["US", "CA", "UK", "DE", "FR"],
    },
})
```

### Custom Validators

Add custom validation logic with the `validators` option:

```python
def validate_no_profanity(value):
    """Return error message if invalid, None if valid."""
    bad_words = ["spam", "scam"]
    if value and any(word in value.lower() for word in bad_words):
        return "Please avoid inappropriate language."
    return None

def validate_unique_username(value):
    from myapp.models import User
    if User.objects.filter(username=value).exists():
        return "This username is already taken."
    return None

self.form = LiveForm({
    "username": {
        "required": True,
        "min_length": 3,
        "validators": [validate_unique_username],
    },
    "bio": {
        "max_length": 500,
        "validators": [validate_no_profanity],
    },
})
```

### LiveForm Properties

```python
# Current field values
self.form.data  # {"name": "John", "email": "john@example.com", ...}

# Current errors (field -> first error message)
self.form.errors  # {"email": "Enter a valid email address."}

# Overall validity
self.form.valid  # True if no errors and all required fields have values
```

### LiveForm Methods

```python
# Validate a single field
error = self.form.validate_field("email", "invalid-email")
# Returns error message or None

# Validate all fields at once
is_valid = self.form.validate_all()  # Returns True/False

# Set values without validating
self.form.set_values({"name": "John", "email": "john@example.com"})

# Reset form to empty state
self.form.reset()
```

## live_form_from_model — Generate from Django Model

Automatically create a LiveForm from a Django model's field definitions:

```python
from djust.forms import live_form_from_model
from myapp.models import Contact

class ContactView(LiveView):
    def mount(self, request, **kwargs):
        # Automatically extracts validation rules from model fields
        self.form = live_form_from_model(
            Contact,
            exclude=["id", "created_at"],
            initial={"status": "pending"}
        )
```

The helper extracts:
- `required` from `blank=False`
- `max_length` from CharField
- `email` validation from EmailField
- `url` validation from URLField
- `choices` from field choices
- `min`/`max` from validators

## FormMixin — Django Forms Integration

For existing Django Form classes, use `FormMixin` for real-time validation:

```python
from django import forms
from djust import LiveView, event_handler
from djust.forms import FormMixin

class ContactForm(forms.Form):
    name = forms.CharField(min_length=2)
    email = forms.EmailField()
    message = forms.CharField(widget=forms.Textarea)

class ContactView(FormMixin, LiveView):
    template_name = "contact.html"
    form_class = ContactForm

    def form_valid(self, form):
        """Called when form passes validation."""
        send_email(form.cleaned_data)
        self.success_message = "Message sent!"
        self.reset_form()

    def form_invalid(self, form):
        """Called when form fails validation."""
        self.error_message = "Please fix the errors below."
```

```html
<!-- contact.html -->
<form dj-submit="submit_form">
    {% if success_message %}
        <div class="success">{{ success_message }}</div>
    {% endif %}

    <div class="field">
        <label>Name</label>
        <input name="name" 
               value="{{ form_data.name }}"
               dj-change="validate_field"
               data-field="name">
        {% if field_errors.name %}
            <span class="error">{{ field_errors.name.0 }}</span>
        {% endif %}
    </div>

    <!-- Repeat for other fields... -->

    <button type="submit">Send</button>
</form>
```

### FormMixin Properties

| Property | Description |
|----------|-------------|
| `form_data` | Dict of current field values |
| `field_errors` | Dict of field -> list of errors |
| `form_errors` | Non-field errors |
| `is_valid` | True after successful validation |
| `form_instance` | The Django Form instance |

### FormMixin Methods

| Method | Description |
|--------|-------------|
| `validate_field(field, value)` | Validate a single field |
| `submit_form(**data)` | Validate all and call form_valid/form_invalid |
| `reset_form()` | Reset to initial state |

## Real-Time Validation with dj-change

For instant feedback, use `dj-change` to validate on blur:

```html
<input name="email" 
       dj-change="validate"
       dj-debounce="300">
```

The `dj-change` event fires:
- On `blur` for text inputs
- On `change` for selects, checkboxes, radios

Combine with `dj-debounce` to avoid validating on every keystroke.

## Full Example: Registration Form

```python
# views.py
from djust import LiveView, event_handler
from djust.forms import LiveForm

class RegistrationView(LiveView):
    template_name = "register.html"

    def mount(self, request, **kwargs):
        self.form = LiveForm({
            "username": {
                "required": True,
                "min_length": 3,
                "max_length": 20,
                "pattern": r"^[a-zA-Z0-9_]+$",
                "validators": [self.validate_unique_username],
            },
            "email": {
                "required": True,
                "email": True,
                "validators": [self.validate_unique_email],
            },
            "password": {
                "required": True,
                "min_length": 8,
            },
            "password_confirm": {
                "required": True,
                "validators": [self.validate_passwords_match],
            },
        })
        self.registered = False

    def validate_unique_username(self, value):
        from django.contrib.auth.models import User
        if User.objects.filter(username=value).exists():
            return "Username already taken."
        return None

    def validate_unique_email(self, value):
        from django.contrib.auth.models import User
        if User.objects.filter(email=value).exists():
            return "Email already registered."
        return None

    def validate_passwords_match(self, value):
        if value != self.form.data.get("password"):
            return "Passwords don't match."
        return None

    @event_handler
    def validate(self, field="", value="", **kwargs):
        self.form.validate_field(field, value)

    @event_handler
    def register(self, **form_data):
        self.form.set_values(form_data)
        if self.form.validate_all():
            User.objects.create_user(
                username=self.form.data["username"],
                email=self.form.data["email"],
                password=self.form.data["password"],
            )
            self.registered = True
```

```html
<!-- register.html -->
{% if registered %}
    <div class="success">
        Account created! <a href="/login/">Log in</a>
    </div>
{% else %}
<form dj-submit="register">
    <div class="field">
        <label>Username</label>
        <input name="username" 
               value="{{ form.data.username }}"
               dj-change="validate"
               dj-debounce="500"
               class="{% if form.errors.username %}error{% endif %}">
        {% if form.errors.username %}
            <span class="error-text">{{ form.errors.username }}</span>
        {% endif %}
        <small>3-20 characters, letters, numbers, and underscores only</small>
    </div>

    <div class="field">
        <label>Email</label>
        <input name="email" type="email"
               value="{{ form.data.email }}"
               dj-change="validate"
               dj-debounce="500"
               class="{% if form.errors.email %}error{% endif %}">
        {% if form.errors.email %}
            <span class="error-text">{{ form.errors.email }}</span>
        {% endif %}
    </div>

    <div class="field">
        <label>Password</label>
        <input name="password" type="password"
               dj-change="validate"
               class="{% if form.errors.password %}error{% endif %}">
        {% if form.errors.password %}
            <span class="error-text">{{ form.errors.password }}</span>
        {% endif %}
        <small>Minimum 8 characters</small>
    </div>

    <div class="field">
        <label>Confirm Password</label>
        <input name="password_confirm" type="password"
               dj-change="validate"
               class="{% if form.errors.password_confirm %}error{% endif %}">
        {% if form.errors.password_confirm %}
            <span class="error-text">{{ form.errors.password_confirm }}</span>
        {% endif %}
    </div>

    <button type="submit" {% if not form.valid %}disabled{% endif %}>
        Create Account
    </button>
</form>
{% endif %}
```

## Tips

1. **Debounce async validators**: For validators that hit the database (like uniqueness checks), use `dj-debounce` to avoid excessive queries.

2. **Disable submit until valid**: Use `{% if not form.valid %}disabled{% endif %}` on submit buttons.

3. **Show errors only after interaction**: Track a `touched` state to avoid showing errors before the user interacts with the field.

4. **Custom error messages**: The built-in validators return sensible defaults, but custom validators can return any error message.
