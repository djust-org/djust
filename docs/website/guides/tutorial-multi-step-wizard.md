---
title: "Tutorial: Build a multi-step form wizard"
slug: tutorial-multi-step-wizard
section: guides
order: 62
level: intermediate
description: "Build a 3-step signup wizard with per-step validation, back/next navigation, and a final review screen — using only state(), @event_handler, and conditional rendering. No state machines, no router, no JavaScript."
---

# Tutorial: Build a multi-step form wizard

Multi-step UIs (signup, checkout, onboarding, settings flows) are
the most common place developers reach for a state machine library
or a client-side router. djust handles them with the same
primitives you already use for any LiveView: a `state()` cursor and
a few conditional template branches.

By the end you'll have a 3-step signup wizard:

- **Step 1**: collect name + email, validate non-empty + email format
  on Next.
- **Step 2**: collect plan + billing-cycle, validate plan was picked.
- **Step 3**: review collected fields and submit.
- **Back** and **Next** between steps; refusal to advance if the
  current step is invalid (with the error rendered inline).

Total code: one Python class, one template, no JavaScript, no
external dependencies.

| You'll learn | Documented in |
|---|---|
| State as a step cursor | [State & Computation Primitives](/guides/state-primitives/) |
| Conditional rendering across steps | [Template Cheat Sheet](/guides/template-cheatsheet/) |
| Per-step server-side validation | [Forms & Validation](/guides/forms/) |
| Submit-final pattern with `@action` | [Server Actions](/guides/server-actions/) |

> **Prerequisites:** [Quickstart](/getting-started/), familiarity with
> [`state()`](/guides/state-primitives/) and [`@event_handler`](/guides/server-actions/).

---

## Step 1 — Model the wizard state

The whole "wizard" is just a `step` cursor plus the in-progress
form data. We track a per-step `errors` dict so each Next click
either advances or surfaces the issue.

```python
# myapp/views.py
from djust import LiveView, action, event_handler, state


class SignupWizardView(LiveView):
    template_name = "signup_wizard.html"

    step = state(1)
    name = state("")
    email = state("")
    plan = state("")
    cycle = state("monthly")
    errors = state(default_factory=dict)
```

Five fields total. `step` starts at 1, the rest start empty. The
`errors` dict is reset on every advance attempt so old errors don't
linger.

---

## Step 2 — Validation per step

Validation lives on the server (no `<input pattern="…">` rabbit
holes). One validator per step, returning a fresh dict of field
→ error message:

```python
import re

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
VALID_PLANS = {"hobby", "team", "enterprise"}


def _validate_step_1(name: str, email: str) -> dict:
    errors = {}
    if not name.strip():
        errors["name"] = "Tell us your name."
    if not EMAIL_RE.match(email.strip()):
        errors["email"] = "Enter a valid email address."
    return errors


def _validate_step_2(plan: str) -> dict:
    if plan not in VALID_PLANS:
        return {"plan": "Pick a plan to continue."}
    return {}
```

---

## Step 3 — Advance / retreat handlers

Two `@event_handler`s plus the final `@action` for submit:

```python
class SignupWizardView(LiveView):
    # ... state as above ...

    @event_handler
    def next_step(self, **kwargs):
        # Pull current values out of kwargs so a quick edit doesn't
        # require the user to re-tab through the form.
        for field in ("name", "email", "plan", "cycle"):
            if field in kwargs:
                setattr(self, field, kwargs[field])

        if self.step == 1:
            self.errors = _validate_step_1(self.name, self.email)
        elif self.step == 2:
            self.errors = _validate_step_2(self.plan)
        else:
            self.errors = {}

        if not self.errors:
            self.step += 1

    @event_handler
    def back_step(self, **kwargs):
        self.errors = {}
        if self.step > 1:
            self.step -= 1

    @action
    def submit(self, **kwargs):
        # All earlier steps already validated. Worst case is the
        # user never reached step 3 — guard anyway.
        if self.step != 3:
            raise ValueError("Wizard incomplete.")
        # Real signup: create user, send welcome email, etc.
        # For the tutorial we just acknowledge.
        return {"signup": {"name": self.name, "email": self.email}}
```

Three things worth pulling out:

- **Validation is the source of truth for step transitions.** The
  step only advances when `self.errors` is empty after running the
  current step's validator.
- **Form values flow through kwargs.** Every event payload includes
  the form field values; we copy them into `self.*` so the next
  render shows what the user typed. Without this, switching steps
  would clear the inputs.
- **Final submit uses `@action`**, not `@event_handler`, so the
  template can read `submit.pending` / `.error` / `.result` for the
  success-screen UX.

---

## Step 4 — The template

