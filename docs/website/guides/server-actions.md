---
title: "Server Actions (`@action` and `dj-form-pending`)"
slug: server-actions
section: guides
order: 12.6
level: intermediate
description: "React 19-parity Server Actions for djust — @action wraps a handler so its pending/error/result state is auto-injected into the template, paired with dj-form-pending for declarative in-flight form UX."
---

# Server Actions (`@action` and `dj-form-pending`)

**New in v0.8.0.** Two paired primitives that close the
React-19-Server-Actions gap with zero boilerplate per handler.

| Primitive | Layer | What it gives you |
|---|---|---|
| `@action` decorator | server | `{pending, error, result}` for the handler, automatically injected into the template under the action's name. |
| `dj-form-pending` attribute | client | Declaratively hide/show/disable any element inside a `<form dj-submit>` while the submit is in-flight — no prop drilling, no JS. |

`dj-form-pending` covers the **in-flight client UX** during the
network round-trip; `@action` covers the **post-completion server
state** after the handler returns. Together they give you React 19
form ergonomics without writing a single state-management line.

---

## `@action` — Server Actions

Decorate a `LiveView` method with `@action`:

```python
from djust import LiveView, action


class TodoView(LiveView):
    todos = state(default_factory=list)

    @action
    def create_todo(self, title: str = "", **kwargs):
        if not title:
            raise ValueError("Title is required")
        todo = Todo.objects.create(title=title, user=self.request.user)
        self.todos.append({"id": todo.id, "title": todo.title})
        return {"created": todo.id}
```

The framework auto-populates `self._action_state["create_todo"]`
across the handler's lifecycle:

| Phase | `_action_state["create_todo"]` |
|---|---|
| Before handler runs | `{pending: True,  error: None, result: None}` |
| On `return value` | `{pending: False, error: None, result: value}` |
| On `raise exc` (re-raised) | `{pending: False, error: "<message>", result: None}` |

Every action's state is then injected into the template context
**under its method name**, so you can read it directly:

```django
{% if create_todo.pending %}
    <p>Saving…</p>
{% elif create_todo.error %}
    <div class="error">{{ create_todo.error }}</div>
{% elif create_todo.result %}
    <div class="success">Todo {{ create_todo.result.created }} created!</div>
{% endif %}
```

### How `@action` differs from `@event_handler`

Every `@action` is also an `@event_handler` underneath — same
dispatch path, same parameter coercion, same `@permission_required`
/ `@rate_limit` semantics. The only addition is the action-state
tracking layer. Use the bare `@event_handler` when you don't care
about post-completion state; use `@action` when the template needs
to react to it.

### Both decorator forms work

```python
@action
def create_todo(self, **kwargs): ...

@action(description="Create a new todo item")
def create_todo(self, **kwargs): ...
```

The optional `description=` is metadata — surfaced via
`is_action(func)` for tooling that introspects the view (e.g. the
djust MCP server).

### Retry semantics

Re-running an action **resets** its state. A failed run followed by
a successful run shows only the success; a successful run followed
by a failure shows only the error. The template never sees stale
result alongside fresh error (or vice versa).

### Name collisions

If a `@action`'s method name happens to match a public class
attribute, the action's state wins in the template context — actions
are always the canonical reading of their name. This is a deliberate
feature, not a bug; it lets you use natural names like `save` or
`delete` without reserving them on the view class.

---

## `dj-form-pending` — declarative pending UI

Mark any element nested inside a `<form dj-submit>` with
`dj-form-pending` and it reacts automatically while the submit is
in-flight:

```html
<form dj-submit="create_todo">
    <input name="title" placeholder="What needs doing?">

    <button type="submit">
        <span dj-form-pending="hide">Add</span>
        <span dj-form-pending="show">Saving…</span>
    </button>

    <button type="button" dj-form-pending="disabled" dj-click="cancel">
        Cancel
    </button>
</form>
```

Three modes:

