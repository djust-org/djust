---
title: "Tutorial: Show who's reading right now with presence"
slug: tutorial-presence
section: guides
order: 67
level: intermediate
description: "Add a 'who's here' avatar bar to any LiveView in 15 lines. Uses PresenceMixin's join/leave callbacks plus the built-in heartbeat to keep it accurate even through reconnects, idle tabs, and crashed clients."
---

# Tutorial: Show who's reading right now with presence

Every shared page benefits from a tiny "who else is here" affordance.
On a chat thread it's reassurance. On a document, it's coordination.
On a dashboard, it's situational awareness ("Sarah's on call so this
graph spike is already known"). The pattern is the same: render an
avatar strip with the people currently viewing.

djust ships `PresenceMixin` for this. By the end of this tutorial
you'll have:

- A small avatar bar at the top of a document page showing every
  user currently viewing it, with their name on hover.
- **Live join/leave** — bar updates within ~200 ms when anyone
  opens or closes the page.
- **Accurate through crashes** — the client's 30-second ping doubles
  as the presence heartbeat, and a presence with no heartbeat for
  60 seconds is dropped, so a crashed tab doesn't linger.
- A subtle **flash message** when another user joins or leaves
  ("Alice joined", "Bob left"), implemented with the existing
  flash-message system.

| You'll learn | Documented in |
|---|---|
| `PresenceMixin` setup + `track_presence()` | [Real-Time Presence](presence.md) |
| `presence_key` for grouping presences by resource | [Real-Time Presence](presence.md) |
| `_on_presence_change` for reacting to other users | [Real-Time Presence](presence.md) |
| Pairing presence with [Flash Messages](flash-messages.md) | This tutorial |
| Why presence ≠ session — the design rationale | This tutorial |

> **Prerequisites:** [Quickstart](../getting-started/first-liveview.md) and the
> [real-time comments tutorial](tutorial-real-time-comments.md)
> are nice context (presence and broadcast are companion patterns
> for collaborative UIs). A backend with either Redis (recommended)
> or in-memory channels.

---

## What you're building

```
┌─────────────────────────────────────────────────────┐
│  Q4 Planning Doc                  ●●●● 4 here       │
│                                   ╳╳╳╳              │
│                                  alice·bob·carla·me │
├─────────────────────────────────────────────────────┤
│  ## Goals                                           │
│  Lorem ipsum...                                     │
│                                                     │
│  Bob just joined. ───────── (flash, fades 4s)       │
└─────────────────────────────────────────────────────┘
```

Each colored dot is one connected viewer. Hover for the name.
The flash message appears briefly when someone joins/leaves.

---

## Step 1 — The view + PresenceMixin wiring

```python
# myapp/views.py
import hashlib

from djust import LiveView
from djust.decorators import event_handler
from djust.presence import PresenceMixin

from .models import Document


def _color_for_user(user) -> str:
    """Stable color from username — same user gets the same color
    on every page they appear on. Better than random because the
    visual recognition transfers across docs."""
    h = hashlib.sha1(user.username.encode()).hexdigest()
    return f"#{h[:6]}"


class DocumentView(PresenceMixin, LiveView):
    template_name = "document.html"
    login_required = True

    # Presence key isolates each document's presence list. The
    # {doc_id} placeholder is filled from the view's own attributes,
    # so set self.doc_id before calling track_presence().
    presence_key = "document:{doc_id}"

    def mount(self, request, *, doc_id: int, **kwargs):
        self.doc_id = doc_id
        self.doc = Document.objects.get(pk=doc_id)
        self.track_presence(meta={
            "name": request.user.username,
            "color": _color_for_user(request.user),
        })
        self._seen = self._names()

    def _names(self):
        # Each presence record is {"id", "joined_at", "meta"}; the dict
        # passed to track_presence() is under "meta".
        return {p["id"]: p["meta"].get("name", "") for p in self.list_presences()}

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["presences"] = self.list_presences()
        return ctx

    @event_handler
    def _on_presence_change(self, **kwargs):
        # Runs on every OTHER viewer's session when someone joins or
        # leaves. Keep the built-in online_count refresh.
        super()._on_presence_change(**kwargs)
        now = self._names()
        for user_id in now.keys() - self._seen.keys():
            self.put_flash("info", f"{now[user_id]} joined")
        for user_id in self._seen.keys() - now.keys():
            self.put_flash("info", f"{self._seen[user_id]} left")
        self._seen = now
```

