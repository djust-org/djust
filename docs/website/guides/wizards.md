---
title: "Multi-Step Form Wizards"
slug: wizards
section: guides
order: 4
level: intermediate
description: "Build guided multi-step form flows with WizardMixin — step navigation, per-step validation, and data collection over WebSocket"
---

# Multi-Step Form Wizards

`WizardMixin` manages step navigation, per-step validation, and data collection for guided multi-step form flows. Everything runs over WebSocket with no page reloads.

## Quick Start

### 1. Define Your Forms

```python
from django import forms

class PersonalInfoForm(forms.Form):
    first_name = forms.CharField(max_length=100)
    last_name = forms.CharField(max_length=100)
    email = forms.EmailField()

class AddressForm(forms.Form):
    street = forms.CharField(max_length=200)
    city = forms.CharField(max_length=100)
    state = forms.CharField(max_length=2)
    zip_code = forms.CharField(max_length=10)
```

### 2. Create the LiveView

```python
from djust import WizardMixin, LiveView

class SignupWizard(WizardMixin, LiveView):
    template_name = "signup/wizard.html"

    wizard_steps = [
        {"name": "personal", "title": "Personal Info", "form_class": PersonalInfoForm},
        {"name": "address",  "title": "Address",       "form_class": AddressForm},
        {"name": "review",   "title": "Review & Submit"},  # no form — informational
    ]

    def on_wizard_complete(self, step_data):
        # step_data = {
        #   "personal": {"first_name": "Rosa", "last_name": "Mendez", "email": "..."},
        #   "address":  {"street": "...", "city": "...", "state": "...", "zip_code": "..."},
        # }
        User.objects.create(
            first_name=step_data["personal"]["first_name"],
            last_name=step_data["personal"]["last_name"],
            email=step_data["personal"]["email"],
        )
```

Place `WizardMixin` **before** `LiveView` in the class definition so its methods take priority.

### 3. Build the Template

```html
{% load live_tags %}

<div dj-root dj-view="myapp.views.SignupWizard">

    {# Step indicator #}
    <div class="steps">
        {% for step in steps %}
            <span class="{% if step.is_current %}active{% endif %}
                         {% if step.is_completed %}completed{% endif %}">
                {{ step.title }}
            </span>
        {% endfor %}
    </div>

    {# Progress bar #}
    <div class="progress-bar" style="width: {{ progress_percent }}%"></div>

    {# Current step form fields #}
    {% for name, html in field_html.items %}
        <div class="field">
            {{ html|safe }}
            {% if step_errors %}
                {% for error in step_errors.name %}
                    <span class="error">{{ error }}</span>
                {% endfor %}
            {% endif %}
        </div>
    {% endfor %}

    {# Navigation buttons #}
    <div class="wizard-nav">
        {% if can_go_back %}
            <button dj-click="prev_step">Back</button>
        {% endif %}

        {% if is_last_step %}
            <button dj-click="submit_wizard">Submit</button>
        {% else %}
            <button dj-click="next_step">Continue</button>
        {% endif %}
    </div>
</div>
```

That's it. The wizard handles validation, navigation, and data collection automatically.

## How It Works

1. `mount()` initializes step state (index, data, errors, completed steps)
2. User fills in fields -- `dj-change="validate_field"` validates on blur
3. User clicks "Continue" -- `next_step` validates the current step, advances if valid
4. User clicks "Back" -- `prev_step` goes back without validation (data is preserved)
5. On the last step, "Submit" calls `submit_wizard` which re-validates ALL steps, then calls `on_wizard_complete()`

## Event Handlers

All handlers are available via `dj-click` or `dj-submit` in templates:

| Handler              | Description                                                |
| -------------------- | ---------------------------------------------------------- |
| `next_step`          | Validate current step, advance if valid                    |
| `prev_step`          | Go back one step (no validation, data preserved)           |
| `go_to_step`         | Jump to a completed step (`data-step_index="N"`)           |
| `update_step_field`  | Store a single field value (`data-field="name"`)           |
| `validate_field`     | Validate a field on change (used by `as_live_field()`)     |
| `submit_wizard`      | Validate all steps, call `on_wizard_complete()` if valid   |

### Jumping to a Step

Users can click on completed steps in the step indicator:

```html
{% for step in steps %}
    {% if step.is_completed %}
        <button dj-click="go_to_step" data-step_index="{{ step.index }}">
            {{ step.title }}
        </button>
    {% endif %}
{% endfor %}
```

`go_to_step` only allows jumping to completed steps or the current step -- users cannot skip ahead past unvalidated steps.

## Template Context

Every render provides these variables:

### Step State