```html
<!-- myapp/templates/signup_wizard.html -->
<section class="wizard">
  <ol class="wizard-steps" aria-label="Signup progress">
    <li {% if step == 1 %}aria-current="step"{% endif %}>1. About you</li>
    <li {% if step == 2 %}aria-current="step"{% endif %}>2. Plan</li>
    <li {% if step == 3 %}aria-current="step"{% endif %}>3. Review</li>
  </ol>

  <form dj-submit="next_step">

    {% if step == 1 %}
      <label>
        Name
        <input name="name" value="{{ name }}" required />
        {% if errors.name %}<span class="err">{{ errors.name }}</span>{% endif %}
      </label>
      <label>
        Email
        <input name="email" type="email" value="{{ email }}" required />
        {% if errors.email %}<span class="err">{{ errors.email }}</span>{% endif %}
      </label>

    {% elif step == 2 %}
      <fieldset>
        <legend>Plan</legend>
        <label><input type="radio" name="plan" value="hobby"      {% if plan == "hobby" %}checked{% endif %} /> Hobby</label>
        <label><input type="radio" name="plan" value="team"       {% if plan == "team" %}checked{% endif %} /> Team</label>
        <label><input type="radio" name="plan" value="enterprise" {% if plan == "enterprise" %}checked{% endif %} /> Enterprise</label>
        {% if errors.plan %}<span class="err">{{ errors.plan }}</span>{% endif %}
      </fieldset>
      <fieldset>
        <legend>Billing cycle</legend>
        <label><input type="radio" name="cycle" value="monthly" {% if cycle == "monthly" %}checked{% endif %} /> Monthly</label>
        <label><input type="radio" name="cycle" value="annual"  {% if cycle == "annual" %}checked{% endif %} /> Annual (save 20%)</label>
      </fieldset>

    {% elif step == 3 %}
      <dl class="review">
        <dt>Name</dt><dd>{{ name }}</dd>
        <dt>Email</dt><dd>{{ email }}</dd>
        <dt>Plan</dt><dd>{{ plan|capfirst }} ({{ cycle }})</dd>
      </dl>
    {% endif %}

    <div class="actions">
      {% if step > 1 %}
        <button type="button" dj-click="back_step">&larr; Back</button>
      {% endif %}

      {% if step < 3 %}
        <button type="submit">Next &rarr;</button>
      {% else %}
        <button type="button" dj-click="submit" dj-loading.disable>
          <span dj-loading.hide>Create account</span>
          <span dj-loading.show hidden>Creating&hellip;</span>
        </button>
      {% endif %}
    </div>

    {% if submit.result %}
      <p class="ok" role="status">
        Welcome, {{ submit.result.signup.name }}! Check {{ submit.result.signup.email }} for a verification link.
      </p>
    {% endif %}
    {% if submit.error %}
      <p class="err" role="alert">{{ submit.error }}</p>
    {% endif %}
  </form>
</section>
```

Three template patterns at work:

| Pattern | Why |
|---|---|
| `{% if step == N %}` per step | Only one step's inputs are mounted at a time. djust's diffing means switching steps is a single patch, not a re-render of the whole form. |
| `value="{{ name }}"` / `checked` reflectors | Echo the current state so users see what they previously typed when they navigate Back. |
| `dj-loading.show` / `.hide` on the submit button | Standard loading-state UX. Pairs with `@action` so the button stays disabled until submit completes. |

---

## Why this beats a state machine library

For three steps with simple linear navigation, a state machine library
adds more code than it saves: you'd write the same `if step == N` in
your transitions, plus configuration, plus a runtime, plus types. The
djust pattern collapses to:

- One integer for the cursor.
- One handler per transition.
- One template branch per step.

Where a state machine *does* earn its keep is when transitions are
non-linear (state X can go to state Y, Z, or W depending on the user's
selection) and the transition logic itself is non-trivial. For the
common-case linear or branching wizard, a `state(int)` cursor plus
`@event_handler` validators is enough.

---

## Where to go next

- **Persist progress across page refreshes:** add a `mount()` hook
  that hydrates `step`, `name`, etc. from a saved draft (Django
  session or DB row). Save on every transition.
- **Skip-to-step navigation:** add `<button dj-click="goto_step"
  data-step="2">2. Plan</button>` and a `goto_step` handler that
  validates every step ≤ N before jumping.
- **Branching wizards:** if step 2's plan choice changes step 3's
  questions, just check `self.plan` inside the step-3 template
  branch — no extra primitive needed.
- **Server-side draft autosave:** drop a `@dj_listen("draft_changed")`
  on the view and have a periodic JS hook NOTIFY when the user is
  idle. Same pattern as the [comment-thread tutorial](/guides/tutorial-real-time-comments/).
- **Submit to an external API:** if `submit()` calls a slow third-party
  service, wrap the call in `start_async()` so the spinner shows
  immediately. See [Loading States](/guides/loading-states/).

The wizard is one of three shapes that combine `state()` with
conditional templates:

1. **Cursor wizard** (this tutorial): one integer, linear or branching steps.
2. **Mode toggle**: one boolean / enum, two render branches.
3. **Master-detail**: one selected-id field, list + panel branches.

Once they click, every "the UI changes based on what the user has
done so far" feature is the same recipe.
