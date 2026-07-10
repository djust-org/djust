---
title: "Navigation & URL State"
slug: navigation
section: guides
order: 4
level: intermediate
description: "SPA-like navigation with live_patch, live_redirect, and dj-navigate directives"
---

# Navigation & URL State

djust provides `live_patch()` and `live_redirect()` for managing URL state without full page reloads, inspired by Phoenix LiveView. Bookmark-friendly URLs, browser back/forward, and deep linking all work out of the box.

## What You Get

- **`live_patch()`** -- Update URL query params without remounting the view
- **`live_redirect()`** -- Navigate to a different LiveView over the existing WebSocket
- **Template directives** -- `dj-patch` and `dj-navigate` for declarative navigation
- **Browser history** -- Full back/forward support via `popstate` handling

## Quick Start

### 1. Subclass LiveView (navigation is built in)

`NavigationMixin` is already included in `LiveView`, so its navigation helpers are always
available — just subclass `LiveView`. Do **not** also inherit `NavigationMixin`
(`class ProductListView(NavigationMixin, LiveView)`): it is already in `LiveView`'s bases, so
that raises `TypeError: Cannot create a consistent method resolution`.

```python
from djust import LiveView
from djust.decorators import event_handler

class ProductListView(LiveView):
    template_name = 'products/list.html'

    def mount(self, request, **kwargs):
        self.category = "all"
        self.page = 1
        self.products = []

    def handle_params(self, params, uri):
        """Called when URL params change (live_patch or browser back/forward)."""
        self.category = params.get("category", "all")
        self.page = int(params.get("page", 1))
        self.products = self.fetch_products()

    @event_handler()
    def filter_by_category(self, category="all", **kwargs):
        self.live_patch(params={"category": category, "page": 1})
```

### 2. Use Navigation Directives

```html
<!-- Update URL params without remount -->
<a dj-patch="?category=electronics&page=1">Electronics</a>
<a dj-patch="?category=books&page=1">Books</a>

<!-- Navigate to a different view -->
<a dj-navigate="/products/{{ product.id }}/">View Details</a>
```

## API Reference

### `live_patch(params=None, path=None, replace=False)`

Update the browser URL without remounting the view. Triggers `handle_params()` and a re-render. `mount()` is NOT called again.

```python
# Update query params only
self.live_patch(params={"page": 2})

# Change path and params
self.live_patch(path="/search/", params={"q": "django"})

# Replace current history entry (no back button entry)
self.live_patch(params={"sort": "price"}, replace=True)
```

### `live_redirect(path, params=None, replace=False)`

Navigate to a different LiveView over the existing WebSocket. The current view is unmounted and the new view is mounted fresh.

```python
self.live_redirect("/items/42/")
self.live_redirect("/search/", params={"q": "widgets"})
```

### `handle_params(params, uri)`

Callback invoked when URL params change. Override this to update view state based on the URL.

```python
def handle_params(self, params, uri):
    self.category = params.get("category", "all")
    self.page = int(params.get("page", 1))
    self.results = self.search(self.category, self.page)
```

## Template Directives

### `dj-patch`

Declarative `live_patch`. Updates the URL and sends `url_change` to the server without remounting.

```html
<a dj-patch="?sort=name&order=asc">Sort by Name</a>
<a dj-patch="/products/?category=new">New Products</a>
<a dj-patch="/">Home (root path)</a>
```

Patching to the root path `/` is supported and correctly updates the browser URL.

**Note**: Use `dj-patch` for navigation instead of `dj-click` when you need URL updates and browser history support. System check `djust.T010` will warn if you use `dj-click` with navigation-related data attributes like `data-view` or `data-tab`.

### `dj-navigate`

