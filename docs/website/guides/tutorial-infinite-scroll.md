---
title: "Tutorial: Build infinite scroll with dj-viewport-bottom"
slug: tutorial-infinite-scroll
section: guides
order: 45
level: intermediate
description: "Add bidirectional infinite scroll to any list in 20 lines — a server event fires the moment the last row crosses into the viewport, the handler appends the next page, and the framework's per-row diff patches just the new rows in. No JavaScript, no IntersectionObserver wiring, no scroll listeners."
---

# Tutorial: Build infinite scroll with dj-viewport-bottom

Pagination is dead in modern web UI. Users expect to scroll until
they care to stop, not page-click through a 1990s table footer.
The implementation has historically been a pain — a JS scroll
listener, IntersectionObserver bookkeeping, race conditions when
two scroll events fire before the first response lands, and
remembering to dedupe when the user scrolls fast.

djust ships `dj-viewport-bottom` as a built-in primitive: put it on
the list container and it fires a server event the moment the
container's last child scrolls into view. The infrastructure
(IntersectionObserver setup, once-per-row firing, cleanup) is the
framework's problem.

By the end of this tutorial you'll have an activity feed where:

- The first 20 entries render on initial load.
- **Scrolling near the bottom auto-loads the next 20** —
  triggered by an IntersectionObserver on the last row, not a
  scroll listener.
- A small inline **"Loading more…"** row sits under the list while
  there is more to load.
- When you reach the end, the loading row is replaced by a quiet
  **"That's all."** marker — no perpetual loading state.
- **No JavaScript** is authored — the `dj-viewport-bottom`
  attribute does the work.

| You'll learn | Documented in |
|---|---|
| `dj-viewport-bottom` for last-row-fired server events | [Large Lists](large-lists.md) |
| Cursor-based pagination on the server side | This tutorial |
| `dj-key` per-row diffing for append-only updates | [Keyed vs Unkeyed Lists](keyed-lists-performance.md) |
| When to use `dj-virtual` instead of infinite scroll | This tutorial |

> **Prerequisites:** [Quickstart](../getting-started/installation.md), the [search-as-you-type
> tutorial](tutorial-search-as-you-type.md) (sets up the
> loading-state vocabulary). A queryset with enough rows to
> actually scroll past — a few hundred is enough.

---

## What you're building

```
┌──────────────────────────────────────┐
│ Activity                             │
├──────────────────────────────────────┤
│ alice pushed v1.4.2                  │
│ bob filed issue #1283                │
│ carla commented on #1281             │
│ ... 17 more rows ...                 │
│ alice merged PR #1280                │
├──────────────────────────────────────┤
│ ... next 20 rows fade in here ...    │
├──────────────────────────────────────┤
│        Loading more&nbsp;…           │  ← stays under the list
└──────────────────────────────────────┘
```

The list itself carries `dj-viewport-bottom="load_more"`. When its
last row scrolls into view, the client sends `load_more`, which
appends 20 more rows to the state list and re-renders. The new
last row is a fresh element, so it is ready for the next trigger.

---

## Step 1 — The model + initial query

```python
# myapp/models.py
from django.db import models


class Activity(models.Model):
    actor = models.CharField(max_length=80)
    verb = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]  # cursor-friendly: id is monotonic
```

We use `id`-based ordering because it's cursor-friendly. Avoid
`created_at` cursors when there's any chance of ties at
millisecond precision (CSV import, fixture loads).

---

## Step 2 — The view, with a cursor + page size

```python
# myapp/views.py
from djust import LiveView, state, event_handler

from .models import Activity


PAGE_SIZE = 20


class ActivityFeedView(LiveView):
    template_name = "feed.html"

    activities = state(default_factory=list)
    cursor = state(0)        # smallest id of the rows we've loaded
    has_more = state(True)

    def mount(self, request, **kwargs):
        first_page = self._fetch(after_id=None)
        self.activities = first_page
        self.cursor = first_page[-1]["id"] if first_page else 0
        self.has_more = len(first_page) == PAGE_SIZE

    @event_handler
    def load_more(self, **kwargs):
        if not self.has_more:
            return
        page = self._fetch(after_id=self.cursor)
        if page:
            self.activities = self.activities + page
            self.cursor = page[-1]["id"]
        self.has_more = len(page) == PAGE_SIZE

    def _fetch(self, after_id: int | None) -> list[dict]:
        qs = Activity.objects.all()
        if after_id is not None:
            qs = qs.filter(id__lt=after_id)  # older rows
        rows = qs[:PAGE_SIZE]
        return [
            {"id": r.id, "actor": r.actor, "verb": r.verb,
             "created_at": r.created_at.isoformat()}
            for r in rows
        ]
```