| Mode | Behavior |
|---|---|
| `dj-form-pending="hide"` | Element is hidden via the `hidden` attribute while pending. Use for an idle label that disappears during submit. |
| `dj-form-pending="show"` | Element is hidden by default, visible while pending. Use for a spinner or "Saving…" text. |
| `dj-form-pending="disabled"` | `disabled = true` while pending; original `disabled` state restored on resolve. User-disabled elements stay disabled (the helper tracks pre-pending state in `data-djust-form-pending-was-disabled`). |

Unknown modes are silently ignored — forward-compatible if more
modes are added in a future release.

### CSS-only hooks via `data-djust-form-pending`

The form itself gets `data-djust-form-pending="true"` while pending,
so CSS selectors can react without any JS:

```css
form[data-djust-form-pending] {
    cursor: progress;
}

form[data-djust-form-pending] .spinner {
    display: inline-block;
}

form:not([data-djust-form-pending]) .spinner {
    display: none;
}
```

### Scope isolation

Only `[dj-form-pending]` descendants of the **actually-submitting**
form react. If you have multiple `<form dj-submit>` forms on the
same page, they don't interfere — each form's pending state is
scoped to itself.

### Error-path cleanup

The pending flag is set BEFORE the network round-trip and cleared
in a `finally` block so it always resolves — even when the handler
raises. You'll never see a stuck "Saving…" state because of an
unhandled exception.

---

## Putting them together

The canonical pattern: `dj-form-pending` for the in-flight UX,
`@action` for the post-completion state, both reading the same
handler:

```python
class TodoView(LiveView):
    todos = state(default_factory=list)

    @action
    def create_todo(self, title: str = "", **kwargs):
        if not title:
            raise ValueError("Title cannot be empty")
        todo = Todo.objects.create(title=title, user=self.request.user)
        self.todos.append({"id": todo.id, "title": todo.title})
        return {"id": todo.id}
```

```html
<form dj-submit="create_todo">
    <input name="title" placeholder="What needs doing?">

    <button type="submit">
        <span dj-form-pending="hide">Add</span>
        <span dj-form-pending="show">Saving…</span>
    </button>

    {# Post-completion state — read after the handler returns. #}
    {% if create_todo.error %}
        <p class="error">{{ create_todo.error }}</p>
    {% elif create_todo.result %}
        <p class="success">Added!</p>
    {% endif %}
</form>

<ul>
    {% for todo in todos %}
        <li>{{ todo.title }}</li>
    {% endfor %}
</ul>
```

The user types a title, clicks Add. During the round-trip the
button text flips to "Saving…" (`dj-form-pending`). On success the
new todo appears in the list and "Added!" shows below
(`create_todo.result`). On failure (empty title) the "Saving…"
clears and the error renders (`create_todo.error`).

Total client JS authored: zero. Total server state code: zero.

---

## Comparison vs related primitives

| Decorator / attribute | When to use |
|---|---|
| `@event_handler` | Most handlers. Triggers a re-render; no auto-tracked state. |
| `@event_handler(expose_api=True)` | Handler that's also callable over HTTP for mobile / S2S / AI agents. |
| [`@server_function`](server-functions.md) | Same-origin browser RPC with no re-render. Use for typeahead / validation. |
| **`@action`** | Form submission where the template needs to react to `pending` / `error` / `result`. |
| `dj-loading="event_name"` | Per-event loading scope outside a form context. |
| **`dj-form-pending`** | Per-form pending UI without naming the event. Lives inside a `<form dj-submit>`. |

---

## Tests

`@action` is covered by **18 regression tests** in
`tests/test_action_decorator.py`. `dj-form-pending` is covered by
**8 JSDOM tests** in `tests/js/dj-form-pending.test.js` covering
`data-djust-form-pending` toggle, mode handling, user-disabled
preservation, scope isolation, error-path cleanup, and forward-
compat for unknown modes.

## See also

- [Server Functions](server-functions.md) — for browser RPC without
  re-render or auto-tracked state.
- [Forms](forms.md) — `FormMixin` / `as_live_field()` for binding
  Django forms.
- [Events (core concepts)](../core-concepts/events.md) — the broader
  event-binding model that `@action` extends.