Declarative `live_redirect`. SPA-navigates to a **different LiveView over the existing WebSocket** — no socket teardown, no full page reload. (A full reload happens only as a fallback when the target path isn't in the route map.)

```html
<a dj-navigate="/dashboard/">Go to Dashboard</a>
<a dj-navigate="/items/{{ item.id }}/">View Item</a>
```

#### Zero wiring required

`dj-navigate` works **out of the box** — no `live_session()` needed. The client
route map (URL path → LiveView) is **auto-derived from your Django URLconf**
(every route whose view subclasses `djust.LiveView`) and **auto-emitted by
`{% djust_client_config %}`**, the tag that's already in every scaffolded base
`<head>`. As long as your base template loads `{% djust_client_config %}` and
your LiveViews are wired into `urlpatterns`, SPA navigation just works.

```html
{% load live_tags %}
<head>
    {% djust_client_config %}  {# emits the API/SSE prefixes + the route map #}
</head>
```

If `dj-navigate` is used but no LiveView routes are found in the URLconf (so the
route map is empty), djust's system check **`djust.T016`** warns you — otherwise
`dj-navigate` would silently fall back to a full page reload.

`live_session()` is still supported for grouping views that share a WebSocket
connection, and its registrations are merged into the auto-derived map. It is no
longer required just to make `dj-navigate` resolve routes.

#### Active-link highlighting

A persistent nav usually lives **outside** `[dj-root]`, so `dj-navigate`'s
`dj-root`-only swap won't update a server-rendered "active page" class on
navigation. djust handles this for you: after every navigation (click, WS mount,
and browser back/forward) it sets `aria-current="page"` on the `[dj-navigate]`
link whose path matches the current URL and removes it from the others. Style the
active link off that attribute — no per-app JavaScript needed:

```html
<a dj-navigate="/">Home</a>
<a dj-navigate="/docs/">Docs</a>

<style>
  a[dj-navigate][aria-current="page"] { font-weight: 700; color: #fff; }
</style>
```

Cross-origin `dj-navigate` targets (e.g. a link to a sister site) are never
marked current, and an `aria-current` you set yourself to a different value
(`step`, `true`, …) is left untouched. Matching is exact-path; for
section/ancestor highlighting (e.g. `/docs/` active on `/docs/guides/x`), add
your own rule on top.

> **Chart.js / map blank after `dj-navigate`?** Scripts in SPA-patched content
> don't execute, so an inline `<script>` that inits a library renders on a hard
> reload but stays blank after navigation. Initialize third-party libraries from
> a [client hook](hooks#integrating-third-party-libraries-chartjs-maps-editors)
> registered once in your base template, not an inline `<script>`.

### `auto_navigate` — automatic link interception (default ON in v1.1)

With `dj-navigate` you annotate each link. **`auto_navigate`** goes one step
further: a single delegated click listener SPA-navigates **plain `<a href>`
links** — no djust attribute needed — whenever the link's path resolves in the
route map.

**As of v1.1 this is ON by default** — native `dj-navigate` is djust's canonical
SPA-navigation model, so plain in-app links are SPA-navigated with zero config
(the route map is auto-emitted). It degrades gracefully (see the fall-through
list below), so existing apps keep working. To **opt out** (e.g. you wire your
own external TurboNav), set it to `False`:

```python
LIVEVIEW_CONFIG = {"auto_navigate": False}
```

> On djust **1.0.x** this was opt-in (`auto_navigate` defaulted to `False`); set
> it to `True` there to get the same behavior.

`{% djust_client_config %}` emits a small flag and the
client intercepts in-app navigations automatically. It is deliberately
conservative — a link **falls through to a normal browser navigation** (no
interception) whenever any of these hold:

- a modifier/middle click (⌘/Ctrl/Shift/Alt, or a non-left button) — i.e. new tab/window
- a `target` other than `_self`, a `download` attribute, or `rel="external"`
- the link or any ancestor has `data-no-navigate`
- the href is external (different origin) or a non-http(s) scheme (`mailto:`, `tel:`)
- a same-page hash-only jump (`#section`) — the browser scrolls instead
- **the path isn't a LiveView route in the route map** — admin pages, plain
  Django views, and routes the current user can't access just reload normally

Same-view query-only changes use `live_patch` (state-preserving); cross-view uses
`live_redirect`. Because the route map is auth-filtered, `auto_navigate` never
intercepts a route the current user isn't authorized for — it full-reloads and
the server enforces access.

Opt a single link out with `data-no-navigate`:

```html
<a href="/reports/" data-no-navigate>Force a full reload</a>
```

`auto_navigate` is opt-in and should soak in your app before you rely on it; it
changes the behavior of *every* in-app link, so the opt-out matrix above is the
contract. `dj-navigate` remains the explicit, always-on way to mark a single SPA link.

## Example: Search with URL State

```python
class SearchView(LiveView):  # navigation is built in — no NavigationMixin needed
    template_name = 'search.html'

    def mount(self, request, **kwargs):
        self.query = ""
        self.sort = "relevance"
        self.results = []

    def handle_params(self, params, uri):
        self.query = params.get("q", "")
        self.sort = params.get("sort", "relevance")
        if self.query:
            self.results = Product.objects.filter(
                name__icontains=self.query
            ).order_by(self.sort)

    @event_handler()
    def search(self, value="", **kwargs):
        self.live_patch(params={"q": value, "sort": self.sort})

    @event_handler()
    def change_sort(self, sort="relevance", **kwargs):
        self.live_patch(params={"q": self.query, "sort": sort})
```

```html
<input type="text" dj-change="search" value="{{ query }}">

<div class="sort-options">
    <a dj-patch="?q={{ query }}&sort=relevance">Relevance</a>
    <a dj-patch="?q={{ query }}&sort=price">Price</a>
    <a dj-patch="?q={{ query }}&sort=-created">Newest</a>
</div>

{% for product in results %}
    <div class="product">
        <h3><a dj-navigate="/products/{{ product.id }}/">{{ product.name }}</a></h3>
        <p>${{ product.price }}</p>
    </div>
{% endfor %}
```

## When to Use Patch vs Redirect

| Use `live_patch()` | Use `live_redirect()` |
|---|---|
| Filtering, sorting, paginating | Navigating to a different page |
| Changing tabs within the same view | Moving between list and detail views |
| Updating search parameters | Redirecting after form submission |
| You want `mount()` NOT called again | You need a fresh `mount()` call |

## Best Practices

### ⚠️ Anti-Pattern: Don't Use `dj-click` for Navigation

This is **the most common mistake** when building multi-view djust apps. Using `dj-click` to trigger a handler that immediately calls `live_redirect()` creates an unnecessary round-trip.

**❌ Wrong** — using `dj-click` to trigger a handler that calls `live_redirect()`:

```python
# Anti-pattern: Handler does nothing but navigate
@event_handler()
def go_to_item(self, item_id, **kwargs):
    self.live_redirect(f"/items/{item_id}/")  # Wasteful round-trip!
```

```html
<!-- Wrong: Forces WebSocket round-trip just to navigate -->
<button dj-click="go_to_item" dj-value-item_id="{{ item.id }}">View</button>
```

**✅ Right** — using `dj-navigate` directly:

```html
<!-- Right: Client navigates immediately, no server round-trip -->
<a dj-navigate="/items/{{ item.id }}/">View Item</a>
```

**Why it matters:** Direct navigation is 10-20x faster (~10ms vs 110-250ms), saves WebSocket bandwidth, and provides instant user feedback.

#### When to Use `live_redirect()` in Handlers

Use handlers for navigation only when navigation depends on **server-side logic**:

- **Conditional navigation** after form validation
- **Navigation based on** auth/permissions checks
- **Navigation after** async operations (creating records, API calls)
- **Multi-step wizard** logic with conditional flow

**Common theme:** The handler does **meaningful work** before navigating. If your handler only calls `live_redirect()`, use `dj-navigate` instead.

### Anti-Pattern: Don't Use `dj-click` for Tab/View Switching

Using `dj-click` with data attributes like `data-view` or `data-tab` to switch between sections within a view is fragile and loses URL state. Use `dj-patch` instead.

**The anti-pattern:**

```html
<button dj-click="switch_view" data-view="settings">Settings</button>
```

```python
@event_handler()
def switch_view(self, view="", **kwargs):
    self.active_view = view
    self._load_data()
```

**Why this breaks:**

1. **Data attributes are fragile** -- if the VDOM diff replaces the element mid-click, or the user clicks a child element (e.g. an icon `<span>` inside the button), the `view` param can arrive as `""`, leaving the UI in a broken state.
2. **No URL update** -- the browser URL doesn't change, so back/forward doesn't work, tabs aren't bookmarkable, and refreshing always resets to the default view.
3. **Race conditions** -- if `handle_tick` fires between the click and the re-render, state can get out of sync because there's no URL as source of truth.

**The correct pattern:**

```html
<a dj-patch="?tab=settings"
   class="{% if active_tab == 'settings' %}active{% endif %}">
    Settings
</a>
<a dj-patch="?tab=overview"
   class="{% if active_tab == 'overview' %}active{% endif %}">
    Overview
</a>
```

```python
class DashboardView(LiveView):  # navigation is built in — no NavigationMixin needed
    template_name = 'dashboard.html'
    VALID_TABS = {"overview", "settings", "logs"}

    def mount(self, request, **kwargs):
        self.active_tab = "overview"

    def handle_params(self, params, uri):
        tab = params.get("tab", "overview")
        if tab in self.VALID_TABS:
            self.active_tab = tab
        self._load_tab_data()
```

**Why it works:**

- `dj-patch` updates the URL immediately on the client (no round-trip delay for the URL change)
- `handle_params` is a first-class lifecycle method with proper re-render sequencing
- Browser back/forward and bookmarks work automatically
- Idempotent -- calling `handle_params` twice with the same params is a no-op
- System check `djust.T010` detects the anti-pattern and suggests `dj-patch`

**Rule of thumb:**

| Directive | Use for |
|---|---|
| `dj-click` | Actions that modify state (increment counter, delete item, toggle) |
| `dj-patch` | Navigation that should update the URL (tabs, filters, pagination) |
| `dj-navigate` | SPA navigation to a different LiveView over the WebSocket (full reload only as a fallback when the route isn't in the map) |

### URL Design Best Practices

- Use query params for filter/sort/page state that should be shareable and bookmarkable.
- Use `replace=True` for transient state changes (e.g., intermediate typing) to avoid polluting browser history.
- Always implement `handle_params()` to restore state from URL -- this ensures deep links and browser back/forward work correctly.
- Keep URL params flat and simple: `?category=books&page=2` rather than nested structures.