| Variable            | Type   | Description                               |
| ------------------- | ------ | ----------------------------------------- |
| `current_step`      | dict   | `{name, title, index}` of current step    |
| `total_steps`       | int    | Total number of steps                     |
| `progress_percent`  | int    | `floor(index / total * 100)`              |
| `steps`             | list   | All steps with `is_current` / `is_completed` flags |

### Navigation Flags

| Variable            | Type | Description                            |
| ------------------- | ---- | -------------------------------------- |
| `can_go_back`       | bool | True if not on first step              |
| `can_go_forward`    | bool | True if current step is completed      |
| `is_first_step`     | bool | True if on step 0                      |
| `is_last_step`      | bool | True if on the final step              |

### Form Data

| Variable            | Type | Description                                         |
| ------------------- | ---- | --------------------------------------------------- |
| `form_data`         | dict | `{field_name: value}` for current step              |
| `form_required`     | dict | `{field_name: bool}` required flags                 |
| `form_choices`      | dict | `{field_name: [{value, label}]}` for choice fields  |
| `field_html`        | dict | `{field_name: SafeString}` pre-rendered widget HTML |
| `step_errors`       | dict | `{field_name: [errors]}` for current step           |
| `step_data`         | dict | All data across all steps                           |

Choice fields are also exposed as flat top-level variables (e.g. `status_choices`, `borough_choices`) for convenience.

## Pre-Rendered Field HTML

The Rust template engine cannot call Python methods with arguments, so form fields must be pre-rendered in Python. `WizardMixin` does this automatically via `as_live_field()`:

```html
{# These are pre-rendered HTML strings with dj-change bindings #}
{{ field_html.first_name|safe }}
{{ field_html.email|safe }}
```

Each field includes `dj-change="validate_field"` and `data-field="<name>"` attributes for real-time validation.

You can also render fields manually in `get_context_data()`:

```python
def get_context_data(self, **kwargs):
    context = super().get_context_data(**kwargs)
    # Custom event handler for a specific field
    context["custom_field"] = self.as_live_field("email", event_name="check_email")
    return context
```

## Informational Steps (No Form)

Steps without a `form_class` are informational -- they always pass validation and are useful for review or confirmation pages:

```python
wizard_steps = [
    {"name": "info",    "title": "Your Info",    "form_class": InfoForm},
    {"name": "review",  "title": "Review"},       # no form_class
    {"name": "confirm", "title": "Confirmation"}, # no form_class
]
```

On review steps, display the collected data:

```html
{% if current_step.name == "review" %}
    <h3>Please review your information:</h3>
    <p>Name: {{ step_data.info.first_name }} {{ step_data.info.last_name }}</p>
    <p>Email: {{ step_data.info.email }}</p>
{% endif %}
```

## Persisting Data

Override `on_wizard_complete()` to save the collected data:

```python
def on_wizard_complete(self, step_data):
    from django.db import transaction

    with transaction.atomic():
        user = User.objects.create(
            first_name=step_data["personal"]["first_name"],
            last_name=step_data["personal"]["last_name"],
            email=step_data["personal"]["email"],
        )
        Address.objects.create(
            user=user,
            street=step_data["address"]["street"],
            city=step_data["address"]["city"],
        )
```

**Important:** `step_data` contains raw string values, not Django `cleaned_data`. Parse dates, numbers, etc. yourself:

```python
def on_wizard_complete(self, step_data):
    from datetime import date

    birth_date = date.fromisoformat(step_data["personal"]["date_of_birth"])
```

This is intentional. Django's `cleaned_data` contains Python objects (`datetime.date`, `Decimal`) that are not JSON-serializable. Since djust serializes all public state to JSON between WebSocket events, storing `cleaned_data` would corrupt the data on the next event.

## Security

`submit_wizard` re-validates ALL previous steps before calling `on_wizard_complete()`. This guards against tampered WebSocket event replays -- template-level button visibility (`{% if is_last_step %}`) is not a security boundary because `dj-click` events can be sent directly over the WebSocket.

If re-validation fails for any step, the wizard navigates back to the failing step and shows the errors.

## Step Definition Reference

Each step in `wizard_steps` is a dict with these keys:

| Key          | Required | Type        | Description                    |
| ------------ | -------- | ----------- | ------------------------------ |
| `name`       | yes      | str         | Unique step identifier         |
| `title`      | no       | str         | Human-readable step title      |
| `form_class` | no       | Form class  | Django Form for this step      |

Steps without `form_class` are informational and always pass validation.

## Combining with Other Mixins

`WizardMixin` composes with other djust mixins:

```python
from djust import WizardMixin, LiveView
from djust.auth import LoginRequiredMixin

class SecureWizard(LoginRequiredMixin, WizardMixin, LiveView):
    login_required = True
    wizard_steps = [...]
```

Place `WizardMixin` before `LiveView` and after auth mixins in the MRO.
