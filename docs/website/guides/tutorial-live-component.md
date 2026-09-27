---
title: "Tutorial: Build a reusable LiveComponent"
slug: tutorial-live-component
section: guides
order: 72
level: intermediate
description: "Build a star-rating widget once, drop it onto any page, and have parent views react to its events. LiveComponents have their own state, lifecycle, and event handlers — like a LiveView but composable. Includes the right way for child components to talk back to their parents."
---

# Tutorial: Build a reusable LiveComponent

LiveViews answer "what does this URL render?" LiveComponents
answer "what's a reusable interactive widget?" — a star rating,
a tag picker, a code-editor pane, a notification bell. The same
widget mounted in multiple parent views, each instance with its
own state, all communicating up to their parent through a clean
event channel.

If you've used React, this is roughly "function components with
their own `useState`." If you've used Phoenix LiveView, it's
exactly `LiveComponent` — same name, same shape.

By the end of this tutorial you'll have:

- A **`StarRating` component** that takes a current rating + a
  read-only flag, renders 5 stars, and lets the user click to
  change the rating.
- The component **owns its own UI state** (hover preview, active
  star), so the parent view's code never deals with mouse moves.
- When the user **commits a rating**, the component **sends a
  `rating_changed` event** up to its parent view, which persists
  to the DB.
- The same component **dropped into two different parent views**
  (a movie review page and a restaurant review page) without any
  copy-paste.

| You'll learn | Documented in |
|---|---|
| `LiveComponent` lifecycle (`mount`, `get_context_data`, event handlers) | [Components](components.md) |
| `self.trigger_update()` after changing component state | [Components](components.md) |
| `self.send_parent("event", payload)` and the parent's `handle_component_event()` | [Components](components.md) |
| When to use a `Component` (stateless) vs a `LiveComponent` (stateful) | This tutorial |

> **Prerequisites:** [Quickstart](../getting-started/installation.md), the [optimistic
> updates tutorial](tutorial-optimistic-updates.md) for context
> on the @event_handler shape. Familiarity with LiveView mount /
> event-handler patterns helps.

---

## Component vs LiveComponent

Two flavors, picked by the question "does this widget have
state?":

| | `Component` | `LiveComponent` |
|---|---|---|
| State | None | Per-instance, lives across re-renders |
| Events | Handled by the parent view | Its own `@event_handler` methods |
| Cost | ~1-10us per render (Rust) | ~50-100us per render (template) |
| Use for | Badges, icons, status pills, formatters | Tabs, modals, ratings, pickers, anything interactive |

The decision is binary: if the widget needs to remember
*anything* between renders (selected tab, hovered star, draft
text), it's a `LiveComponent`. Otherwise it's a `Component` and
renders 50× faster.

This tutorial is about LiveComponents — `Component` is covered in
the [Components guide](components.md).

---

## Step 1 — The component class

```python
# myapp/components/star_rating.py
from djust import LiveComponent
from djust.decorators import event_handler


class StarRating(LiveComponent):
    template_name = "components/star_rating.html"

    def mount(self, **kwargs):
        # `kwargs` contains whatever the parent passed to the
        # constructor: StarRating(value=..., read_only=...)
        self.value = kwargs.get("value", 0)
        self.max = kwargs.get("max", 5)
        self.read_only = kwargs.get("read_only", False)
        # Explicit star numbers — there is no `times` filter.
        self.stars = list(range(1, self.max + 1))
        # Per-instance UI state — the parent never sees `hover`.
        self.hover = 0

    def get_context_data(self):
        # A LiveComponent's template sees only what this returns
        # (plus `component_id`, which the framework adds).
        return {
            "value": self.value,
            "max": self.max,
            "read_only": self.read_only,
            "stars": self.stars,
            "hover": self.hover,
        }

    @event_handler
    def hover_star(self, n: int = 0, **kwargs):
        if self.read_only:
            return
        self.hover = n
        self.trigger_update()

    @event_handler
    def hover_clear(self, **kwargs):
        if self.read_only:
            return
        self.hover = 0
        self.trigger_update()

    @event_handler
    def commit_rating(self, n: int = 0, **kwargs):
        if self.read_only or n < 1 or n > self.max:
            return
        self.value = n
        self.hover = 0
        self.trigger_update()
        # Tell the parent — it decides what to do (persist to DB,
        # log the action, congratulate the user, etc.).
        self.send_parent("rating_changed", {"value": n})
```

Four things to call out:

1. **`mount(**kwargs)` is called once, when the parent constructs
   the component** — `StarRating(value=movie.rating,
   read_only=True)`. The constructor kwargs are the component's
   "props" channel.
2. **`get_context_data()` is required.** The default returns an
   empty dict, so without it the template would see only
   `component_id`.
