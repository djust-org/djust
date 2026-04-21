---
title: "Type-Safe Template Validation"
slug: typecheck
section: guides
order: 37
level: intermediate
description: "Catch template variable typos before they hit production with manage.py djust_typecheck — static analysis over all LiveView templates"
---

# Type-Safe Template Validation

Python's dynamic typing makes template-variable bugs the #1 source of "it rendered blank and I don't know why" issues. `manage.py djust_typecheck` reads every LiveView template, extracts every variable reference, and flags names that the view can't possibly provide.

No TypeScript. No type stubs. No build step. Just a management command you run locally or in CI.

## Running the check

```bash
python manage.py djust_typecheck
```

Example output:

```
djust_typecheck: 2 view(s) with unresolved names

myapp.views.CheckoutView (checkout.html)
    line 14: cartt
    line 22: total_amount

myapp.views.ProfileView (profile.html)
    line 7: usre
```

The command walks every `LiveView` subclass in the project, resolves each view's `template_name` via Django's template loaders, and looks for any variable or tag reference that isn't covered by the view's declared context.

### Options

```bash
python manage.py djust_typecheck --json            # machine-readable JSON
python manage.py djust_typecheck --app myapp       # one Django app
python manage.py djust_typecheck --view CheckoutView  # one view
python manage.py djust_typecheck --strict          # exit non-zero on findings
```

Add it to CI with `--strict`:

```yaml
# .github/workflows/ci.yml
- name: Template typecheck
  run: python manage.py djust_typecheck --strict
```

## What counts as "declared"

A reference resolves against the union of:

1. **Public class attributes** — anything set on the class body (or inherited from user mixins) with a non-underscore name.
2. **`self.foo = ...` assignments** — anywhere in the class (mount, event handlers, helpers). AST-extracted without running the code.
3. **`@property` decorated methods** — treated as attributes.
4. **Literal `return {...}` in `get_context_data`** — keys from a `return {"foo": ...}` or `return {**super, "foo": ...}` literal.
5. **Template-local names** — `{% for x in ... %}`, `{% with x=... %}`, and `{% inputs_for fs as form %}` all bind identifiers the rest of the block may use.
6. **Framework built-ins** — `user`, `request`, `perms`, `csrf_token`, `messages`, `forloop`, `djust`, `is_dirty`, `changed_fields`, `async_pending`, etc.
7. **Project-wide globals** — anything listed in `settings.DJUST_TEMPLATE_GLOBALS`.

Example:

```python
# settings.py
DJUST_TEMPLATE_GLOBALS = ["site_title", "current_year", "feature_flags"]
```

Context processors that inject values into every template should be listed here — otherwise the checker will flag every reference to them.

## Silencing false positives

The checker is intentionally conservative — anything it can't statically resolve gets flagged. Three ways to silence:

### Per-template pragma

```django
{# djust_typecheck: noqa #}
```

Silences the entire template. Use `noqa name1, name2` to silence specific names only:

```django
{# djust_typecheck: noqa dynamic_section, late_bound_flag #}
```

### Per-view opt-in to strict mode

```python
class CheckoutView(LiveView):
    strict_context = True
    template_name = "checkout.html"
```

`strict_context = True` makes *that view's* findings count as errors regardless of the global `--strict` flag. Good for "this view is fully typed; regress at your peril."

### Global globals list

```python
# settings.py
DJUST_TEMPLATE_GLOBALS = ["navbar", "breadcrumbs"]
```

For names injected by base templates, context processors, or framework plumbing outside djust.

## Known limitations

- **Dynamic dict returns** — `get_context_data` that builds its dict in a loop, via `dict(...)`, or with `update()` calls is not followed. Declare the keys on `self` instead, or list them in `DJUST_TEMPLATE_GLOBALS`.
- **`{% include %}` scope** — the included template's variables aren't cross-checked against the including view's context. Include templates should be checked independently if they also belong to a LiveView.
- **Filter argument evaluation** — `{{ foo|default:bar }}` is treated as a reference to `foo` only (the `bar` argument is not statically extracted yet).
- **Component slots** — `LiveComponent` templates that reference slot content are not traced back to the parent's context.

The checker errs on the side of false positives over false negatives — a flagged name is either a real bug or a name you should tell the checker about (via `noqa`, `DJUST_TEMPLATE_GLOBALS`, or a class attribute).

## Why this is a differentiator

Neither Phoenix nor React catches template-variable typos statically without an external type system (TypeScript, Flow). djust is the first Python-side LiveView-style framework with a first-party static template check. The tradeoff: accepting some false positives in exchange for catching real bugs before they reach users — most projects find the first run worth it.

## Related

- [Developer Tools](developer-tools.md) — `djust_doctor` configuration check, latency simulator
- [Error Overlay (Dev Mode)](error-overlay.md) — catches the *other* kind of template bug: runtime rendering errors
