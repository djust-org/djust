---
title: "Tutorial: Build a server-driven guided product tour"
slug: tutorial-guided-tour
section: guides
order: 77
level: advanced
description: "Build an in-app tour that walks new users through your dashboard step-by-step — highlighting buttons, scrolling to sections, popping tooltips — with the entire script written in Python on the server. Uses self.push_commands() to send JS Command chains down the WebSocket. The DOM dances; you never touch JavaScript."
---

# Tutorial: Build a server-driven guided product tour

The standard guided-tour libraries (Intro.js, Shepherd, Driver.js)
all live in JavaScript-land: a JSON config of selectors and tooltip
text, a JS module that walks them, and a separate stylesheet for
the highlight cutouts. Every step is duplicated between your
Django views (which know what data exists for the user) and the
JS config (which knows where to point arrows).

djust flips this with **`self.push_commands(chain)`** — the
server can send any [`JS Command chain`](tutorial-js-commands.md)
to the client and have it execute. The tour script lives in
Python, branches on user state in Python, and the framework
ships the DOM ops down the WebSocket as needed.

By the end of this tutorial you'll have:

- A **"Show me around"** button on a fresh user's dashboard.
- A 4-step tour that **highlights the Create button**, then
  **scrolls to the Activity feed**, then **pops a tooltip on
  the search box**, then **opens the help menu**.
- **Branching:** if the user has zero projects, the tour talks
  about creating one; if they have projects, it talks about
  managing them.
- **Skip** — the user can click "Skip" mid-tour; the step they
  stopped at is recorded server-side.
- **One-time-only** — the tour fires for new users; once
  completed, it doesn't fire again.