Three things to call out:

1. **`presence_key`** uses `{attribute}` substitution from the view's
   attributes. For a doc page, presences for `/docs/42/` are isolated
   from presences for `/docs/43/`. Two users on different docs don't
   see each other.
2. **`track_presence(meta=...)`** is what registers you. The
   `meta` dict is returned to every connected viewer of the same
   key, so put in it only what every viewer may see (`name`,
   `color`, `is_typing`, etc.). Presence registers only over the
   WebSocket; the first HTTP render skips it.
3. **`_on_presence_change`** is the hook that fires on the OTHER
   viewers' sessions when someone joins or leaves. It carries no
   payload, so the view diffs `list_presences()` against the list it
   saw last time. (`handle_presence_join` / `handle_presence_leave`
   exist too, but they run only on the session that is itself
   joining or leaving, so they can't tell others.) Override it with
   `@event_handler` and call `super()` to keep the built-in
   `online_count` refresh.

---

## Step 2 — The avatar bar template

```html
<!-- myapp/templates/document.html -->
{% load live_tags %}

<header class="doc-header">
  <h1>{{ doc.title }}</h1>

  <div class="presence" aria-label="{{ online_count }} viewers online">
    <ul class="presence-dots">
      {% for p in presences %}
        <li dj-key="{{ p.id }}">
          <span
            class="presence-dot"
            style="background: {{ p.meta.color }}"
            title="{{ p.meta.name }}"
            aria-label="{{ p.meta.name }} is viewing"
          ></span>
        </li>
      {% endfor %}
    </ul>
    <span class="presence-count">{{ online_count }} here</span>
  </div>
</header>

<article class="doc-body">
  {% djust_markdown doc.body %}
</article>

{# Flash messages from put_flash() above.
   {% dj_flash %} renders the container itself; do not hand-roll it. #}
{% load djust_flash %}
{% dj_flash %}
```

Two patterns the template uses:

| Pattern | Effect |
|---|---|
| `{% for p in presences %}` over `list_presences()`, `{{ online_count }}` | Each render walks the current presence list; your metadata is under `p.meta`. `PresenceMixin` keeps `online_count` up to date, and `_on_presence_change` re-renders the other viewers when someone joins or leaves. |
| `{% dj_flash %}` from `djust_flash` | Renders the flash container (`id="dj-flash-container"`, marked `dj-update="ignore"` so patches leave it alone). Messages queued with `put_flash()` appear there without a full re-render. See [Flash Messages](flash-messages.md). |

---

## Step 3 — The CSS

```css
.doc-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 20px;
  border-bottom: 1px solid var(--color-border, #e5e7eb);
}
.presence {
  display: flex;
  align-items: center;
  gap: 10px;
}
.presence-dots {
  display: flex;
  list-style: none;
  margin: 0;
  padding: 0;
}
.presence-dot {
  display: inline-block;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  border: 2px solid var(--color-bg, #fff);
  margin-left: -6px;     /* overlap stacking */
  cursor: default;
  animation: presence-fade-in 0.25s ease-out;
}
.presence-dots li:first-child .presence-dot {
  margin-left: 0;
}
.presence-count {
  font-size: 12px;
  font-family: var(--font-mono);
  color: var(--color-muted, #6b7280);
}
@keyframes presence-fade-in {
  from { transform: scale(0.5); opacity: 0; }
  to   { transform: scale(1);   opacity: 1; }
}
```

The negative margin makes the dots overlap into a stack. The
fade-in animation is a small touch — when someone joins, their
dot pops into existence rather than appearing abruptly.

---

## Step 4 — Try it with two browsers

Open `/docs/1/` in two browsers logged in as different users.
Within ~200 ms of the second user opening the page, the first
user's avatar bar grows from 1 dot to 2 and a flash message
"bob joined" appears. Close the second tab — the dot disappears
and "bob left" flashes on the first.

Now try a harder case: open three browsers as `alice`, `bob` and
`carla`, then kill bob's browser process instead of closing the
tab. If the server never sees the socket close, bob's record stays
until his heartbeat is 60 seconds old; after that he is dropped
from the list the next time it is read. When bob comes back, his
page mounts again and he rejoins.

---

## Why presence ≠ session

A common misread of presence is "show me everyone with an active
session." That's not what `PresenceMixin` does — and it's not what
you want. Three reasons:

1. **Presence is keyed by user, not by tab.** A logged-in user's
   presence id is their user id, so alice with the doc open in two
   tabs is one presence. Anonymous visitors collapse per browser
   session; set `presence_unique_per_connection = True` to count
   each anonymous tab separately.
2. **A session that's been idle for an hour isn't presence.** The
   browser tab might be open behind 50 others, the user gone for
   coffee — but a tab whose WebSocket is gone stops heartbeating,
   and its presence is dropped after 60 seconds.
3. **Presence is per-resource, not per-user.** With
   `presence_key = "document:{doc_id}"`, alice viewing doc 42 and
   bob viewing doc 43 don't see each other. That's the whole point
   — "who's looking at *this*" is the question presence answers.

If you need a global "all online users" list, that's a separate
query, not presence.

---

## What just happened, end to end

```
   Browser A (alice)              Server                  Browser B (bob)
       │                              │                          │
       │  open /docs/1/               │                          │
       │ ──────────────────────────► mount()                     │
       │                              │ track_presence({alice})  │
       │ ◄ render (1 dot: alice)      │                          │
       │                              │                          │
       │                              │  ◄────── open /docs/1/ ──│
       │                              │     mount()              │
       │                              │  track_presence({bob})   │
       │                              │  → push _on_presence_    │
       │                              │    change to alice       │
       │ ◄ patch: dot + flash ────────│ ─────► render ──────────►│
       │   "bob joined"               │       (2 dots)           │
       │                              │                          │
       │  ... 5 minutes later ...     │                          │
       │                              │                          │
       │                              │  ◄──── socket closes ────│ (tab closed)
       │                              │  untrack_presence(bob)   │
       │                              │  → push _on_presence_    │
       │ ◄ patch: dot - flash ────────│    change to alice       │
       │   "bob left"                 │                          │
```

Every effect — render, flash, leave — was a state change or a
`put_flash()`. The framework handles the diff streaming and
heartbeat plumbing.

---

## Where to go next

- **Live cursors:** swap `PresenceMixin` for `LiveCursorMixin` and drop the
  `{% cursors %}` overlay into the template. That tag ships in the optional
  components package, so add `djust.components` to `INSTALLED_APPS` and
  `{% load djust_components %}` first. You get Figma-style remote cursors. See [Real-Time Presence § cursors](presence.md).
- **Typing indicators:** put `is_typing: true` in the meta dict
  while the user is in the textarea (set on focus, clear on blur
  or after a debounce). Other viewers see a "alice is typing…"
  marker.
- **Many rooms in one view:** if one view serves many documents,
  `presence_broadcast_scoped = True` makes a join wake only the
  sessions on the same document. See [Real-Time
  Presence](presence.md).
- **Persisted "last seen":** combine presence with a periodic
  `Presence.objects.update_or_create(user=…, doc=…, ts=now)` write
  so you can show "Last viewed by alice 12 minutes ago" even when
  no one is currently here.

The five-line shape of this tutorial — `PresenceMixin` +
`presence_key` + `track_presence(meta=…)` + `list_presences()` +
`_on_presence_change` — is the entire presence API. Once
it clicks, "who's here" becomes a UI primitive you can drop into
any LiveView in a few minutes.
