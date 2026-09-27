---
title: "Tutorial: Modals and toasts without a server round-trip"
slug: tutorial-js-commands
section: guides
order: 52
level: beginner
description: "Build a modal dialog and a toast notification system using JS Commands — chained DOM operations that run client-side, without ever pinging the server. Then pair with a server push for the actual save. The right tool for ephemeral UI state the server doesn't care about."
---

# Tutorial: Modals and toasts without a server round-trip

There's a class of UI state the server has no business hearing
about: a modal that's open, a dropdown that's expanded, a toast
that's been shown and dismissed. Round-tripping every "open this
panel" to the server costs ~80 ms of latency for a state change
that's purely cosmetic — the user doesn't care whether the
modal-open bit lives on the server or in the DOM.

djust's **JS Commands** are the right tool for this. They're the
same kind of declarative chain Phoenix LiveView 1.0 introduced —
a sequence of DOM operations that run client-side, in order, with
no WebSocket frame. You author them in Python (so they're
typechecked alongside your view), but they execute locally.

By the end of this tutorial you'll have:

- A "Profile" page with an **Edit profile** button that opens a
  modal **instantly** (no server round-trip).
- The modal **fades in** with a CSS animation that runs when
  `JS.show()` reveals it — no inline JS.
- A **save handler** that receives the form server-side, then
  closes the modal with the same kind of chain, pushed from Python
  with `self.push_commands()`, and shows a "Profile saved." toast.
- A **dismissable toast** that hides itself after 3 s, using a
  three-line hook.

| You'll learn | Documented in |
|---|---|
| `JS.show / hide / toggle / add_class / remove_class / focus` | [JS Commands](js-commands.md) |
| Running a chain from a server handler with `self.push_commands()` | [Server-Driven UI](server-driven-ui.md) |
| Why the form save goes through `dj-submit`, not a chain's `push` | This tutorial |
| When NOT to use JS Commands (state the server cares about) | This tutorial |

> **Prerequisites:** [Quickstart](../getting-started/installation.md), the [optimistic
> updates tutorial](tutorial-optimistic-updates.md) (recommended
> — JS Commands and `@optimistic` answer related but distinct
> questions). The only JavaScript is a three-line hook for the
> toast timer.

---

## When JS Commands earn their keep

JS Commands cover **client-only state**. The decision rule:

| The state… | Use |
|---|---|
| Lives on the server (DB row, current count, etc.) | `@event_handler` (round-trip) |
| Will be authoritatively confirmed by the server | `@optimistic` |
| Affects only the immediate visual presentation, server doesn't care | **`JS Commands`** |

Examples of "server doesn't care": modal open/closed, dropdown
expanded, accordion section visible, sidebar collapsed, toast
shown, color-picker active, tab selected (when each tab's content
is already in the DOM).

Examples where JS Commands are wrong: a "bookmark" toggle (server
needs to persist), a "mark as read" button (server tracks
read-state), a sort-column toggle (server may need to re-query).
For those, you want a real handler.

The middle case — save the form AND close the modal — is solved
by combining: the form's `dj-submit` sends the fields to a server
handler, and the handler pushes the close chain back with
`self.push_commands()`. Opening and cancelling stay local; the
modal closes only once the save has actually happened.

---

## Step 1 — The view + JS chains

```python
# myapp/views.py
from djust import LiveView, action, state
from djust.js import JS


class ProfileView(LiveView):
    template_name = "profile.html"
    login_required = True

    name = state("")
    email = state("")
    saved_message = state("")

    def mount(self, request, **kwargs):
        self.name = request.user.first_name or request.user.username
        self.email = request.user.email

        # Define the JS chains once. They're plain Python objects
        # that the client interprets as a sequence of DOM ops.

        self.open_modal = (
            JS.add_class("modal-open", to="body")
              .show("#edit-modal", display="flex")
              .focus("#name-input")
        )

        self.close_modal = (
            JS.hide("#edit-modal")
              .remove_class("modal-open", to="body")
        )

        self.dismiss_toast = JS.hide("#toast")

    @action
    def save_profile(self, name: str = "", email: str = "", **kwargs):
        self.name = name.strip()
        self.email = email.strip()
        # Persist to DB, etc. (omitted)
        self.saved_message = "Profile saved."
        # The save succeeded, so close the modal: the same chain
        # as Cancel, sent to the browser from the server.
        self.push_commands(
            JS.hide("#edit-modal").remove_class("modal-open", to="body")
        )
```

