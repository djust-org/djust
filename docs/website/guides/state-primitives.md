---
title: "State & Computation Primitives"
slug: state-primitives
section: guides
order: 18
level: intermediate
description: "Memoized @computed, automatic dirty tracking (is_dirty / changed_fields), stable IDs (unique_id), and component context sharing (provide_context / consume_context) — small primitives that close the gap with React's useMemo / useId / Context."
---

# State & Computation Primitives

Four small primitives shipped together in v0.5.1 to close gaps with
the equivalent React hooks. Each one is opt-in: skip them and your
LiveView still works exactly as before.

| Primitive | React equivalent | Use case |
|---|---|---|
| `@computed("dep")` | `useMemo` | Cache expensive derived values; recompute only on dep change |
| `is_dirty` / `changed_fields` / `mark_clean()` | n/a | "Unsaved changes" warnings, conditional save buttons |
| `self.unique_id(suffix="")` | `useId` | Stable form-field / `aria-labelledby` IDs across renders |
| `self.provide_context()` / `consume_context()` | Context API | Share a value with descendant components without prop drilling |

---

## Memoized `@computed("dep1", "dep2")`

Plain `@computed` evaluates on every access — fine for cheap
properties. With dependency names, the value is **cached on the
instance** and only recomputed when any listed dep changes (identity
or shallow content).

```python
from djust.decorators import computed
from djust.live_view import LiveView, state


class CartView(LiveView):
    items = state(default_factory=list)
    tax_rate = state(default=0.0825)

    @computed("items", "tax_rate")
    def total(self):
        # Only recomputes when items or tax_rate changes.
        subtotal = sum(item["price"] * item["qty"] for item in self.items)
        return subtotal * (1 + self.tax_rate)
```

**When the cache invalidates.** A dep is "changed" when its identity
differs OR its shallow fingerprint (id + length + sampled keys —
matching the `_snapshot_assigns` semantics used elsewhere in djust)
differs. Mutating an item in place (`self.items[0]["qty"] = 2`) without
reassigning the list won't invalidate — assign a new list, or
recompute manually.

**Skip the cache.** Use plain `@computed` (no args) when the
computation is cheap enough that property semantics are fine — every
access recomputes:

```python
@computed
def display_name(self):
    return f"{self.first_name} {self.last_name}"
```

---

## Automatic dirty tracking

After `mount()`, djust captures a baseline of every public attr. From
that point on:

- `self.changed_fields` — set of attr names that differ from baseline.
- `self.is_dirty` — `bool(self.changed_fields)`.
- `self.mark_clean()` — reset the baseline (call after a successful save).

```python
class ProfileView(LiveView):
    first_name = state(default="")
    last_name = state(default="")

    def mount(self, request):
        self.first_name = request.user.first_name
        self.last_name = request.user.last_name

    @event_handler
    def save(self):
        request.user.first_name = self.first_name
        request.user.last_name = self.last_name
        request.user.save()
        self.mark_clean()       # baseline now matches the saved state
```

```django
{# template — show a Save button only when there's work to save #}
{% if is_dirty %}
  <button dj-click="save">Save changes</button>
  <p class="hint">{{ changed_fields|length }} field(s) changed</p>
{% else %}
  <p class="hint">All changes saved.</p>
{% endif %}
```

The tracker respects `static_assigns` (constants the framework
shouldn't watch) and ignores private (`_`-prefixed) attrs. Both the
WebSocket consumer and the HTTP API dispatch view capture the
baseline post-mount, so dirty tracking works identically over both
transports.

### Common patterns

- **`beforeunload` warning**: render `<body data-dirty="{{ is_dirty }}">`,
  hook `beforeunload` in your base template to read the attr and
  warn the user.
- **Conditional save**: `{% if is_dirty %}` around the submit button.
- **Skip work in `handle_event`**: `if not self.is_dirty: return` at
  the top of an autosave handler.

---

## Stable `self.unique_id(suffix="")`

Returns a deterministic ID stable across re-renders of the same logical
element position. Format: `djust-<viewslug>-<n>[-<suffix>]`.

```python
class FormView(LiveView):
    def mount(self, request):
        self.email_id = self.unique_id("email")
        self.email_help_id = self.unique_id("email-help")
```

```django
<label for="{{ email_id }}">Email</label>
<input id="{{ email_id }}" aria-describedby="{{ email_help_id }}">
<p id="{{ email_help_id }}">We never share your email.</p>
```

The counter resets per render boundary via the framework's
`reset_unique_ids()` hook, so the same call in `mount()` always
returns the same ID across re-renders. Two views on the same page
get distinct IDs because the slug differs.

When to reach for `unique_id()`:

- `aria-labelledby` / `aria-describedby` — accessibility associations
  that need stable IDs across renders.
- `<label for="…">` paired with `<input id="…">` for non-Form views
  (FormMixin already generates stable IDs for Django form fields).
- Any element another script or stylesheet selects by ID.

Don't use it for routing keys (those should derive from data) or
session-scoped IDs (use the request session instead).

---

## Component context sharing

`provide_context(key, value)` exposes a value to all descendants of
the current view or component; `consume_context(key, default=None)`
walks the parent chain and returns the value (or `default` if none
provided it).

```python
class ThemedAppView(LiveView):
    def mount(self, request):
        self.provide_context("theme", request.user.preferences.theme)
        self.provide_context("locale", request.LANGUAGE_CODE)


# In a deeply-nested LiveComponent:
class AccentBadge(LiveComponent):
    def get_context_data(self):
        theme = self.consume_context("theme", default="light")
        return {"theme": theme}
```

The lookup walks `_djust_context_parent` upward until a provider is
found — no prop drilling required. Scope is per render tree;
`clear_context_providers()` resets the chain (rarely needed
manually — render boundaries do this for you).

When to reach for context vs explicit props:

- **Context** — values used by many descendants at varying depths
  (theme, locale, current user, feature flags).
- **Explicit props / assigns** — values used only by one or two
  immediate children (the `Assign(...)` DSL is more discoverable
  and runtime-validates).

---

## See also

- [Components guide](components.md) — the `Assign` / `Slot` DSL for
  declarative props on `LiveComponent` and function components.
- [Hooks guide](hooks.md) — when you really do need a JS-side hook
  instead of a server-side primitive.