Three patterns to call out:

1. **The cursor is the smallest `id` we've seen.** Each fetch is
   `id < cursor` — so concurrent inserts at the head don't shift
   the page boundaries. With offset-based pagination, an insert
   at the top would shift the next page by one and you'd see a
   row twice. With cursor-based, never.
2. **Idempotent `load_more`.** The client fires once per last
   row (it marks the row with `data-dj-viewport-fired`), and the
   early return `if not self.has_more` makes a late extra event
   harmless.
3. **`has_more` is a derived flag** based on whether the last
   fetch returned a full page. Cleaner than counting against a
   total — the framework re-renders when it flips, so the
   "Loading more…" row disappears automatically when there's
   nothing left to load.

---

## Step 3 — The template

`dj-root` on the outer element marks the region djust patches; djust
stamps `dj-view` onto it when it renders the page, which is what
connects the page to its LiveView. Without it the page renders but
never connects, and `load_more` never fires. Put the fragment inside
the page skeleton from
[your first LiveView](../getting-started/first-liveview.md).

```html
<!-- myapp/templates/feed.html -->
<section class="feed" dj-root>
  <h1>Activity</h1>

  <ul class="feed-list" dj-viewport-bottom="load_more">
    {% for entry in activities %}
      <li class="feed-item" dj-key="{{ entry.id }}">
        <strong>{{ entry.actor }}</strong>
        <span>{{ entry.verb }}</span>
        <time datetime="{{ entry.created_at }}">{{ entry.created_at }}</time>
      </li>
    {% endfor %}
  </ul>

  {% if has_more %}
    <div class="feed-sentinel" role="status">
      <span class="spinner" aria-hidden="true"></span>
      Loading more&hellip;
    </div>
  {% else %}
    <p class="feed-end" role="status">That&rsquo;s all.</p>
  {% endif %}
</section>
```

The attribute on the list is the whole trick:

| Attribute | Behavior |
|---|---|
| `dj-viewport-bottom="load_more"` | Fire the `load_more` server event when the container's last child crosses into the viewport (10% visible by default; tune with `dj-viewport-threshold`). Backed by `IntersectionObserver` — no scroll listener, no polling. Each last row fires once; after a render appends rows, the new last row is observed, so it keeps working. |
| `dj-key="{{ entry.id }}"` | Keys each row, so the diff matches existing rows by id and only inserts the new ones. |
| `{% if has_more %}` … `{% else %}` | When `has_more` flips false, the "Loading more…" row is replaced by "That's all." |
| `role="status"` on the loading row | Screen readers announce "Loading more…" politely. |

> **Why not a separate sentinel element?** `dj-viewport-bottom`
> watches the container's last child, and each child fires only
> once. A sentinel that stays in place would fire once and never
> again; a real row that is replaced by the next page's rows
> re-arms on its own. Keep the loading row outside the list.

---

## Step 4 — A bit of CSS

```css
.feed-list {
  list-style: none;
  margin: 0;
  padding: 0;
}
.feed-item {
  display: flex;
  align-items: baseline;
  gap: 12px;
  padding: 10px 16px;
  border-bottom: 1px solid var(--color-border, #e5e7eb);
  /* Subtle fade-in for newly-appended rows. The framework's per-row
     diff means only NEW rows get this animation — existing rows
     don't restyle on each load_more cycle. */
  animation: feed-row-in 0.2s ease-out;
}
@keyframes feed-row-in {
  from { opacity: 0; transform: translateY(4px); }
  to   { opacity: 1; transform: translateY(0); }
}
.feed-sentinel {
  display: flex;
  align-items: center;
  gap: 8px;
  justify-content: center;
  padding: 16px;
  font-size: 13px;
  color: var(--color-muted, #6b7280);
  min-height: 40px;
}
.feed-end {
  text-align: center;
  padding: 24px 16px;
  color: var(--color-muted, #6b7280);
  font-style: italic;
}
.spinner {
  width: 14px;
  height: 14px;
  border: 2px solid var(--color-border, #e5e7eb);
  border-top-color: var(--color-accent, #3b82f6);
  border-radius: 50%;
  animation: spin 0.7s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }
```

