---
title: "Tutorial: Build a search-as-you-type feature"
slug: tutorial-search-as-you-type
section: guides
order: 41
level: beginner
description: "Build a debounced live search box that calls the server, shows a spinner while in flight, and renders results as the user types — five framework features tied together in ~80 lines of Python and HTML."
---

# Tutorial: Build a search-as-you-type feature

By the end of this tutorial you'll have a working search box that:

- Fires on every keystroke, **debounced to 300 ms** so you don't hammer
  the server.
- Shows an inline **"Searching…" spinner** while the request is in
  flight.
- Renders matching results immediately when the server responds.
- Handles the **empty query**, **no results**, and **error** paths
  cleanly.

It pulls together five framework features —
`dj-input`, `dj-debounce`, `state(...)`, `dj-loading.*`, and a
server-side handler — without a single line of client-side
JavaScript.

| You'll learn | Documented in |
|---|---|
| Wiring an input to a server event | [Events](../core-concepts/events.md) |
| Debouncing keystrokes | [Events: `dj-debounce` / `dj-throttle`](../core-concepts/events.md) |
| Reactive state | [State & Computation Primitives](state-primitives.md) |
| Loading states | [Loading States & Background Work](loading-states.md) |
| Rendering lists | [Templates](../core-concepts/templates.md) |

> **Prerequisites:** Finish the [installation](../getting-started/installation.md) and
> [first LiveView](../getting-started/first-liveview.md) guides so that you have a
> Django project with djust installed and a working LiveView mounted at
> some URL. We'll add the search feature into a fresh `SearchView`.

---

## What you're building

A page that looks roughly like this:

```
Search:  [______________________]
         Searching…              ← appears only while in flight
         ─────────
         · Indexing strategies for large LiveViews
         · State primitives in v0.5.1
         · Why the transport is in Rust
```

The user types, the spinner appears for ≤ 300 ms after they stop
typing, then the list updates. Empty query renders no results.
Search errors render an inline message instead of a blank list.

---

## Step 1 — Define the LiveView and its state

Create `myapp/views.py` with a new LiveView. We track three pieces
of state: the current query string, the matching documents, and a
"last search failed" message for the error path.

```python
from djust import LiveView, state, event_handler


class SearchView(LiveView):
    template_name = "search.html"

    query = state("")
    results = state(default_factory=list)
    error = state("")
```

> `state("")` declares a reactive attribute. Re-assigning it in any
> handler triggers a minimal re-render of the parts of the template
> that read it — same model as React's `useState` but managed entirely
> on the server.

---

## Step 2 — Add the input and the spinner

Create `myapp/templates/search.html`. The form is plain HTML — the
djust attributes do all the wiring. Only the reactive region is shown;
put it inside the page skeleton from
[your first LiveView](../getting-started/first-liveview.md), whose
`<div dj-root>` marks the part djust patches:

```html
<div dj-root>
<form>
  <label>
    Search:
    <input
      type="text"
      name="q"
      value="{{ query }}"
      dj-input="search"
      dj-debounce="300"
      autocomplete="off"
      aria-label="Search documents"
    />
  </label>

  <p dj-loading.show dj-loading.for="search">
    Searching…
  </p>
</form>

<ul>
  {% for r in results %}
    <li>{{ r.title }}</li>
  {% endfor %}
</ul>

{% if error %}
  <p role="alert" class="error">{{ error }}</p>
{% endif %}
</div>
```

What each djust attribute does:

| Attribute | Effect |
|---|---|
| `dj-input="search"` | Every input change fires the `search` event on the server, sending the input's current text as the `value` kwarg (and its `name`, `q`, as `field`). |
| `dj-debounce="300"` | Wait 300 ms after the last keystroke before firing. Subsequent keystrokes within that window cancel and restart the timer. Text inputs are already debounced 300 ms by default, so this attribute only makes the timing explicit; change the number to tune it. |
| `dj-loading.show dj-loading.for="search"` | Show this `<p>` element only while the `search` event is in flight. djust hides it when the page binds and sets `display: block` while the event runs (`dj-loading.show="flex"` picks another display value). |

> `dj-input` always sends the text as `value`, whatever the input's
> `name` is, so the handler parameter is `value`.

---

## Step 3 — Handle the event

Add an `event_handler` to `SearchView`:

```python
import logging

from django.db.models import Q

from .models import Document

logger = logging.getLogger(__name__)


class SearchView(LiveView):
    template_name = "search.html"

    query = state("")
    results = state(default_factory=list)
    error = state("")

    @event_handler
    def search(self, value: str = "", **kwargs):
        self.query = value
        self.error = ""

        q = value.strip()
        if not q:
            self.results = []
            return

        try:
            qs = Document.objects.filter(
                Q(title__icontains=q) | Q(body__icontains=q)
            ).order_by("-updated_at")[:20]
            self.results = [{"id": d.id, "title": d.title} for d in qs]
        except Exception:
            # Log the details for yourself; show the user a generic message.
            # A raw database error can reveal table and column names.
            logger.exception("Document search failed for %r", q)
            self.results = []
            self.error = "Search is unavailable right now. Please try again."
```

Three reactive assignments (`self.query`, `self.results`, `self.error`)
are all the framework needs to figure out which template fragments
need re-rendering. The diff is sent over the WebSocket as a minimal
JSON patch — not a full HTML re-render.

> **Why `**kwargs`?** djust passes the whole event payload to
> handlers. The named parameter `value` extracts the text coerced to
> `str`; `**kwargs` accepts the rest (`field`, `_target`) without
> raising.

---

## Step 4 — Wire up the URL

In `myapp/urls.py`:

```python
from django.urls import path
from .views import SearchView

urlpatterns = [
    path("search/", SearchView.as_view(), name="search"),
]
```

That's it. Run `python manage.py runserver`, visit `/search/`, and
type. The list updates 300 ms after each keystroke, the "Searching…"
line appears only while the round-trip is in flight, and an empty
query clears the list.

---

## Step 5 — Polish: empty state and "no results"

Two final empty-state branches make the UI feel finished. Replace
the `<ul>` block in `search.html`:

```html
{% if not query|cut:" " %}
  <p class="hint">Type to search documents.</p>
{% elif not results %}
  <p class="hint">No documents match &ldquo;{{ query }}&rdquo;.</p>
{% else %}
  <ul>
    {% for r in results %}
      <li>{{ r.title }}</li>
    {% endfor %}
  </ul>
{% endif %}
```

`query|cut:" "` strips the spaces, so a whitespace-only query counts as
empty. (The template engine does not call string methods such as
`query.strip`.) Now the page reads like a real product:

| State | Shown |
|---|---|
| Page load (query empty) | "Type to search documents." |
| Typing (in flight) | "Searching…" + previous results |
| Results returned | The `<ul>` of matches |
| No matches | "No documents match &ldquo;…&rdquo;." |
| Error | Red `role="alert"` message |

---

## Why this works the way it does

A few framework-level guarantees worth understanding:

1. **Debounce lives on the client.** The 300 ms timer is a JavaScript
   timer in the djust client runtime — the server never sees the
   keystrokes that get cancelled. Zero wasted requests.
2. **Loading state is declarative.** No `await fetch()`, no
   `setLoading(true)` / `setLoading(false)`. The framework knows the
   event is in flight because it issued the WebSocket frame; it can
   toggle `[dj-loading.*]` elements automatically.
3. **The diff is minimal.** When `self.results = […]` changes, only
   the `<ul>` (or its conditional siblings) re-render. The `<form>`
   stays mounted, so the input's cursor position, focus, and selection
   are preserved across renders. No flicker, no caret jumping.
4. **The handler is just a method.** No special framework primitive
   for "search". Anything you can put in a regular Python function
   works — DB queries, calls to external services, ML inference. If
   it's slow, see [Loading States & Background Work](loading-states.md)
   for `start_async()`.

---

## Where to go next

- **Long-running queries:** if a single search takes more than
  ~200 ms (e.g. ML re-ranking, external API), wrap the slow call in
  `start_async()` so the spinner shows immediately and the results
  fill in when ready. See [Loading States & Background
  Work](loading-states.md).
- **Highlight the match:** wrap matching substrings in `<mark>` on
  the server before sending the patch. Both the query and the titles
  are user text, so escape each piece before adding markup: build the
  string with `format_html("{}<mark>{}</mark>{}", before, match, after)`,
  which escapes its arguments. Never call `mark_safe` on a string that
  contains user text.
- **Server-side cancellation:** if the user types 'foo' then 'bar'
  before the 'foo' search returns, you don't want to render stale
  'foo' results. Use a per-LiveView `_search_seq` counter and only
  apply results whose seq matches the latest one.
- **Prefetch next page on hover:** add `dj-prefetch` to result `<a>`
  elements so the destination loads in the background when the user
  starts moving toward it.

The five primitives you used here — `state`, `dj-input`,
`dj-debounce`, `dj-loading.*`, and a plain `@event_handler` — are
the same ones every interactive feature in djust is built from.
Once they click, autocomplete, filtering, sorting, pagination, and
inline editing are all variations on the same pattern.
