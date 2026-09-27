---
title: "Tutorial: Real-time form validation with FormMixin"
slug: tutorial-form-validation
section: guides
order: 40
level: intermediate
description: "Build a 'create a project' form that validates each field as the user types — slug format and availability, email format on blur, a date-range rule on submit — using a Django Form inside FormMixin. Errors appear inline within ~80 ms; no JavaScript validation library."
---

# Tutorial: Real-time form validation with FormMixin

Forms are the highest-friction surface in any app. Every extra
round-trip ("submit → server error → fix → resubmit") costs you
users. The fix is validation that runs as the user types, with the
same rules the server enforces on submit.

djust gets you there with **`FormMixin`** — wraps a normal Django
`Form` so its validation runs over the WebSocket on every field
change. No JS validation library duplicating server rules. No
"my client and server validation drifted." One `Form` class is
the source of truth.

By the end of this tutorial you'll have a **"create a project"** form
with:

- A **name**, validated as the user types.
- A **URL slug** (`/p/<slug>/`), checked for format and availability
  as the user types (debounced 300 ms) — red "taken" / green
  "available" inline.
- A **contact email**, validated on blur, not on every keystroke
  (avoids "alice@" looking invalid mid-typing).
- A **start and end date**, with a cross-field rule ("the end can't
  be before the start") that runs on submit.
- The **submit button disabled** until the required fields are
  filled in and error-free.

| You'll learn | Documented in |
|---|---|
| `FormMixin` + `form_class`, `form_valid()` | [Forms](forms.md) |
| The built-in `validate_field` / `submit_form` handlers | [Forms](forms.md) |
| `form_data` / `field_errors` / `form_errors` template variables | [Forms overview](../forms/index.md#formmixin-template-variables) |
| `dj-input` vs `dj-change` (keystroke vs blur) | [Events](../core-concepts/events.md) |
| `dj-debounce` to throttle DB-touching validators | This tutorial |
| `@rate_limit` on the built-in handlers | [Decorators](../api-reference/decorators.md) |

> **Prerequisites:** [Your First LiveView](../getting-started/first-liveview.md), familiarity
> with Django Forms ([docs](https://docs.djangoproject.com/en/stable/topics/forms/))
> and the [search-as-you-type tutorial](tutorial-search-as-you-type.md)
> (sets up the debouncing vocabulary).

> **Building sign-up or login?** Don't hand-roll it with this pattern.
> Use djust's [account pages](accounts.md): with the `allauth` backend
> they ship sign-up, email verification by code, password reset and
> rate limits with secure defaults. Verification is easy to get subtly
> wrong — tokens that outlive activation, links a mail scanner clicks
> for the user, someone pre-registering another person's address — and
> the account pages already handle those cases. Use `FormMixin` for
> your own forms, like the one below.

---

## Step 1 — The model and the Django form

<!-- The imports are correct Django; the checker's minimal settings
     cannot load django.contrib.auth. -->
<!-- doc-snippet-check: skip -->
```python
# myapp/models.py
from django.conf import settings
from django.db import models


class Project(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    name = models.CharField(max_length=80)
    slug = models.SlugField(max_length=50, unique=True)  # public URL: /p/<slug>/
    contact_email = models.EmailField(blank=True)
    starts_on = models.DateField(null=True, blank=True)
    ends_on = models.DateField(null=True, blank=True)
```

<!-- Imports the reader's own myapp.models, which the checker cannot load. -->
<!-- doc-snippet-check: skip -->
```python
# myapp/forms.py
from django import forms
from django.core.exceptions import ValidationError

from .models import Project

RESERVED_SLUGS = {"admin", "api", "new", "settings", "static"}


class ProjectForm(forms.Form):
    name = forms.CharField(min_length=3, max_length=80)
    slug = forms.SlugField(max_length=50)
    contact_email = forms.EmailField(required=False)
    starts_on = forms.DateField(required=False)
    ends_on = forms.DateField(required=False)

    def clean_slug(self):
        slug = self.cleaned_data["slug"].lower()
        if slug in RESERVED_SLUGS:
            raise ValidationError("That address is reserved.")
        if Project.objects.filter(slug=slug).exists():
            raise ValidationError("That address is taken.")
        return slug

    def clean(self):
        cleaned = super().clean()
        starts, ends = cleaned.get("starts_on"), cleaned.get("ends_on")
        if starts and ends and ends < starts:
            self.add_error("ends_on", "The end date can't be before the start date.")
        return cleaned
```

Field validators are regular Django `clean_<field>` methods. **The
same code runs on per-keystroke validation, on blur, and on full-form
submit.** Per-field validation runs the field's own checks and its
`clean_<field>()`; the form-wide `clean()` — the date-range rule —
runs on submit, because it needs more than one field. That's the
contract: write the validation once; the framework picks the right
moment to run it.

> **When is a live "taken" check safe?** Here it answers "is this
> public URL free?" — anyone can already find out by visiting
> `/p/<slug>/`, so saying so as the user types reveals nothing. Don't
> use a live existence check where existence itself is private: an
> email address on a sign-up form, a customer number, a private
> repository name. For those, check on submit and answer the same way
> whether or not the value exists.

---

## Step 2 — The view, with FormMixin

<!-- Imports the reader's own myapp modules, which the checker cannot load. -->
<!-- doc-snippet-check: skip -->
```python
# myapp/views.py
from django.db import IntegrityError, transaction

from djust import LiveView
from djust.decorators import event_handler, rate_limit
from djust.forms import FormMixin

from .forms import ProjectForm
from .models import Project


class NewProjectView(FormMixin, LiveView):
    template_name = "new_project.html"
    form_class = ProjectForm
    login_required = True  # projects belong to a user

    # FormMixin's built-in handlers, re-declared only to rate-limit them:
    # the slug check queries the database on every pause in typing.
    @event_handler
    @rate_limit(rate=5, burst=20, on_exceed="drop")
    def validate_field(self, **kwargs):
        super().validate_field(**kwargs)

    @event_handler
    @rate_limit(rate=0.5, burst=5)
    def submit_form(self, **kwargs):
        super().submit_form(**kwargs)

    def form_valid(self, form):
        # submit_form() already ran form.is_valid() — every clean_*
        # method and clean() again, on the whole form — before this.
        data = form.cleaned_data
        try:
            with transaction.atomic():
                Project.objects.create(owner=self.request.user, **data)
        except IntegrityError:
            # Someone took the slug between the live check and submit.
            # The unique constraint is the real guarantee; the live check
            # is only a hint.
            self.field_errors = {"slug": ["That address was just taken. Try another."]}
            self.error_message = "Please fix the errors below."
            return
        # reset_form() clears the fields AND the messages, so reset
        # first and set the message after.
        self.reset_form()
        self.success_message = f"Project “{data['name']}” created at /p/{data['slug']}/."

    def form_invalid(self, form):
        self.error_message = "Please fix the errors below."

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["can_submit"] = not self.field_errors and all(
            self.form_data.get(name) for name in ("name", "slug")
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
   successful create.

The two overrides only add `@rate_limit` to the built-in handlers.
`validate_field` uses `on_exceed="drop"`, so a fast typist loses an
occasional check instead of their connection. `submit_form` keeps the
default, which counts refusals toward djust's abuse disconnect.

**What those limits do and don't stop.** `@rate_limit` keys its bucket
on the caller: the user for a logged-in visitor (as here), the session
for an anonymous one. That caps the work one account or one browser
can cause. On a **public** form it does not stop a script that drops
its cookie and reloads: each fresh session gets a fresh bucket. If you
put a DB-backed live check on a form anonymous visitors can reach, add
a per-IP limit in front of the page as well, at your reverse proxy or
in a middleware.

`can_submit` is ours: `is_valid` only reflects the last submit, so
the "enable the button" rule is computed from the live field state.

---

## Step 3 — The template

```html
<!-- myapp/templates/new_project.html -->
<form dj-root dj-submit="submit_form" novalidate>
  {% csrf_token %}
  <fieldset>
    <legend>New project</legend>

    {# name — validates on every keystroke (debounced 300ms) #}
    <label class="field {% if field_errors.name %}is-invalid{% elif form_data.name %}is-valid{% endif %}">
      Name
      <input type="text" name="name" value="{{ form_data.name }}"
             dj-input="validate_field" required />
      {% for err in field_errors.name %}
        <span class="err">{{ err }}</span>
      {% endfor %}
    </label>

    {# slug — format + availability as the user types #}
    <label class="field {% if field_errors.slug %}is-invalid{% elif form_data.slug %}is-valid{% endif %}">
      Address: /p/<input type="text" name="slug" value="{{ form_data.slug }}"
             dj-input="validate_field" dj-debounce="300"
             autocapitalize="off" spellcheck="false" required />/
      {% for err in field_errors.slug %}
        <span class="err">{{ err }}</span>
      {% endfor %}
    </label>

    {# contact email — validates on blur (dj-change) so partial input doesn't flash red #}
    <label class="field {% if field_errors.contact_email %}is-invalid{% elif form_data.contact_email %}is-valid{% endif %}">
      Contact email (optional)
      <input type="email" name="contact_email" value="{{ form_data.contact_email }}"
             dj-change="validate_field" autocomplete="email" />
      {% for err in field_errors.contact_email %}
        <span class="err">{{ err }}</span>
      {% endfor %}
    </label>

    {# dates — the range rule lives in clean(), so it shows after submit #}
    <label class="field {% if field_errors.starts_on %}is-invalid{% endif %}">
      Starts on
      <input type="date" name="starts_on" value="{{ form_data.starts_on }}"
             dj-change="validate_field" />
      {% for err in field_errors.starts_on %}
        <span class="err">{{ err }}</span>
      {% endfor %}
    </label>
    <label class="field {% if field_errors.ends_on %}is-invalid{% endif %}">
      Ends on
      <input type="date" name="ends_on" value="{{ form_data.ends_on }}"
             dj-change="validate_field" />
      {% for err in field_errors.ends_on %}
        <span class="err">{{ err }}</span>
      {% endfor %}
    </label>
  </fieldset>

  <button type="submit" {% if not can_submit %}disabled{% endif %}
          dj-form-pending="disabled">
    <span dj-form-pending="hide">Create project</span>
    <span dj-form-pending="show" hidden>Creating&hellip;</span>
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

Four deliberate choices:

| Choice | Why |
|---|---|
| `dj-input="validate_field"` on name + slug | Live feedback — the user sees "taken" / "available" as they type. |
| `dj-change="validate_field"` on email and dates | Email is "blur to validate" because `alice@` shouldn't flash red mid-typing. `dj-change` fires when the input loses focus or the user hits Enter. |
| `dj-debounce="300"` on slug | Slug validation hits the DB (uniqueness check). Without debounce, every keystroke would `SELECT 1 FROM myapp_project WHERE slug=?`. 300 ms cuts that to ~1 query per "settled typing pause." (Text inputs already debounce `dj-input` by 300 ms; the attribute makes it explicit.) |
| The date-range rule in `clean()` | It needs both dates, so it can't run per field. It runs on submit and, through `add_error("ends_on", …)`, lands next to the field it's about. |

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

Wire the view up (`path("projects/new/", NewProjectView.as_view())`),
sign in, and visit `/projects/new/`:

1. Type `ab` in name → red "Ensure this value has at least 3
   characters (it has 2)."
2. Type `My Launch!` in the address → red "Enter a valid “slug”
   consisting of letters, numbers, underscores or hyphens."
3. Type `admin` → red "That address is reserved."
4. Type `launch` → green (assuming no project uses it yet). Create
   one, then start a second project and type `launch` again → red
   "That address is taken."
5. Type `alice@` in the contact email — no error yet (didn't blur).
   Tab out → red "Enter a valid email address."
6. Pick an end date before the start date → no error yet: the rule
   lives in `clean()`. Click **Create project** → red "The end date
   can't be before the start date." under the end date.
7. Fix the dates and submit → `form_valid` creates the project, the
   form resets, and the success message shows the new address.

Each per-field validation: ~80 ms round-trip. With debouncing,
the DB sees one validation per pause, not one per keystroke. The
user gets near-real-time feedback without ever feeling the
network.

---

## Why this beats client-side validation libraries

The classic alternative is a JS validation library (Yup, Zod,
react-hook-form, etc.) that runs in the browser. It's faster
(no round-trip) but it has a fatal flaw: **the rules drift from
the server**. Slug availability can't run client-side at all
(no DB access). Reserved-word lists get updated on the server and
not in the bundle. Email validation regexes disagree on edge cases.

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
| Sign-up, login, password reset | djust's [account pages](accounts.md) |
| 50-field admin config (validation on submit only) | Plain Django form view (not LiveView) |
| Real-time validation with DB checks | **`FormMixin`** |
| Real-time validation, no DB hits | Could be either; `FormMixin` is still simplest |

The win of `FormMixin` is the live-feedback UX. If you don't
need that — settings pages where submit is fine — plain
`@event_handler` with one `clean()` pass on submit is simpler.

---

## What just happened, end to end

```
   Browser                                 Server
       │                                       │
       │ user types "launch" in the address    │
       │ ── 300ms debounce ─────────────────► validate_field(field="slug", value="launch")
       │                                       │   form = ProjectForm(form_data)
       │                                       │   slug field clean + validators
       │                                       │     → clean_slug runs
       │                                       │     → Project.objects.filter(...).exists() → False
       │                                       │   field_errors has no "slug" key
       │ ◄ patch: field flips green ──────────│
       │                                       │
       │ user types "admin"                    │
       │ ── 300ms debounce ─────────────────► validate_field(field="slug", value="admin")
       │                                       │   clean_slug → reserved
       │                                       │   field_errors["slug"] = ["That address is reserved."]
       │ ◄ patch: red border + error msg ─────│
       │                                       │
       │ click Create project                  │
       │ ─────────────────────────────────────► submit_form()
       │                                       │   form.is_valid() — every clean_* + clean()
       │                                       │   form_valid(form):
       │                                       │     Project.objects.create(owner=user, ...)
       │                                       │       (IntegrityError → "just taken")
       │                                       │     self.reset_form()
       │                                       │     self.success_message = "Project … created"
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
- **Editing an existing project:** pass `initial=` data in `mount()`,
  and exclude the project's own row from the slug check
  (`.exclude(pk=self.project.pk)`), or the current slug reads as
  "taken".
- **CSRF protection:** events over the WebSocket are covered by
  the connection's origin check; keep `{% csrf_token %}` in the
  form, as above, so the HTTP fallback transport can send the
  token.
- **File fields:** uploads have their own transport; handle them
  with `UploadMixin` as in the
  [uploads tutorial](tutorial-file-uploads-progress.md) rather
  than a `forms.FileField`.

The five-line shape — `class MyForm(forms.Form): clean_<field>(self): ...`
plus `class MyView(FormMixin, LiveView): form_class = MyForm` —
is the entire surface. Once you've written one validated form,
the next ten are mostly more `clean_*` methods.
