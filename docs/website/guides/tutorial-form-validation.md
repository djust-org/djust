---
title: "Tutorial: Real-time form validation with FormMixin"
slug: tutorial-form-validation
section: guides
order: 40
level: intermediate
description: "Build a sign-up form that validates each field as the user types — username availability, password strength, email format — using Django Forms inside FormMixin. Errors appear inline within ~80 ms; no full-form re-renders, no JavaScript validation library."
---

# Tutorial: Real-time form validation with FormMixin

Forms are the highest-friction surface in any app. Every extra
re-render, every laggy error message, every "click submit and find
out" round-trip is a cost the user pays. The dream UX is:
field-level validation that fires as the user types, errors that
appear inline next to the bad input, the submit button enabled
only when the whole form is valid, and a server-side guarantee
that the data is correct before persisting.

djust gets you there with **`FormMixin`** — wraps a normal Django
`Form` so its validation runs over the WebSocket on every field
change. No JS validation library duplicating server rules. No
"my client and server validation drifted." One `Form` class is
the source of truth.

By the end of this tutorial you'll have:

- A sign-up form with **username, email, password** fields.
- **Username availability** checked as the user types (debounced
  300 ms) — red "taken" / green "available" inline.
- **Password strength** rated as the user types (uses Django's
  built-in `password_validation`).
- **Email format** validated on blur, not on every keystroke
  (avoids "alice@" looking invalid mid-typing).
- The **submit button disabled** until every field is filled in
  and error-free.