3. **`self.trigger_update()` marks the component changed** so the
   parent view re-renders it. The re-render goes through the
   normal VDOM diff, so the patch that reaches the browser covers
   only the stars whose classes changed.
4. **`self.send_parent(event_name, payload)` is how the component
   talks back to the parent.** The parent receives it in
   `handle_component_event(component_id, event, data)` — one
   method per view, keyed by event name.

---

## Step 2 — The component template

```html
<!-- myapp/templates/components/star_rating.html -->
<div class="star-rating {% if read_only %}is-read-only{% endif %}"
     dj-mouseleave="hover_clear" data-component-id="{{ component_id }}">
  {% for star_n in stars %}
    <button
      type="button"
      class="star {% if hover >= star_n or value >= star_n and hover == 0 %}is-filled{% endif %}"
      dj-mouseenter="hover_star"
      dj-click="commit_rating"
      dj-value-n="{{ star_n }}"
      aria-label="Rate {{ star_n }} of {{ max }}"
      {% if read_only %}disabled{% endif %}
    >★</button>
  {% endfor %}
</div>
```

Three patterns:

| Pattern | Effect |
|---|---|
| `data-component-id="{{ component_id }}"` on the root element | Tells the framework which component instance an event targets. The client walks up from the element that fired the event to the nearest `data-component-id`, so once on the root covers every button. A `template_name` component must add it itself; only inline `template` components are wrapped automatically. |
| `dj-value-n="{{ star_n }}"` | Sent as the `n` argument with both the `dj-mouseenter` and the `dj-click` event; the `n: int` annotation coerces it. |
| `dj-mouseleave="hover_clear"` on the wrapper | Cleanup event — when the cursor leaves the rating row, hover preview resets. Without this, the last-hovered preview would stick after the cursor moved away. |

---

## Step 3 — Drop it into a parent view

```python
# myapp/views.py
from djust import LiveView

from .components.star_rating import StarRating
from .models import Movie


class MovieDetailView(LiveView):
    template_name = "movie_detail.html"
    login_required = True

    def mount(self, request, *, movie_id: int, **kwargs):
        self.movie = Movie.objects.get(pk=movie_id)
        # Each user's own rating, if any
        self.user_rating = self.movie.ratings.filter(
            user=request.user,
        ).values_list("value", flat=True).first() or 0
        # Two instances, each with its own component_id.
        self.my_rating = StarRating(value=self.user_rating, max=5)
        self.average_rating = StarRating(
            value=self.movie.average_rating_int, max=5, read_only=True,
        )

    def handle_component_event(self, component_id, event, data):
        """Receives every send_parent() from this view's components."""
        # Only the interactive instance sends rating_changed; the
        # read-only one returns before send_parent().
        if event == "rating_changed":
            value = data["value"]
            self.movie.ratings.update_or_create(
                user=self.request.user,
                defaults={"value": value},
            )
            self.user_rating = value
```

```html
<!-- myapp/templates/movie_detail.html -->
<article class="movie">
  <h1>{{ movie.title }}</h1>
  <p>{{ movie.tagline }}</p>

  <div class="movie-rating">
    <p>Your rating</p>
    {{ my_rating }}
  </div>

  <div class="movie-rating">
    <p>Average ({{ movie.ratings_count }} reviews)</p>
    {{ average_rating }}
  </div>
</article>
```

Two instances of the same component on one page:

- **The user's rating** — interactive, mounted with the user's
  current value (or 0).
- **The community average** — read-only, displays the rounded
  average.

When the user clicks a star in the first one, the event carries
that instance's `component_id`, so `StarRating.commit_rating` runs
on it. It calls `send_parent`, and the parent's
`handle_component_event` persists the rating and updates
`user_rating`. The read-only instance's HTML doesn't change, so
the diff sends no patch for it.

---

## Step 4 — Drop the same component on a different page

The component is a Python class. Drop it onto any LiveView
template:

```html
<!-- myapp/templates/restaurant_detail.html -->
<article class="restaurant">
  <h1>{{ restaurant.name }}</h1>

  {{ my_rating }}

  {# ... rest of the restaurant page ... #}
</article>
```

```python
# myapp/views.py
class RestaurantDetailView(LiveView):
    # ... mount as for MovieDetailView, with
    #     self.my_rating = StarRating(value=self.user_rating, max=5)

    def handle_component_event(self, component_id, event, data):
        # Same handler shape; different model + persistence
        if event == "rating_changed":
            self.restaurant.ratings.update_or_create(
                user=self.request.user,
                defaults={"value": data["value"]},
            )
            self.user_rating = data["value"]
```

The component's hover state, click handlers, and template all
work identically. The parent just decides what to do with the
bubbled event. Two persistence backends, one widget.

---

## Why no shared mutable state across instances