| You'll learn | Documented in |
|---|---|
| `self.push_commands(JS.…)` to drive client UI from Python | [Server-Driven UI](server-driven-ui.md) |
| Branching tour scripts based on server state | This tutorial |
| `JS` chains for highlight + scroll + tooltip | [JS Commands](js-commands.md) |
| When server-driven UI beats hooks (and when it doesn't) | This tutorial |

> **Prerequisites:** [Quickstart](../getting-started/installation.md), the [JS
> Commands tutorial](tutorial-js-commands.md), and
> familiarity with `state(...)` from earlier tutorials. A
> dashboard view to bolt the tour onto.

---

## The core idea

JS Commands ([tutorial #11](tutorial-js-commands.md)) are
chains of DOM operations the client runs. Normally you wire one
to a `dj-click` and the user triggers it. With
`push_commands`, the **server triggers it** — same chain,
different fire mechanism.

```python
class DashboardView(LiveView):
    @event_handler
    def show_tour_step_1(self, **kwargs):
        self.push_commands(
            JS.add_class("tour-highlight", to="#btn-create")
              .focus("#btn-create")  # focusing also scrolls it into view
        )
```

The user clicks "Show me around" → the server runs
`show_tour_step_1` → it pushes the chain → the client adds the
class and focuses the button, which scrolls it into view. No JS
authored.

The key insight: the **same `JS` builder** that powers
`dj-click="{{ chain }}"` (client-triggered) also powers
`self.push_commands(chain)` (server-triggered). One mental model,
two trigger mechanisms.

---

## Step 1 — Tour state model

```python
# myapp/models.py
from django.conf import settings
from django.db import models


class TourProgress(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="tour_progress",
    )
    completed = models.BooleanField(default=False)
    skipped_at_step = models.PositiveIntegerField(null=True)
    last_seen_at = models.DateTimeField(auto_now=True)
```

A single row per user tracks tour completion. The tour fires
only when this row is missing or `completed=False`.

---

## Step 2 — The view

```python
# myapp/views.py
from djust import LiveView, event_handler, state
from djust.js import JS

from .models import Project, TourProgress


TOUR_STEPS = [
    "step_1_create_button",
    "step_2_activity_feed",
    "step_3_search_box",
    "step_4_help_menu",
]


class DashboardView(LiveView):
    template_name = "dashboard.html"
    login_required = True

    show_tour_button = state(False)
    tour_active = state(False)
    tour_step = state(0)
    tour_total = state(len(TOUR_STEPS))
    has_projects = state(False)

    def mount(self, request, **kwargs):
        self.has_projects = Project.objects.filter(owner=request.user).exists()
        progress, _ = TourProgress.objects.get_or_create(user=request.user)
        # Show the "Show me around" button only to users who haven't
        # completed (or skipped past) the tour yet.
        self.show_tour_button = not progress.completed

    @event_handler
    def start_tour(self, **kwargs):
        self.tour_active = True
        self.tour_step = 1
        self._dispatch_step(1)

    @event_handler
    def next_tour_step(self, **kwargs):
        self.tour_step += 1
        if self.tour_step > len(TOUR_STEPS):
            self._finish_tour()
            return
        self._dispatch_step(self.tour_step)

    @event_handler
    def skip_tour(self, **kwargs):
        TourProgress.objects.filter(user=self.request.user).update(
            skipped_at_step=self.tour_step,
            completed=True,  # don't show again — they explicitly opted out
        )
        self.tour_active = False
        self.show_tour_button = False
        # Clean up any lingering tour classes.
        self.push_commands(
            JS.remove_class("tour-highlight", to=".tour-highlight")
        )

    def _finish_tour(self):
        TourProgress.objects.filter(user=self.request.user).update(completed=True)
        self.tour_active = False
        self.show_tour_button = False
        self.push_commands(
            JS.remove_class("tour-highlight", to=".tour-highlight")
              .show("#tour-complete-toast")
              # The fade is a CSS animation (Step 4). Commands don't
              # wait for each other, so a timed hide() here would run
              # at once rather than after the fade.
              .add_class("toast-fade-out", to="#tour-complete-toast")
        )

    def _dispatch_step(self, n: int):
        # Each step builds and pushes a JS chain. The chains are
        # written here in Python, but they execute in the browser.
        if n == 1:
            verb = "Create your first project" if not self.has_projects \
                   else "You can create more projects from"
            self.push_commands(
                JS.remove_class("tour-highlight", to=".tour-highlight")
                  .add_class("tour-highlight", to="#btn-create")
                  .focus("#btn-create")
                  .show("#tour-tooltip")
                  .set_attr("data-text", verb + " here.", to="#tour-tooltip")
            )
        elif n == 2:
            self.push_commands(
                JS.remove_class("tour-highlight", to=".tour-highlight")
                  .add_class("tour-highlight", to="#activity-feed")
                  .focus("#activity-feed")
                  .set_attr(
                      "data-text",
                      "Recent updates from your team show up here.",
                      to="#tour-tooltip",
                  )
            )
        elif n == 3:
            self.push_commands(
                JS.remove_class("tour-highlight", to=".tour-highlight")
                  .add_class("tour-highlight", to="#search-box")
                  .focus("#search-box")
                  .set_attr(
                      "data-text",
                      "Search anything in your workspace from here. "
                      "Tip: ⌘K opens it from any page.",
                      to="#tour-tooltip",
                  )
            )
        elif n == 4:
            self.push_commands(
                JS.remove_class("tour-highlight", to=".tour-highlight")
                  .add_class("tour-highlight", to="#help-menu-trigger")
                  .focus("#help-menu-trigger")
                  .show("#help-menu", display="block")
                  .set_attr(
                      "data-text",
                      "Stuck? Help, docs, and support live here.",
                      to="#tour-tooltip",
                  )
            )
```

Five things to call out:

1. **`push_commands(chain)`** is the one piece of new API. It's
   on every `LiveView` via `PushEventMixin`. Takes a `JS` builder,
   pushes a `djust:exec` event the client interprets.
2. **The chain always starts with `JS.remove_class("tour-highlight", to=".tour-highlight")`** — clean-up of the previous step's highlight before applying the new one. Without it, every step's highlight would accumulate and the page would look like a Christmas tree.
3. **Branching on server state** (`if not self.has_projects`)
   happens **in Python** — this is the whole point. The tour
   text adapts to the user's actual data.
4. **No JS module to write or maintain.** No JSON config of
   selectors. The selectors live next to the Python that knows
   what to highlight.
5. **`focus` doubles as the scroll.** The browser scrolls a
   focused element into view, so `focus` and `set_attr` — both
   standard commands — cover this tour. There is no built-in
   `scroll_to`; to scroll without moving focus, register a
   [custom command](js-commands.md#custom-commands) and call
   `JS.ext.scroll_to(to=...)`. See the
   [JS Commands guide](js-commands.md) for the full list.

---

## Step 3 — The template

```html
<!-- myapp/templates/dashboard.html -->
<header class="dash-head">
  <h1>Dashboard</h1>
  <input id="search-box" type="search" placeholder="Search…" />
  <button id="btn-create">+ New project</button>
  <button id="help-menu-trigger" type="button">?</button>
  <div id="help-menu" hidden>{# … help links … #}</div>
</header>

<section id="activity-feed" tabindex="-1">
  <h2>Activity</h2>
  {# … activity items … #}
</section>

{# Tour-only UI — hidden when not active. #}
{% if show_tour_button and not tour_active %}
  <button class="tour-launcher" type="button" dj-click="start_tour">
    Show me around &rarr;
  </button>
{% endif %}

{% if tour_active %}
  <div id="tour-tooltip" class="tour-tooltip" data-text="">
    <p class="tour-text">{# populated by JS chain via set_attr #}</p>
    <p class="tour-step-meta">Step {{ tour_step }} of {{ tour_total }}</p>
    <div class="tour-actions">
      <button type="button" dj-click="skip_tour">Skip</button>
      <button type="button" dj-click="next_tour_step">
        {% if tour_step < tour_total %}Next &rarr;{% else %}Done{% endif %}
      </button>
    </div>
  </div>
{% endif %}

{# Toast that flashes after tour completion. Hidden by default,
   shown by the _finish_tour chain. #}
<div id="tour-complete-toast" class="toast" hidden>
  Tour complete. Welcome aboard!
</div>
```

`tabindex="-1"` lets the chain focus the activity section (and
so scroll to it) without adding it to the Tab order.

The tooltip element is rendered once when the tour is active.
Its `data-text` attribute is populated via `JS.set_attr` from
each step's chain — so the same DOM node holds the
current-step text without re-rendering the whole template on
every step change.

---

## Step 4 — The CSS

```css
.tour-highlight {
  outline: 3px solid #f59e0b;
  outline-offset: 4px;
  border-radius: 4px;
  transition: outline-color 0.2s, outline-offset 0.2s;
  position: relative;
  z-index: 100;  /* lift above page content for visual prominence */
}

.tour-tooltip {
  position: fixed;
  bottom: 24px;
  right: 24px;
  width: 320px;
  padding: 16px;
  background: #fff;
  border-radius: 8px;
  box-shadow: 0 8px 24px rgba(0,0,0,0.15);
  z-index: 1000;
}

.tour-tooltip::before {
  content: attr(data-text);
  display: block;
  font-size: 14px;
  line-height: 1.5;
  margin-bottom: 12px;
}

.tour-step-meta {
  font-size: 11px;
  color: #6b7280;
  margin: 4px 0 12px;
}

.tour-actions {
  display: flex;
  justify-content: space-between;
  gap: 8px;
}

/* Added by the _finish_tour chain: hold for ~2 s, then fade. */
.toast-fade-out {
  animation: toast-fade-out 2.5s ease-in forwards;
}
@keyframes toast-fade-out {
  0%, 80% { opacity: 1; }
  100%    { opacity: 0; }
}
```

The `::before` pseudo-element + `attr(data-text)` is the trick —
the text is rendered from the data-text attribute via CSS, so
when the JS chain calls `set_attr("data-text", "...")`, the
displayed text updates instantly without any JS Templating or
innerHTML mutation.

---

## Step 5 — Try it as a new user

Sign up a fresh user, log in, hit `/dashboard/`. The "Show me
around" button is visible (no `TourProgress` row → defaults to
`completed=False`).

Click it:
- Step 1: the **Create** button gets a yellow outline; the page
  scrolls to it; the tooltip says "Create your first project here."
  (or "You can create more projects from here." if you already
  have some).
- Click **Next**: outline moves to the activity feed; tooltip
  updates.
- Click **Next**: outline moves to the search box; cursor
  focuses it; tooltip says to use ⌘K.
- Click **Next**: outline moves to the help menu; the menu
  opens; tooltip explains it.
- Click **Done**: toast shows "Tour complete. Welcome aboard!"
  for about 2 s and fades; `TourProgress.completed = True`. The
  "Show me around" button never appears again.

Reload the page. The button is gone. The tour is one-time-only.

---

## When server-driven UI beats other patterns

You could implement this tour with [JS Commands chains stored
in template variables](tutorial-js-commands.md) (no
`push_commands`):

```html
<button dj-click="{{ tour_step_1_chain }}">Step 1</button>
```

That works for fixed scripts. The win of `push_commands` is
**branching on server state** — the tour script changes based on
`self.has_projects`, the user's plan tier, recent activity, or
anything else the server knows. The browser doesn't need to.

Compare to [hooks](tutorial-chart-hook.md): hooks are
right when you're integrating a third-party library. They
require authoring JS. `push_commands` is right when the server
needs to drive purely-DOM changes from Python without any
custom JS at all. They overlap on a Venn diagram, but the
common-case answer is:

- "I need this third-party library mounted" → hook
- "I need to flash this button from the server" → `push_commands`

---

## What just happened, end to end

```
   Browser                                  Server
       │                                        │
       │ click "Show me around"                 │
       │ ──────────────────────────────────► start_tour
       │                                        │   self.tour_active = True
       │                                        │   self.tour_step = 1
       │                                        │   self._dispatch_step(1)
       │                                        │     branches on self.has_projects
       │                                        │     builds JS chain in Python
       │                                        │     self.push_commands(chain)
       │ ◄ djust:exec event ────────────────────│
       │   client interprets ops:               │
       │   1. remove_class tour-highlight       │
       │   2. add_class tour-highlight on       │
       │      #btn-create                       │
       │   3. focus #btn-create (scrolls to it) │
       │   4. show #tour-tooltip                │
       │   5. set_attr data-text on #tour-tooltip│
       │ (no further round-trip;                │
       │  template re-render also lands         │
       │  to mount the tooltip element)         │
       │                                        │
       │ click "Next"                           │
       │ ──────────────────────────────────► next_tour_step
       │                                        │   self.tour_step = 2
       │                                        │   self._dispatch_step(2) → push chain
       │ ◄ djust:exec ──────────────────────────│
       │   highlight moves, tooltip text        │
       │   updates, page scrolls                │
       │                                        │
       │ ... 2 more steps ...                   │
       │                                        │
       │ click "Done" on step 4                 │
       │ ──────────────────────────────────► next_tour_step
       │                                        │   self.tour_step = 5 > len(TOUR_STEPS)
       │                                        │   _finish_tour()
       │                                        │     TourProgress.completed = True
       │                                        │     push_commands(toast chain)
       │ ◄ djust:exec (toast + cleanup) ────────│
       │ ◄ patch (tour UI removed) ─────────────│
```

Two separate transports doing distinct things at once: HTML
patches (the tour UI mounting/unmounting via state changes) and
`djust:exec` events (the server driving DOM ops on existing
elements). They're complementary; nothing competes.

---

## Where to go next

- **AI-driven tours:** the `_dispatch_step` body could call an
  LLM ("describe what's on screen for this user") and use the
  reply to build the chain. The server has full context; the
  LLM picks the next step.
- **Remote support handoff:** a support agent in a separate
  view could `push_commands` to a customer's session — "let me
  highlight the button you need to click." Drop the chain
  through Channels.group_send and the support agent's view
  becomes a remote control.
- **Onboarding A/B:** branch the tour script on
  `request.user.experiment_group`. The server holds the
  treatment assignment; the client never needs to know which
  variant is active.
- **A declarative version:** `TutorialMixin` packages this pattern
  — a list of `TutorialStep`s with highlight, message, timeout and
  wait-for-event, plus a built-in bubble. See
  [Guided Tours with `TutorialMixin`](tutorials.md).
- **Automated UI testing:** record a `push_commands` sequence as
  a fixture; replay it in your test suite to drive a headless
  browser through the same DOM ops a real user would trigger.
- **Voice / accessibility integration:** wire a speech-to-text
  hook to a server handler that builds the appropriate JS chain
  from the recognized intent ("show me my recent activity" →
  `push_commands(JS.focus("#activity-feed"))`).

The shape — `self.push_commands(JS.…)` from any handler — is
the entire surface. It's small, but the patterns it enables
(server-driven tours, remote handoffs, AI-orchestrated UI) are
patterns that don't have a clean equivalent in client-side
frameworks. Worth knowing it exists; reach for it when "the
server should drive the next DOM op" is the right model.