Three chains, two important patterns:

1. **CSS does the animation; the chain changes state.** The
   built-in commands run in order and do not wait for each other:
   `JS.transition("cls", time=N)` adds `cls` for `N` ms, but the
   next op runs immediately. So the fade-in is a CSS animation
   that starts when `show` reveals the modal (Step 3). An animated
   *close* would need a [custom command](js-commands.md#custom-commands)
   that returns a Promise, because a returned Promise is awaited
   before the next op runs.
2. **The save goes through `dj-submit`, and the server closes
   the modal.** `dj-submit` sends every form field to the handler.
   A chain's `.push("save_profile")` op would send only its
   `value=` dict, not the form, and only `dj-click` runs a chain
   at all. After saving, the handler calls `self.push_commands()`
   and the close chain runs in the browser.

---

## Step 2 — The template

The page sits inside one `<div dj-root>`. That attribute marks the
region djust patches, and djust stamps `dj-view` onto it when it
renders the page, which is what connects the page to `ProfileView`.
Without it the chains still run (they are client-side), but **Save**
never reaches the server. Put the fragment inside the page skeleton
from [your first LiveView](../getting-started/first-liveview.md).

```html
<!-- myapp/templates/profile.html -->
<div dj-root>
<section class="profile">
  <h1>Your profile</h1>
  <dl>
    <dt>Name</dt>   <dd>{{ name }}</dd>
    <dt>Email</dt>  <dd>{{ email }}</dd>
  </dl>
  <button type="button" dj-click="{{ open_modal }}">Edit profile</button>
</section>

<!-- The modal lives in the DOM at all times, hidden by default. -->
<div id="edit-modal" class="modal" style="display: none;">
  <form class="modal-card" dj-submit="save_profile">
    <h2>Edit profile</h2>

    <label>
      Name
      <input id="name-input" name="name" value="{{ name }}" required />
    </label>
    <label>
      Email
      <input name="email" type="email" value="{{ email }}" required />
    </label>

    <div class="modal-actions">
      <button type="button" dj-click="{{ close_modal }}">Cancel</button>
      <button type="submit">Save</button>
    </div>
  </form>
</div>

<!-- Toast renders into existence when saved_message is set. -->
{% if saved_message %}
  <div id="toast" class="toast" dj-hook="AutoDismiss">
    {{ saved_message }}
    <button type="button" dj-click="{{ dismiss_toast }}" aria-label="Dismiss">&times;</button>
  </div>
{% endif %}
</div>
```

Two patterns to call out:

| Pattern | Effect |
|---|---|
| `dj-click="{{ open_modal }}"` | Run the JS chain stored in `self.open_modal` when clicked. The chain is serialized to JSON the client interprets. Only `dj-click` accepts a chain; `dj-submit` and the other event attributes take a handler name. |
| `dj-hook="AutoDismiss"` | Runs a JS chain when the element mounts. There is no attribute that schedules a chain on mount, so the delay lives in the hook:<br>`window.djust.hooks.AutoDismiss = { mounted() { setTimeout(() => this.js().hide().exec(this.el), 3000); } };` |

---

## Step 3 — The CSS

```css
.modal {
  position: fixed;
  inset: 0;
  background: rgb(0 0 0 / 0.5);
  display: flex;          /* set by JS.show(display="flex") */
  align-items: center;
  justify-content: center;
  z-index: 50;
  animation: fade-in 200ms ease-out;
}
.modal-card {
  background: var(--color-bg, #fff);
  border-radius: 8px;
  padding: 24px;
  width: min(420px, 100% - 32px);
}
.modal-actions {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
  margin-top: 16px;
}

.toast {
  position: fixed;
  bottom: 24px;
  right: 24px;
  padding: 12px 18px;
  background: var(--color-accent, #10b981);
  color: white;
  border-radius: 6px;
  box-shadow: 0 4px 12px rgb(0 0 0 / 0.15);
  z-index: 60;
  animation: fade-in 200ms ease-out;
}

@keyframes fade-in {
  from { opacity: 0; }
  to   { opacity: 1; }
}

body.modal-open { overflow: hidden; }
```

A CSS animation starts whenever an element goes from
`display: none` to displayed, so `JS.show()` replays `fade-in`
every time the modal opens, and the toast fades in when the
render inserts it. No timer in Python has to match the
stylesheet.

---

## Step 4 — Wire the URL and try it

```python
# myapp/urls.py
from django.urls import path
from .views import ProfileView

urlpatterns = [path("profile/", ProfileView.as_view())]
```

Visit `/profile/`:

- Click **Edit profile** → modal fades in instantly. Open the
  Network tab — no WebSocket frame fires.
- Type changes in the form → no events. The form is plain HTML
  until you submit.
- Click **Cancel** → modal closes instantly. Still no network.
- Click **Save** → a single WebSocket frame sends the form to
  `save_profile`. The server updates the profile, sets
  `saved_message` and pushes the close chain; the modal closes,
  the toast appears, and it hides itself 3 seconds later.

The "feel" is dramatically faster than a same-feature page where
every interaction round-trips. The server still has authority
over the actual save — but the UX overhead is local.

---

## What just happened, end to end

```
   Browser                                Server
      │                                       │
      │ click "Edit profile"                  │
      │ ── JS chain runs locally:             │
      │    body.classList.add("modal-open")   │
      │    #edit-modal.style.display="flex"   │
      │    (CSS fade-in animation runs)       │
      │    focus #name-input                  │
      │ (no WS frame)                         │
      │                                       │
      │ user types name + email               │
      │ (no events)                           │
      │                                       │
      │ click Save → dj-submit sends the form │
      │ ─────────────────────────────────────► save_profile()
      │                                       │ self.name = ...
      │                                       │ self.email = ...
      │                                       │ self.saved_message = "Profile saved."
      │                                       │ self.push_commands(close chain)
      │ ◄ patch: <div id="toast">…</div> ─────│
      │ ◄ djust:exec: hide #edit-modal, ──────│
      │   remove modal-open from body         │
      │  toast renders; AutoDismiss hook's    │
      │  mounted() hides it after 3 s         │
```

Two round-trips eliminated (opening the modal and cancelling it)
plus one preserved (the actual save, which also closes the
modal). That's the JS Commands sweet
spot.

---

## Where to go next

- **Animated dropdowns:** the same show-plus-CSS-animation pattern works for
  `<details>` panels, accordion sections, kebab menus.
- **Confirm-before-action:** chain `JS.show("#confirm-dialog")`
  on a Delete button, then have the dialog's primary action
  push the actual `delete` event. No round-trip until the user
  confirms.
- **Tabs whose content is already in the DOM:** ship all tabs
  in the initial render, then switch via
  `JS.hide(".tab-panel").show("#tab-N")` — instant, no
  re-render.
- **Sticky form errors:** when a server validation fails, push
  a JS chain from the handler with `self.push_commands()` that
  adds `.invalid` to the bad fields. `JS.dispatch("CustomEvent")` to integrate with any JS
  validation library you already have.
- **Don't reach for a state library.** "I need a Redux/Zustand
  store for this modal-open state" — usually you don't. JS
  Commands cover ~80% of what those libraries are bolted on for.

The decision tree (`server-state → @event_handler`,
`server-confirms → @optimistic`, `client-only → JS Commands`)
is the entire shape of UI-state ownership in djust. Once it
clicks, "where does this state live?" stops being a hard question.