Each `StarRating(...)` you construct is a **fresh instance**
with its own `self.value`, `self.hover`, etc. Two
instances of `StarRating` on the same page don't share state.
That's deliberate — if they did, hovering one would highlight
both.

If you DO want shared state (e.g. one component reflects another
component's selection), wire it through the parent: child A
fires an event, and the parent's `handle_component_event` sets
the new value on child B (`self.child_b.update(value=...)`)
before the render.
Don't try to peer-to-peer between siblings; the framework's
event dispatch goes through the parent, and that's the only
clean shape.

---

## When to reach for a LiveComponent vs alternatives

| You want… | Use |
|---|---|
| One-off interactive UI on a single page | Just put it in the LiveView |
| Stateless reusable HTML (badges, icons, status pills) | `Component` (faster, no state) |
| Stateful reusable widget used on multiple pages | **`LiveComponent`** |
| Same widget but the state lives on the parent | Pass `value=` + child fires events; don't store on the child |
| Cross-page state (cart, notifications) | Parent LiveView state; mount component instances per page |

The boundary that confuses people: when does a thing that COULD
be a LiveComponent become one that SHOULD be?

- Used on >1 page → yes, probably.
- Has its own UI state that the parent doesn't need → yes.
- Just markup + click handlers that talk to the parent → not
  worth the abstraction; inline it.

---

## What just happened, end to end

```
   Browser                              Server
       │                                    │
       │ GET /movies/42/                   │
       │ ────────────────────────────────► MovieDetailView.mount()
       │                                    │   self.movie = Movie.objects.get(...)
       │                                    │   self.user_rating = 4
       │                                    │
       │                                    │   self.my_rating = StarRating(value=4)
       │                                    │   → StarRating.mount(value=4)
       │                                    │     → hover=0
       │                                    │ template renders {{ my_rating }}:
       │                                    │   5 stars, 4 filled
       │ ◄ initial HTML ────────────────────│
       │                                    │
       │ user hovers star 5                 │
       │ ────────────────────────────────► StarRating.hover_star(n=5)
       │                                    │   self.hover = 5
       │                                    │   self.trigger_update()
       │ ◄ patch (5 stars filled) ──────────│   ← only the changed stars are patched
       │                                    │
       │ user clicks star 5                 │
       │ ────────────────────────────────► StarRating.commit_rating(n=5)
       │                                    │   self.value = 5
       │                                    │   self.hover = 0
       │                                    │   self.trigger_update()
       │                                    │   self.send_parent("rating_changed", {value: 5})
       │                                    │     ↓
       │                                    │   MovieDetailView.handle_component_event(
       │                                    │       <id>, "rating_changed", {value: 5})
       │                                    │     movie.ratings.update_or_create(...)
       │                                    │     self.user_rating = 5
       │ ◄ patch (changed stars only) ──────│   ← one render for the whole event
```

The hover events are handled by the component (each one is a
round-trip, but the patch is a few bytes of class-toggle DOM).
The commit travels up via `send_parent` and the parent persists.

---

## Where to go next

- **Tabs panel:** `LiveComponent` with `selected_tab` state +
  child slot rendering. Each tab's content is in the DOM but
  hidden via `JS Commands`. Switching tabs is purely client-side
  ([JS Commands tutorial](tutorial-js-commands.md)).
- **Notification bell:** a component each page's view constructs;
  the view subscribes to a Postgres LISTEN channel for new
  notifications ([real-time comments tutorial](tutorial-real-time-comments.md)
  pattern) and updates the bell.
- **Form-field widgets:** `EmailField`, `PhoneField`, `MoneyField`
  components with built-in validation. The parent form passes
  `value=` and listens for `change` events.
- **Component tests:** `djust.testing.LiveComponentTestClient`
  (see the [Testing guide](testing.md)) mounts a component in
  isolation — `LiveComponentTestClient(StarRating).mount(value=3,
  max=5)` — so you can `send_event("hover_star", n=4)` and assert
  on its state.
- **Typed outputs instead of `handle_component_event`:** the
  interactive components in `djust.components.interactive` report
  typed outputs that a view subscribes to with
  `@menu.on.selected` (djust 1.3). See
  [Interactive Components](interactive-components.md).
- **A ready-made rating:** djust ships a stateless `Rating`
  component (`from djust.components import Rating`) whose star
  clicks go to a view handler. Reach for it when the view should
  own the value.
- **Don't forget `Component`** for stateless cases. Status badges,
  date formatters, currency formatters, "loading" placeholders —
  all faster as `Component` because they don't need the
  `LiveComponent` lifecycle plumbing.

The core shape — `mount` for setup, `get_context_data` for the
template, `@event_handler` for interaction, `send_parent` for
bubble-up — is most of what a `LiveComponent` needs. Once you've built one, the next ten are
mostly copy-paste.
