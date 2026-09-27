---
title: "Tutorial: Optimistic UI updates with JS Commands"
slug: tutorial-optimistic-updates
section: guides
order: 66
level: intermediate
description: "Build a todo list where toggling a todo's done state flips the UI instantly — before the server has even seen the click. The server still authoritatively confirms, or rolls the guess back, on the next round-trip. Uses a JS Command chain with push() and explains the reconciliation model."
---

# Tutorial: Optimistic UI updates with JS Commands

The cheapest way to make a UI feel fast is to lie a little. When the
user clicks "Like", flip the heart immediately — don't wait the
80 ms it takes the WebSocket round-trip to confirm. If the server
later disagrees (rate-limited, deleted, permission-denied), correct
the UI then. Most of the time it won't disagree, so most of the
time the lie holds and the click feels instant.

djust does this with a [JS Command](js-commands.md) chain on
`dj-click`:

- The chain's DOM operations (here, `add_class` / `remove_class`)
  run in the browser **immediately**, before anything goes over the
  WebSocket.
- Its final `push(...)` sends the event to the server, which runs
  the handler and re-renders. When the server's render agrees with
  the guess, nothing visibly changes; when it rejects the change, the
  handler sends a JS Command back that undoes the guess.

> **Not `@optimistic`.** `djust.decorators.optimistic` exists, but
> it is a marker only: no client code reads it, so decorating a
> handler with it changes nothing at runtime (tracked in
> [#2699](https://github.com/djust-org/djust/issues/2699)). Use a JS
> Command chain as shown here.

By the end of this tutorial you'll have a todo list where:

- **Toggling a todo's done state flips the checkbox + line-through
  immediately.** No spinner, no delay.
- The server still updates the DB, and its re-render confirms the
  flip.
- **Locked todos can't be toggled.** The click still flips
  instantly, and the server rolls it back with a flash message a
  round-trip later. The user sees a brief incorrect state and then
  the truth.

| You'll learn | Documented in |
|---|---|
| JS Command chains on `dj-click`, and `push()` | [JS Commands](js-commands.md) |
| Rolling a guess back with `push_commands()` | [JS Commands](js-commands.md) |
| When NOT to use optimistic updates | This tutorial |
| Reconciliation model (client guess → server authority) | This tutorial |

> **Prerequisites:** [Quickstart](../getting-started/first-liveview.md), the [search-as-you-type
> tutorial](tutorial-search-as-you-type.md) (sets up the
> event-handler vocabulary). Familiarity with `@event_handler` helps.

---

## When optimistic updates earn their keep

An optimistic update is a perf trick, not a correctness one. Use it when:

- The action's **result is determined by the event data alone** —
  toggling, liking, voting, marking-as-read. The server can't say
  anything the client doesn't already know.
- The **failure cases are rare** (rate limits, auth checks, race
  conditions). The cost of a brief wrong-then-right flicker is
  lower than the cost of every action feeling sluggish.
- The action is **idempotent or self-healing** — re-applying the
  server's authoritative diff has to land you in a consistent
  state regardless of the client's guess.

Don't use it when:

- The result depends on **server-side logic the client can't predict**
  (assigned-id, computed total, derived field).
- The failure case is **common** (e.g. a typeahead lookup that often
  returns no match — the user would see a flicker on every character).
- The user would be **confused by the flicker** more than helped by
  the speed (sensitive financial / medical confirmations).

---

## Step 1 — The model

Standard Django:

```python
# myapp/models.py
from django.conf import settings
from django.db import models


class Todo(models.Model):
    title = models.CharField(max_length=200)
    done = models.BooleanField(default=False)
    locked = models.BooleanField(default=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="todos",
    )

    class Meta:
        ordering = ["done", "-id"]
```

---

## Step 2 — The view, with a JS Command per row

```python
# myapp/views.py
from djust import LiveView, state
from djust.decorators import event_handler
from djust.js import JS

from .models import Todo


def _toggle_chain(todo):
    # Flip the row's class in the browser now, then tell the server.
    flip = JS.remove_class if todo.done else JS.add_class
    return str(
        flip("is-done", closest=".todo")
        .push("toggle_todo", value={"todo_id": todo.id})
    )


class TodoListView(LiveView):
    template_name = "todos.html"
    login_required = True

    todos = state(default_factory=list)

    def mount(self, request, **kwargs):
        self._refresh()

    def _refresh(self):
        self.todos = [
            {"id": t.id, "title": t.title, "done": t.done,
             "toggle": _toggle_chain(t)}
            for t in Todo.objects.filter(user=self.request.user)
        ]

    @event_handler
    def toggle_todo(self, todo_id: int = 0, **kwargs):
        todo = Todo.objects.get(id=todo_id, user=self.request.user)
        if todo.locked:
            # The browser already flipped the row. Nothing on the
            # server changed, so the re-render won't touch it: undo
            # the guess explicitly.
            undo = JS.add_class if todo.done else JS.remove_class
            self.push_commands(undo("is-done", to=f"#todo-{todo.id}"))
            self.put_flash("error", "That todo is locked.")
            return
        todo.done = not todo.done
        todo.save()
        self._refresh()
```

Three pieces:

1. **`_toggle_chain()`** builds each row's click behaviour from the
   *server's* current state: add `is-done` if the todo is open,
   remove it if it's done, then `push("toggle_todo", ...)`. The
   chain is stored as its JSON string, so it is plain view state.
2. **The happy path needs no reconciliation code.** The handler
   saves and re-renders; the server's new `class` for that row
   matches what the browser already shows, and the row's next chain
   points the other way.
3. **The failure path must undo the guess itself.** A class that a
   JS Command set stays set until something changes it. When the
   server's state didn't change, the re-render sends no patch for
   that row, so the handler sends the inverse operation with
   `push_commands()`.

---

## Step 3 — The template

```html
<!-- myapp/templates/todos.html -->
{% load djust_flash %}
{% dj_flash %}

<ul class="todos">
  {% for todo in todos %}
    <li id="todo-{{ todo.id }}" dj-key="{{ todo.id }}"
        class="todo {% if todo.done %}is-done{% endif %}">
      <button
        type="button"
        dj-click="{{ todo.toggle }}"
        aria-pressed="{{ todo.done|yesno:'true,false' }}"
        class="todo-toggle"
      >
        <span class="todo-check" aria-hidden="true"></span>
        <span class="todo-title">{{ todo.title }}</span>
      </button>
    </li>
  {% endfor %}
</ul>
```

The pieces that make the optimistic flip work:

| Piece | Role |
|---|---|
| `dj-click="{{ todo.toggle }}"` | `dj-click` recognises a JSON command list and runs it in the browser instead of sending a plain event. The chain ends in `push`, so the server still hears about the click. |
| `class="todo {% if todo.done %}is-done{% endif %}"` | The CSS state hook. The chain flips it with `closest=".todo"`; the server's render sets it authoritatively. |
| `id="todo-{{ todo.id }}"` | The target for the rollback command the handler sends. |
| `dj-key="{{ todo.id }}"` | A stable key, so the diff matches rows by todo rather than by position when the list reorders (`ordering = ["done", "-id"]` moves a toggled todo). |

---

## Step 4 — The CSS

```css
.todo {
  display: flex;
  align-items: center;
  padding: 8px 12px;
  border-bottom: 1px solid var(--color-border, #e5e7eb);
  transition: opacity 0.15s;
}
.todo.is-done {
  opacity: 0.55;
}
.todo.is-done .todo-title {
  text-decoration: line-through;
}
.todo-toggle {
  display: flex;
  align-items: center;
  gap: 10px;
  flex: 1;
  background: transparent;
  border: 0;
  padding: 0;
  text-align: left;
  cursor: pointer;
}
.todo-check {
  display: inline-flex;
  width: 20px;
  height: 20px;
  align-items: center;
  justify-content: center;
  border: 1.5px solid var(--color-muted, #9ca3af);
  border-radius: 4px;
  font-weight: 600;
}
.todo.is-done .todo-check {
  background: var(--color-accent, #10b981);
  border-color: var(--color-accent, #10b981);
  color: white;
}
.todo.is-done .todo-check::after {
  content: "✓";
}
```

The check mark comes from CSS on `.is-done`, so flipping the one
class updates everything the row shows. `opacity` and
`text-decoration` transitions are intentional — they
make the flip feel acknowledged without being abrupt. If you set
`transition: none`, the flip is sharper and the rare server-
correction is more visible.

---

## Step 5 — Try it, including the failure path

```python
# myapp/urls.py
from django.urls import path
from .views import TodoListView

urlpatterns = [path("todos/", TodoListView.as_view())]
```

Visit `/todos/` and click a few todos:

- Each click flips its row instantly. The round-trip then lands a
  server render that matches what the browser predicted, so nothing
  visibly changes on confirmation.
- Now mark one todo `locked` in the admin and click it. It flips
  instantly too. ~60 ms later the handler's rollback command flips
  it back and the flash message appears. The user sees a brief
  wrong state then the truth.

That brief wrong state is the design — an optimistic update can't
suppress it without holding every update for at least one
round-trip, which would defeat the purpose. If you need the flicker
hidden, use a plain `dj-click="toggle_todo"` handler and accept the
~80 ms baseline latency.

---

## What just happened, end to end

```
   Browser                    Client runtime              Server
      │                              │                       │
      │ click toggle (id=42)         │                       │
      │ ──────────────────────────►  │                       │
      │                              │  run JS Command:      │
      │                              │  add_class is-done    │
      │ ◄ DOM change (instant) ───── │                       │
      │                              │  send event over WS   │
      │                              │ ────────────────────► │
      │                              │                       │  Todo.objects.get()
      │                              │                       │  todo.done = True
      │                              │                       │  todo.save()
      │                              │                       │  diff template
      │                              │ ◄──── authoritative ──│
      │                              │       patch
      │                              │  row 42 already has   │
      │                              │  is-done → no visible │
      │                              │  change               │
      │ (no further DOM change)      │                       │
```

When the todo is locked:

```
      │ click toggle (locked todo)   │                       │
      │ ──────────────────────────►  │                       │
      │ ◄ DOM change (instant) ───── │  optimistic flip      │
      │                              │ ────────────────────► │
      │                              │                       │  todo.locked
      │                              │ ◄──── push_commands ──│  (no DB change)
      │                              │       (undo) + flash
      │ ◄ DOM change (revert) ────── │                       │
      │  (brief wrong → corrected)   │                       │
```

---

## Where to go next

- **More [JS Commands](js-commands.md)** for non-state
  optimistic UI (close a modal instantly, then save): `hide`,
  `show`, `transition` and the rest chain the same way.
- **Add a "Saving…" indicator** for actions where the optimistic flip
  isn't fully sufficient (e.g. server-assigned id). Show a small
  inline spinner from the optimistic flip until the server diff
  lands; hide on confirmation.
- **Rate limits** — `@rate_limit(rate=..., burst=...,
  on_exceed="drop")` drops excess events before the handler runs, so
  no handler code can roll the guess back. Keep rate-limited actions
  non-optimistic, or accept that a dropped click leaves its guess on
  screen until the row next re-renders.
- **Reconciliation logging** — in dev, the [latency
  simulator](developer-tools.md) lets you add artificial WS
  delay to verify your optimistic UI handles real-world network
  conditions.
- **Don't optimistic over auth boundaries** — if the action requires
  the user to be logged in and you're not 100% sure they still are,
  leave it as a plain `dj-click="handler"` so the auth-redirect
  doesn't flicker through a fake "success" state first.

The recipe — a JS Command that makes the guess, a `push()` that
tells the server, and a `push_commands()` rollback when the server
says no — is the same shape every "this should feel instant"
interaction uses: likes, votes, follows, mark-as-read, archive,
star, mute. Once you see the flicker happen once when the server
disagrees, the model clicks.