| You'll learn | Documented in |
|---|---|
| `FormMixin` + `form_class`, `form_valid()` | [Forms](forms.md) |
| The built-in `validate_field` / `submit_form` handlers | [Forms](forms.md) |
| `form_data` / `field_errors` template variables | [Forms overview](../forms/index.md#formmixin-template-variables) |
| `dj-input` vs `dj-change` (keystroke vs blur) | [Events](../core-concepts/events.md) |
| `dj-debounce` to throttle DB-touching validators | This tutorial |

> **Prerequisites:** [Your First LiveView](../getting-started/first-liveview.md), familiarity
> with Django Forms ([docs](https://docs.djangoproject.com/en/stable/topics/forms/))
> and the [search-as-you-type tutorial](tutorial-search-as-you-type.md)
> (sets up the debouncing vocabulary).

---

## Step 1 — The Django form (your single source of truth)

<!-- The imports are correct Django; the checker's minimal settings
     cannot load django.contrib.auth.models. -->
<!-- doc-snippet-check: skip -->
```python
# myapp/forms.py
from django import forms
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError


class SignUpForm(forms.Form):
    username = forms.CharField(min_length=3, max_length=30)
    email    = forms.EmailField()
    password = forms.CharField(widget=forms.PasswordInput, min_length=8)

    def clean_username(self):
        username = self.cleaned_data["username"]
        if not username.isalnum():
            raise ValidationError("Username must be letters and digits only.")
        if User.objects.filter(username__iexact=username).exists():
            raise ValidationError("That username is taken.")
        return username

    def clean_password(self):
        password = self.cleaned_data["password"]
        try:
            validate_password(password)  # Django's configured validators
        except ValidationError as e:
            raise ValidationError(list(e.messages))
        return password
```

Field validators are regular Django `clean_<field>` methods. **The
same code runs on per-keystroke validation, on blur, and on full-form
submit.** (Per-field validation runs the field's own checks and its
`clean_<field>()`; the form-wide `clean()` runs on submit.) That's the
contract: write the validation once; the framework picks the right
moment to run it.

> **No "email already registered" check here.** A live validator that
> answers "an account with that email exists" lets anyone test
> addresses against your user table, one keystroke at a time. The
> email field checks only the *format* live (`EmailField` does that);
> Step 2 handles an existing address on submit without revealing it.
> Usernames are different: they are usually public (they appear on
> profiles and in URLs), so the live "That username is taken" check is
> a deliberate trade-off. If your usernames are private, move that
> check out of `clean_username()` in the same way.

---

## Step 2 — The view, with FormMixin

<!-- Imports django.contrib.auth.models, which the checker's minimal
     settings cannot load. -->
<!-- doc-snippet-check: skip -->
```python
# myapp/views.py
from django.contrib.auth.models import User
from django.core.mail import send_mail

from djust import LiveView
from djust.decorators import event_handler, rate_limit
from djust.forms import FormMixin

from .forms import SignUpForm


class SignUpView(FormMixin, LiveView):
    template_name = "signup.html"
    form_class = SignUpForm

    # FormMixin's built-in handlers, re-declared only to rate-limit them:
    # both run database queries for anonymous visitors.
    @event_handler
    @rate_limit(rate=5, burst=20, on_exceed="drop")
    def validate_field(self, **kwargs):
        super().validate_field(**kwargs)

    @event_handler
    @rate_limit(rate=0.2, burst=5)  # 5 tries, then one every 5 seconds
    def submit_form(self, **kwargs):
        super().submit_form(**kwargs)

    def form_valid(self, form):
        # submit_form() already ran form.is_valid() — every clean_*
        # method again, on the whole form — before calling this.
        data = form.cleaned_data
        if User.objects.filter(email__iexact=data["email"]).exists():
            # Answer exactly as for a new address, so the form can't be
            # used to find out who has an account. Tell the owner instead.
            send_mail(
                "Sign-up attempt with your email address",
                "Someone tried to create an account with this address. "
                "If it was you, sign in or reset your password instead.",
                None,
                [data["email"]],
            )
        else:
            User.objects.create_user(
                username=data["username"],
                email=data["email"],
                password=data["password"],
            )
        # reset_form() clears the fields AND the messages, so reset
        # first and set the message after.
        self.reset_form()
        self.success_message = f"Thanks! Check {data['email']} to finish signing up."

    def form_invalid(self, form):
        self.error_message = "Please fix the errors below."

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["can_submit"] = not self.field_errors and all(
            self.form_data.get(name) for name in ("username", "email", "password")
        )
        return context
```

That's the whole view. `FormMixin` supplies the rest:

1. **`validate_field`** — a built-in event handler. Point `dj-input`
   or `dj-change` at it; it validates the one field that changed
   (named by the input's `name` attribute) and records any error.
2. **`submit_form`** — the built-in handler for `dj-submit`. It
   validates the whole form, then calls `form_valid(form)` or
   `form_invalid(form)`.
3. **Template variables** — `form_data` (current values),
   `field_errors` (`{field: [messages]}`), `form_errors` (non-field
   errors), `is_valid` (result of the last submit),
   `success_message` and `error_message`. The Django form instance
   itself stays in Python (`self.form_instance`); it is not in the
   template.
4. **`reset_form()`** — clears the values and errors after a
   successful sign-up.

The two overrides only add `@rate_limit` to the built-in handlers.
`validate_field` uses `on_exceed="drop"`, so a fast typist loses an
occasional check instead of their connection. `submit_form` keeps the
default, which counts refusals toward djust's abuse disconnect: sign-up
is a brute-force target. An existing email gets the same success
message as a new one; the owner gets an email instead of an account.

`can_submit` is ours: `is_valid` only reflects the last submit, so
the "enable the button" rule is computed from the live field state.

---

## Step 3 — The template

```html
<!-- myapp/templates/signup.html -->
<form dj-root dj-submit="submit_form" novalidate>
  {% csrf_token %}
  <fieldset>
    <legend>Create an account</legend>

    {# username — validates on every keystroke (debounced 300ms) #}
    <label class="field {% if field_errors.username %}is-invalid{% elif form_data.username %}is-valid{% endif %}">
      Username
      <input
        type="text"
        name="username"
        value="{{ form_data.username }}"
        dj-input="validate_field"
        dj-debounce="300"
        autocomplete="username"
        required
      />
      {% for err in field_errors.username %}
        <span class="err">{{ err }}</span>
      {% endfor %}
    </label>

    {# email — validates on blur (dj-change) so partial input doesn't flash red #}
    <label class="field {% if field_errors.email %}is-invalid{% elif form_data.email %}is-valid{% endif %}">
      Email
      <input
        type="email"
        name="email"
        value="{{ form_data.email }}"
        dj-change="validate_field"
        autocomplete="email"
        required
      />
      {% for err in field_errors.email %}
        <span class="err">{{ err }}</span>
      {% endfor %}
    </label>

    {# password — validates on keystroke for live strength feedback #}
    <label class="field {% if field_errors.password %}is-invalid{% elif form_data.password %}is-valid{% endif %}">
      Password
      <input
        type="password"
        name="password"
        dj-input="validate_field"
        dj-debounce="200"
        autocomplete="new-password"
        required
      />
      {% for err in field_errors.password %}
        <span class="err">{{ err }}</span>
      {% endfor %}
    </label>
  </fieldset>

  <button type="submit" {% if not can_submit %}disabled{% endif %}
          dj-form-pending="disabled">
    <span dj-form-pending="hide">Sign up</span>
    <span dj-form-pending="show" hidden>Creating account&hellip;</span>
  </button>

  {% if success_message %}
    <p role="status" class="ok">{{ success_message }}</p>
  {% endif %}
  {% if error_message %}
    <p role="alert" class="err">{{ error_message }}</p>
  {% endif %}
  {% for err in form_errors %}
    <p role="alert" class="err">{{ err }}</p>
  {% endfor %}
</form>
```

Three deliberate choices:

| Choice | Why |
|---|---|
| `dj-input="validate_field"` on username + password | Live feedback — user sees "available" / strength meter as they type. |
| `dj-change="validate_field"` on email | Email is "blur to validate" because `alice@` shouldn't flash red mid-typing. `dj-change` fires when the input loses focus or the user hits Enter. |
| `dj-debounce="300"` on username | Username validation hits the DB (uniqueness check). Without debounce, every keystroke would `SELECT 1 FROM auth_user WHERE username=?`. 300ms cuts that to ~1 query per "settled typing pause." (Text inputs already debounce `dj-input` by 300 ms; the attribute makes it explicit.) |

The `is-invalid` / `is-valid` class hooks let CSS style the
field state (red border, green border, neutral border).

---

## Step 4 — A bit of CSS

```css
.field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-bottom: 1rem;
}
.field input {
  padding: 8px 10px;
  border: 1px solid var(--color-border, #d1d5db);
  border-radius: 4px;
  transition: border-color 0.15s, box-shadow 0.15s;
}
.field.is-invalid input {
  border-color: #dc2626;
  box-shadow: 0 0 0 1px #dc2626;
}
.field.is-valid input {
  border-color: #10b981;
}
.err {
  color: #dc2626;
  font-size: 12px;
}
.ok {
  margin-top: 1rem;
  padding: 12px;
  background: #d1fae5;
  border-left: 3px solid #10b981;
  color: #065f46;
}
button[disabled] {
  opacity: 0.5;
  cursor: not-allowed;
}
```

The `transition` on the input border makes the red/green flip
animate smoothly rather than snap. Subtle but it makes the form
feel polished.

---

## Step 5 — Try it

Visit `/signup/`:

1. Type `ab` in username → red "Ensure this value has at least 3 characters (it has 2)."
2. Type `abc` → field flips green (assuming `abc` isn't taken).
3. Type `admin` → red "That username is taken."
4. Type `alice@` in email — no error yet (didn't blur).
5. Tab out of email → red "Enter a valid email address." An address
   that is already registered does NOT go red.
6. Type `pass` in password → red "Ensure this value has at least 8
   characters (it has 4)." The field's `min_length` runs before
   `clean_password()`. Type `password` → red "This password is too
   common." from Django's `validate_password`.
7. Submit button stays disabled until all three fields are
   filled in and green. Click → `submit_form` validates the whole
   form, `form_valid` creates the user (or emails the owner of an
   existing address), and the same success message renders either
   way.

Each per-field validation: ~80 ms round-trip. With debouncing,
the DB sees one validation per pause, not one per keystroke. The
user gets near-real-time feedback without ever feeling the
network.

---

## Why this beats client-side validation libraries

The classic alternative is a JS validation library (Yup, Zod,
react-hook-form, etc.) that runs in the browser. It's faster
(no round-trip) but it has a fatal flaw: **the rules drift from
the server**. Username uniqueness can't run client-side at all
(no DB access). Password strength rules differ between
client (lenient) and server (strict). Email validation regexes
disagree on edge cases.

You end up with two implementations of "valid": one in JS that
the user sees, one in Python that the API enforces. They drift,
and the user sees "looks valid" then "rejected on submit" —
which is the worst possible UX.

`FormMixin` collapses both into one Django `Form` class. The
"client validation" is just the server validating fast. The
80 ms round-trip is real but it's tolerable for the consistency
guarantee.

---

## When to NOT use FormMixin

| The form is… | Use |
|---|---|
| Login form (one shot, no live validation needed) | Plain `LiveView` + `@event_handler def login(...)` |
| 50-field admin config (validation on submit only) | Plain Django form view (not LiveView) |
| Real-time validation with DB checks | **`FormMixin`** |
| Real-time validation, no DB hits, no auth | Could be either; `FormMixin` is still simplest |

The win of `FormMixin` is the live-feedback UX. If you don't
need that — login forms, settings pages where submit is fine —
plain `@event_handler` with one `clean()` pass on submit is
simpler.

---

## What just happened, end to end

```
   Browser                                 Server
       │                                       │
       │ user types "abc" in username          │
       │ ── 300ms debounce ─────────────────► validate_field(field="username", value="abc")
       │                                       │   form = SignUpForm(form_data)
       │                                       │   username field clean + validators
       │                                       │     → clean_username runs
       │                                       │     → User.objects.filter(...).exists() → False
       │                                       │   field_errors has no "username" key
       │ ◄ patch: field flips green ──────────│
       │                                       │
       │ user types "admin"                    │
       │ ── 300ms debounce ─────────────────► validate_field(field="username", value="admin")
       │                                       │   clean_username
       │                                       │     → User.objects.filter("admin").exists() → True
       │                                       │   field_errors["username"] = ["That username is taken."]
       │ ◄ patch: red border + error msg ─────│
       │                                       │
       │ user fixes username + email + password│
       │ all three fields green                │
       │ submit button enables                 │
       │                                       │
       │ click Submit                          │
       │ ─────────────────────────────────────► submit_form()
       │                                       │   form.is_valid() — full re-validation
       │                                       │   form_valid(form):
       │                                       │     User.objects.create_user(...)
       │                                       │     self.reset_form()
       │                                       │     self.success_message = "Welcome..."
       │ ◄ patch: success message; form reset ─│
```

One `Form` class, two render paths (per-field on type/blur, full
on submit). Same validation rules at every step.

---

## Where to go next

- **Async / external-API validators:** if a validator needs to
  hit a slow external service (e.g. "is this domain registered
  with us"), run that check in `start_async()` from
  the [streaming AI tutorial](tutorial-streaming-ai.md).
  Show a tiny spinner next to the field while the validator
  runs.
- **Multi-step forms:** combine `FormMixin` per-step with the
  [multi-step wizard tutorial](tutorial-multi-step-wizard.md)'s
  cursor pattern, or use `WizardMixin` ([Wizards](wizards.md)).
  Each step is a separate `Form` class.
- **CSRF protection:** events over the WebSocket are covered by
  the connection's origin check; keep `{% csrf_token %}` in the
  form, as above, so the HTTP fallback transport can send the
  token.
- **File fields:** uploads have their own transport; handle them
  with `UploadMixin` as in the
  [uploads tutorial](tutorial-file-uploads-progress.md) rather
  than a `forms.FileField`.
- **Real-time strength meters:** beyond Django's
  `password_validation`, drop in [zxcvbn](https://github.com/dropbox/zxcvbn)
  via a [hook](tutorial-chart-hook.md) for a graphical
  meter. The hook reads the input value, computes the strength
  client-side (no validation drift; zxcvbn just produces UX
  hints), updates the meter as the user types.

The five-line shape — `class MyForm(forms.Form): clean_<field>(self): ...`
plus `class MyView(FormMixin, LiveView): form_class = MyForm` —
is the entire surface. Once you've written one validated form,
the next ten are mostly more `clean_*` methods.