The `animation: feed-row-in` on `.feed-item` is the only place
the per-row diff matters. Because the framework only patches
NEW rows (not existing ones) on each `load_more`, only the new
rows trigger the animation. Existing rows stay still.

---

## Step 5 — Wire the URL and try it

```python
# myapp/urls.py
from django.urls import path
from .views import ActivityFeedView

urlpatterns = [path("activity/", ActivityFeedView.as_view())]
```

Visit `/activity/` and scroll. The last row triggers a
`load_more` event when it crosses into view; the loading row
below it shows for the duration of the round-trip; then the next 20 rows fade
in below the existing ones. Repeat until you've loaded everything,
at which point the loading row becomes "That's all."

---

## When to use `dj-virtual` instead

Infinite scroll is the right pattern for **append-only** lists where
the user reads top-to-bottom and the dataset is unbounded — feeds,
chats, logs, activity streams.

For **finite, browsable** datasets where the user might jump around
(a 100K-row admin table they want to scroll, search, sort), reach
for [`dj-virtual`](large-lists.md) instead. It windows the
DOM so only the visible slice renders — `O(visible)` DOM cost
regardless of dataset size — and supports the full set of rows
without needing to load them in pages.

Quick decision rule:

- **Append-only + linear reading** → infinite scroll
  (`dj-viewport-bottom` + cursor)
- **Browse-anywhere + finite total** → virtual scroll (`dj-virtual`)

You can combine them — virtual scroll over a windowed slice that
itself loads more on bottom — but that's an advanced setup; start
with one or the other.

---

## What just happened, end to end

```
   Browser                                 Server
      │                                       │
      │ initial load                          │
      │ ────────────────────────────────────► mount()
      │                                       │  fetch first 20
      │ ◄ render: 20 rows + loading row ─────│  cursor = id of #20
      │                                       │
      │ user scrolls...                       │
      │ last row crosses viewport             │
      │ ────────────────────────────────────► load_more
      │                                       │  fetch next 20 (id<cursor)
      │                                       │  activities += page
      │                                       │  cursor = id of new last
      │ ◄ patch: 20 new <li> rows ────────────│
      │   (new last row is observed)          │
      │                                       │
      │ ... eventually fetch returns < 20 ... │
      │                                       │  has_more = False
      │ ◄ patch: loading row replaced by ─────│
      │   "That's all." marker                │
```

After each patch the client re-scans the list and observes the
new last row. No leaked listeners, no polling.

---

## Where to go next

- **Bidirectional scroll** (loading older rows when scrolling
  *up*): add `dj-viewport-top="load_older"` to the same list —
  it fires when the first row enters the viewport. Use
  `scrollIntoView()` after prepending to keep the user's read
  position stable.
- **Real-time inserts** at the head: combine with the
  [real-time comments tutorial](tutorial-real-time-comments.md)'s
  `@notify_on_save` pattern. New rows from other users get
  prepended; the user's scroll position stays locked unless they
  were at the very top.
- **DOM cap for chat / log viewers**: large feeds left running
  for hours bloat the DOM. Use a [stream `limit=N`](large-lists.md)
  to prune from the opposite edge automatically — the visual
  effect is "scroll up forever, but the DOM never exceeds N rows."
- **Save-and-restore scroll position** across navigations: the
  framework's [Sticky LiveViews](sticky-liveviews.md) keep
  the view mounted (and the scroll position intact) when the
  user navigates away and comes back.

The four-line shape — keyed rows + `dj-viewport-bottom` + idempotent
handler + cursor — is the entire infinite-scroll API. The hard
parts (observer setup, race conditions, cleanup) are off your
plate. Drag it onto a search results page, a notification list, a
log tail — same recipe.
