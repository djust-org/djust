# djust Roadmap

> Current version: **0.3.8rc1** (Alpha) — Last updated: March 18, 2026 (roadmap refreshed with Phoenix 1.0 parity analysis, React 19 patterns, and re-prioritization)

This roadmap outlines what has been built, what is actively being worked on, and where djust is headed. Priorities are shaped by real-world usage across [djust.org](https://djust.org) and [djustlive](https://djustlive.com), and by feature parity goals with Phoenix LiveView 1.0 and React 19-level interactivity.

### Priority Matrix — What Moves the Needle Most

| Priority | Feature | Why | Milestone |
|----------|---------|-----|-----------|
| **P0** | VDOM structural patching (#559) | Blocks all conditional rendering — every new user hits this | v0.4.0 |
| **P0** | JS Commands (`dj.push`, `dj.show`, etc.) | Biggest DX gap vs Phoenix; eliminates server round-trip for UI interactions | v0.4.0 |
| **P0** | Focus preservation across re-renders | Forms feel broken without it — table-stakes for any interactive framework | v0.4.0 |
| **P1** | `dj-value-*` static event params | Most underrated Phoenix feature; used on virtually every event binding | v0.4.0 |
| **P1** | `handle_params` callback | `live_patch` is half-implemented without it — no URL-driven state | v0.4.0 |
| **P1** | Flash messages (`put_flash`) | Every app reinvents this; 40 lines to eliminate universal boilerplate | v0.4.0 |
| **P1** | `on_mount` hooks | Cross-cutting auth/telemetry without copy-pasting into every mount() | v0.4.0 |
| **P1** | `assign_async` / AsyncResult | Foundation for responsive dashboards — independent loading boundaries | v0.5.0 |
| **P1** | Template fragments (static subtree) | Biggest wire-size optimization; how Phoenix achieves sub-ms updates | v0.5.0 |
| **P2** | Server Actions (`@action` decorator) | React 19 parity; standardized pending/error/success for mutations | v0.8.0 |
| **P2** | Async Streams | Phoenix 1.0 parity; infinite scroll and real-time feeds at scale | v0.8.0 |
| **P2** | Connection multiplexing | Pages with 5+ live sections need this to not waste connections | v0.6.0 |
| **P2** | Dead View / Progressive Enhancement | 1.0 requirement for government/accessibility projects | v1.0.0 |
| **P2** | Accessibility (ARIA/WCAG) | 1.0 requirement; Phoenix was criticized for shipping without this | v1.0.0 |
| **P3** | View Transitions API | Cheapest way to make navigation feel native | v0.5.0 |
| **P3** | Islands of interactivity | Content-heavy sites with small interactive zones | v0.7.0 |
| **P3** | Offline mutation queue | Mobile/spotty-connection differentiator | v0.6.0 |

---

## Completed

### Core Framework (Stable)

- Rust-powered template engine (10-100x faster than Django's Python engine)
- Sub-millisecond VDOM diffing with DOM morphing
- WebSocket real-time communication via Django Channels
- HTTP fallback for environments without WebSocket support
- Django Forms integration with real-time field validation
- Two-tier component system (Component + LiveComponent)
- Redis state backend for horizontal scaling
- Hot reload for development (file watcher + WS broadcast)
- Debug panel (`Ctrl+Shift+D`) with event history, VDOM patches, state inspection, network tab
- System checks (`manage.py djust_check`) and security audit (`manage.py djust_audit`)
- All 57 Django built-in template filters supported in Rust engine
- `{% url %}` tag with arguments (including inside `{% for %}` loops)
- MCP server for AI-assisted development
- TurboNav integration with documented contract and guards
- WebSocket security hardening (rate limiting, per-IP connection limits, message size checks, error disclosure prevention)
- Keyed VDOM diff with LIS optimization, proptest/fuzzing coverage
- JIT serialization for M2M, nested dicts, `@property`
- File modularization: `client.js`, `live_view.py`, `websocket.py`, `state_backend.py`, `template_backend.py` all split into focused modules

### State Management Decorators (Phases 1-5)

All state management features are production-ready:

- `@debounce` — Reduce server requests by waiting for input to settle
- `@throttle` — Rate-limit event handlers with leading/trailing edge control
- `@loading` — Automatic loading states with configurable UI feedback
- `@cache` — Client-side LRU caching with TTL for idempotent operations
- `@client_state` — Reactive state bus for cross-component state sharing
- `@optimistic` — Optimistic UI updates with automatic rollback on error
- `DraftModeMixin` — Draft/discard/publish flow with localStorage persistence

**Result**: 87% code reduction compared to equivalent manual JavaScript.

### Real-Time Collaboration (Phase 6, partial)

- Presence tracking (who's online, idle detection)
- Broadcasting (pub/sub messaging across LiveView instances)
- Live indicators (typing, user count)
- Collaborative notepad example app

### v0.3.0 "Phoenix Rising"

- Progressive Web App support with offline-first implementation, service worker integration, 8 PWA template tags
- Multi-tenant architecture with flexible tenant resolution, automatic data isolation, tenant-aware state backends
- 114 new tests (53 PWA, 61 multi-tenant)

### v0.3.6–v0.3.8rc1

- File uploads over binary WebSocket frames with chunked transfer, drag-and-drop zones, client-side image preview, progress tracking, magic-byte MIME validation, auto-upload, extension/MIME filtering (`UploadMixin`, `allow_upload()`, `consume_uploaded_entries()`)
- Server-Sent Events (SSE) fallback transport — same message interface as WebSocket, works in environments that block WS
- `live_session()` URL routing — groups URL patterns into shared WebSocket connections with route map injection for client-side `live_redirect`
- `StreamingMixin` for token-by-token partial DOM updates (LLM response streaming) with 60fps batching
- `dj-patch` on `<select>` and `<input>` elements — WebSocket `url_change` instead of full page reload
- FormMixin serialization fix for `ModelForm` over WebSocket, `model_pk`/`model_label` for re-hydration
- Debug toolbar: state size breakdown (memory + serialized bytes), TurboNav persistence, search in Network/State tabs
- `dj-hook` re-initialization after VDOM patching
- VDOM version sync improvements, multi-tab cache key fix (`request.path`), canvas `width`/`height` preservation during morph
- `djust-deploy` CLI for deployment automation
- `model.id` returns native type (not string) — breaking change in v0.3.6

---

## In Progress

### Stability & Correctness

Active bugs being fixed before expanding feature scope:

| Issue | Description | Status |
|-------|-------------|--------|
| [#560](https://github.com/johnrtipton/djust/issues/560) | Tick auto-refresh causes VDOM version mismatch, dropping user events | Open |
| [#559](https://github.com/johnrtipton/djust/issues/559) | VDOM patching fails when `{% if %}` blocks add/remove DOM elements | Open |
| [#561](https://github.com/johnrtipton/djust/pull/561) | WS cache key collision, canvas morph clear, dj-patch navigation | PR open |

### Rust Template Engine Parity

Closing the remaining gaps between the Rust engine and Django's Python engine:

| Gap | Impact | Workaround |
|-----|--------|------------|
| Model attribute access (`.field_name`) | High | Convert models to dicts in context |
| `"` not escaped to `&quot;` in attributes | Medium | Use hidden `<pre>` + JS `.textContent` |
| Custom `{% load %}` template tags | Medium | Write raw HTML with correct CSS classes |
| `request.path` not available | Low | Inject via context processor |

---

## Next Up

### Milestone: v0.4.0-beta — Stability & Developer Experience

**Goal**: Make djust reliable enough that developers don't hit surprising breakage in normal use. Fix the sharp edges that make new users bounce.

#### Critical Bug Fixes

**VDOM structural patching** (#559) — The single biggest pain point for new users. Conditional blocks (`{% if %}`) that add/remove elements shift sibling VDOM paths, causing incorrect patches. Fix: implement stable node anchors (comment-node placeholders) so conditional content doesn't break surrounding elements. This removes the current requirement to use `style="display:none"` for all conditional rendering. *Phoenix LiveView solved this from day one with its HEEx comprehensions — we need parity.*

**Event sequencing during ticks** (#560) — Server-initiated ticks can collide with user events, silently dropping input. Fix: version-vector or event queue so user actions are never lost. *Phoenix handles this via Erlang's message ordering guarantees; we need explicit sequencing.*

**Focus preservation across re-renders** — When the VDOM patches the DOM, focused elements lose focus and cursor position. This makes typing in forms feel broken when other parts of the page update. Fix: capture `document.activeElement`, selection range, and scroll position before patching; restore after. *Phoenix preserves focus automatically via `phx-update="ignore"` and morph internals; React preserves it via reconciliation. This is table-stakes for feeling like a real app.*

#### JS Commands (Biggest DX Gap)

**JS Commands (`dj.push`, `dj.show`, `dj.hide`, `dj.toggle`, `dj.addClass`, `dj.removeClass`, `dj.transition`, `dj.dispatch`, `dj.focus`, `dj.set_attr`, `dj.remove_attr`)** — This is the single biggest DX gap vs Phoenix LiveView. Phoenix's `JS` module lets developers chain client-side DOM manipulations that execute instantly without a server round-trip: show/hide modals, toggle classes, add transitions, dispatch custom events — all from template attributes. Currently, djust requires a server round-trip for every UI change, creating perceptible latency for simple interactions like opening a dropdown. Implementation: a `DJ` command builder (Python-side) that serializes to a JSON instruction set executed by the client JS. Commands must survive DOM patches (classes added by `dj.addClass` persist across re-renders).

```python
# Target API (Python-side, used in templates)
from djust import DJ

# In template:
# <button dj-click="{{ DJ.push('toggle_sidebar') | DJ.toggle('#sidebar') | DJ.toggle_class('active', '#btn') }}">
# Executes client-side instantly, THEN pushes event to server
```

#### Quick Wins (High impact, low effort)

**Connection state CSS classes** — Auto-apply `dj-connected` / `dj-disconnected` / `dj-loading` CSS classes to the body element based on WebSocket state. Phoenix does this with `phx-connected`/`phx-disconnected` — trivial to implement, big DX win for showing connection status without custom JS.

**`dj-confirm` attribute** — Native browser confirmation dialog before executing an event. `<button dj-click="delete_item" dj-confirm="Are you sure?">Delete</button>`. Phoenix has `data-confirm` — prevents accidental destructive actions without custom JS. Two hours to implement, saves every developer from writing the same confirmation pattern.

**`dj-disable-with` attribute** — Automatically disable submit buttons and replace text during form submission. `<button type="submit" dj-disable-with="Saving...">Save</button>`. Prevents double-submit and provides instant visual feedback. Phoenix's `phx-disable-with` is one of its most-loved small features.

**`dj-key` attribute** — Explicit element keying for lists (like React's `key` prop / Phoenix's `phx-key`) to improve VDOM diffing accuracy for list reordering.

**Window/document event scoping** — `dj-window-keydown`, `dj-window-scroll`, `dj-document-click` attributes for binding events to `window` or `document` rather than the element itself. Phoenix has `phx-window-*`. Essential for keyboard shortcuts, infinite scroll triggers, and click-outside-to-close patterns.

**`dj-debounce` / `dj-throttle` as HTML attributes** — Currently debounce/throttle only works as Python decorators on event handlers, applying the same delay to every caller. Phoenix allows per-element control: `<input dj-change="search" dj-debounce="300">` vs `<select dj-change="filter" dj-debounce="0">`. This is strictly more flexible — the Python decorator becomes the default, the attribute becomes the override. Implementation: client-side timer per element+event pair, ~50 lines of JS.

**`live_title` — Dynamic page title** — Update `<title>` from the server without a page reload. Phoenix's `live_title_tag` is trivial but surprisingly impactful — it enables unread counts, status indicators, and notification badges in browser tabs. API: `self.page_title = "Chat (3 unread)"` in any event handler, sent as a lightweight WS message that updates `document.title` without a VDOM diff.

**`dj-mounted` event** — Fire a server event when an element enters the DOM (after VDOM patch inserts it). Use cases: scroll-into-view for new chat messages, trigger data loading when a tab becomes active, animate elements on appearance. Phoenix has `phx-mounted`. Pairs naturally with `dj-remove` (exit event). Implementation: MutationObserver watching for elements with `dj-mounted` attribute.

**`dj-click-away`** — Fire an event when the user clicks outside an element. `<div dj-click-away="close_dropdown">`. This is the single most common pattern developers manually implement in every interactive app (dropdowns, modals, popovers). Currently requires `dj-window-click` + manual coordinate checking or a JS hook. One attribute, ~20 lines of JS, eliminates boilerplate in every project.

**`dj-lock` — Prevent concurrent event execution** — Disable an element until its event handler completes. `<button dj-click="save" dj-lock>Save</button>` prevents double-clicks and concurrent submissions. Different from `dj-disable-with` (which is cosmetic) — `dj-lock` actually blocks the event from firing again until the server acknowledges completion. Phoenix handles this implicitly via its event acknowledgment protocol. Implementation: client-side `disabled` flag per element, cleared on server response. ~30 lines of JS. Pairs with `dj-disable-with` for the full pattern: lock + visual feedback.

**`dj-auto-recover` — Custom reconnection recovery** — Fires a custom server event on WebSocket reconnect instead of the default form-value replay. `<div dj-auto-recover="restore_state">`. Use case: views with complex state (drag positions, canvas state, multi-step wizard progress) that can't be recovered from form values alone. The handler receives `params` with whatever the client can serialize from the DOM. Phoenix's `phx-auto-recover` solves the same problem — not every reconnection fits the "replay form values" pattern.

**`dj-value-*` — Static event parameters** — Pass static values alongside events without `data-*` attributes or hidden inputs. `<button dj-click="delete" dj-value-id="{{ item.id }}" dj-value-type="soft">Delete</button>` sends `{"id": "42", "type": "soft"}` as params. Phoenix's `phx-value-*` is used everywhere — it's the standard way to pass context with events. Currently djust requires either `data-*` attributes (which the client must extract) or hidden form fields. This is ~20 lines of JS (collect `dj-value-*` attributes on the trigger element and merge into event params) but eliminates boilerplate in every template. *This is arguably the single most underrated Phoenix feature — once developers have it, they use it on every event.*

**`handle_params` callback** — Invoked when URL parameters change via `live_patch` or browser navigation. Phoenix's `handle_params/3` is the standard pattern for URL-driven state (pagination, filters, search, tab selection). Currently, `live_patch` updates the URL but there's no server-side callback to react to the change — developers must manually parse `request.GET` in event handlers. API: `def handle_params(self, params, url, **kwargs)` called after `mount()` on initial render and on every subsequent URL change. This enables bookmark-friendly state: users can share URLs like `/dashboard?tab=metrics&range=7d` and the view reconstructs itself from params. ~50 lines Python. *Without this, `live_patch` is only half-implemented — you can push URLs but can't react to them.*

**`dj-shortcut` — Keyboard shortcut binding** — Declarative keyboard shortcuts on any element. `<div dj-shortcut="ctrl+k:open_search, escape:close_modal">`. Use cases: command palettes (`Ctrl+K`), close modals (`Escape`), save (`Ctrl+S`), undo (`Ctrl+Z`), navigation (`j`/`k` for list items). Currently requires `dj-window-keydown` + manual key checking in Python event handlers — a round-trip for every keypress. `dj-shortcut` handles matching client-side and only fires the event on match. Supports modifier keys (`ctrl`, `shift`, `alt`, `meta`), key combos, and `prevent` modifier to suppress browser defaults (`dj-shortcut="ctrl+s:save" dj-shortcut-prevent`). ~60 lines of JS. *Every productivity app needs keyboard shortcuts. React developers use `react-hotkeys-hook`; this is the built-in equivalent.*

**`dj-copy` — Copy to clipboard** — Copy text content to clipboard on click without a server round-trip. `<button dj-copy="#code-block">Copy</button>` copies the text content of `#code-block`. `<button dj-copy="literal text here">Copy</button>` copies the literal string. Optionally fires a server event for analytics: `dj-copy="#code-block" dj-copy-event="copied"`. Shows visual feedback (configurable CSS class, default: `dj-copied` for 2s). Use cases: code snippets, share links, API keys, referral codes. Currently requires a `dj-hook` for every copy button. ~30 lines of JS. *This is the kind of small built-in that makes developers think "this framework gets it" — every documentation site, every admin panel needs copy buttons.*

**`dj-cloak` — Prevent flash of unstyled content** — Add a `dj-cloak` CSS class to elements that should be hidden until the LiveView WebSocket connects. djust's client JS removes the class on connection. Ship a one-line CSS rule (`[dj-cloak] { display: none !important; }`) in the default stylesheet. Use case: interactive elements (dropdowns, tabs, live search) that look broken before JS hydrates them. Vue has `v-cloak`, Alpine has `x-cloak` — this is expected in any framework that enhances server-rendered HTML. ~5 lines of JS.

**`on_mount` hooks (promoted from v0.6.0)** — Module-level hooks that run on every LiveView mount, declared via `@on_mount` decorator or class attribute. Use cases: authentication checks, telemetry, tenant resolution, feature flags. Phoenix added this in v0.17 and it's now the standard pattern for cross-cutting concerns. Replaces repetitive auth checks in individual `mount()` methods. *Promoted to v0.4.0 because every real app needs cross-cutting mount logic from day one — auth, tenant resolution, telemetry. Without this, developers copy-paste the same 5 lines into every view's `mount()`. Simple to implement (~100 lines Python), massive DX win.*

```python
# Target API
from djust import LiveView, on_mount

@on_mount
def require_auth(view, request, **kwargs):
    if not request.user.is_authenticated:
        return view.redirect('/login/')

class DashboardView(LiveView):
    on_mount = [require_auth]
```

**`_target` param in form change events** — When multiple fields share one `dj-change="validate"` handler, Phoenix sends a `_target` parameter identifying which field triggered the change. This is essential for efficient per-field validation without needing separate handlers per field. Currently djust fires `dj-change` with the field value but doesn't tell the server *which* field changed, forcing developers to either write one handler per field or re-validate everything. Implementation: the client includes the triggering element's `name` attribute as `_target` in the event params. ~10 lines JS, ~5 lines Python. *This is one of those "obvious in hindsight" features — once developers have it, they wonder how they ever wrote forms without it. Phoenix has had it since day one.*

**`dj-page-loading` — Navigation loading bar** — A thin animated progress bar at the top of the viewport during `live_redirect`, `live_patch`, and TurboNav navigation. `<div dj-page-loading class="my-loading-bar">`. Auto-shows when navigation starts, auto-hides on completion. Configurable appearance via CSS (color, height, animation). YouTube, GitHub, and every modern SPA use this pattern (NProgress). Currently djust navigations have no visual feedback — the page appears to freeze. ~40 lines JS + 10 lines CSS. *This is the single cheapest way to make navigation feel fast — a visible progress indicator makes 200ms feel instant while no indicator makes 100ms feel broken. Neither Phoenix nor React include this natively.*

**Flash messages (promoted from v0.5.0)** — Built-in ephemeral notification pattern with `self.put_flash(level, message)` and auto-dismissing client-side rendering. Phoenix's `put_flash` is used in virtually every app. *Promoted to v0.4.0 because this is the #1 pattern developers reinvent in every project. A `FlashMixin` with `put_flash('info', 'Saved!')`, a `{% dj_flash %}` template tag, and ~40 lines of client JS for appear/auto-dismiss animations. Flash messages survive `live_patch` but clear on `live_redirect`. Without this, every djust app ships with a slightly different homegrown toast system.*

#### Developer Tooling

**Error message quality** — Replace silent HTML comments (`<!-- djust: unsupported tag -->`) with visible warnings in DEBUG mode. Surface Rust template engine fallback reasons in the debug panel and server logs. Improve VDOM path error messages to show which element failed and suggest fixes.

**`manage.py djust_doctor`** — Single diagnostic command that verifies: Rust extension loaded, Channels configured, Redis reachable (if configured), template compatibility scan, Python/Django version support.

**Latency simulator** — Dev-only tool (in debug panel) to add artificial latency to WebSocket messages. Essential for testing loading states, optimistic updates, and transitions under real-world conditions. Phoenix includes this built-in.

**Profile & improve performance** — Use existing benchmarks in `tests/benchmarks/` as baselines. Profile the full request path: HTTP render, WebSocket mount, event, VDOM diff, patch. Target: <2ms per patch, <5ms for list updates.

#### Reconnection Resilience

**Form recovery on reconnect** — When WebSocket reconnects after a disconnect, the client should auto-fire `dj-change` with current DOM form values to restore server state. Phoenix does this automatically — users type into a form, lose connection briefly, reconnect, and nothing is lost. Currently djust loses all form state on reconnect.

**Reconnection backoff with jitter** — Exponential backoff with random jitter on WebSocket reconnection to prevent thundering herd after a server restart. Display reconnection attempt count in the connection status UI.

### Milestone: v0.5.0 — Components, Async Loading & Streams

**Goal**: Make djust capable of building complex, production UIs — dashboards, admin panels, data-heavy apps — with the same component patterns React developers expect, and make data loading feel instant.

**`assign_async` / `AsyncResult` (promoted from v0.7.0)** — High-level async data loading inspired by Phoenix's `assign_async` and React's Suspense. Wrap a function in `assign_async()` — the template receives an `AsyncResult` with `.loading`, `.ok`, `.failed` states and renders accordingly. Multiple async assigns load concurrently. Auto-cancels on navigation. Nested async loading within components enables independent loading boundaries (one slow query doesn't block the entire page). *Promoted from v0.7.0 because this is the #1 pattern for building responsive dashboards — every panel loads independently with its own skeleton state. Without this, developers either block the entire mount on the slowest query or manually wire up `start_async` + loading flags for every data source. Phoenix added this in 0.19 and it immediately became the default pattern for all data loading.*

```python
# Target API
class DashboardView(LiveView):
    def mount(self, request, **kwargs):
        self.assign_async('metrics', self._load_metrics)
        self.assign_async('notifications', self._load_notifications)
        # Template renders loading states independently:
        # {% if metrics.loading %}<div class="skeleton">{% endif %}
        # {% if metrics.ok %}{{ metrics.result }}{% endif %}
        # {% if metrics.failed %}Error: {{ metrics.error }}{% endif %}

    async def _load_metrics(self):
        return await expensive_query()
```

**Component `update` callback** — Phoenix's `update/2` on LiveComponents lets you transform assigns before render — essential for components that need to derive internal state from parent-provided props. Without this, components must put derivation logic in `render()` or `get_context_data()`, mixing state transformation with presentation. API: `def update(self, assigns)` called before every render when parent assigns change. The component can transform, validate, or ignore incoming assigns. ~40 lines Python. *This is the key to building reusable component libraries — components need to control how external data maps to internal state. React's `getDerivedStateFromProps` / `useMemo` + `useEffect` serve the same purpose.*

**View Transitions API integration (promoted from v0.6.0)** — Use the browser's native View Transitions API for animated page transitions during `live_redirect` and TurboNav navigation. `<main dj-transition="slide-left">` applies a named view transition when the content changes. Falls back gracefully in unsupported browsers. Low implementation effort (~60 lines JS), supported in Chrome, Edge, Safari, and Firefox (implementing). *Promoted because this is the single biggest perceived-quality improvement available — animated transitions make server-rendered apps feel like native apps. No other server-side framework has first-class View Transitions API support yet. Combined with `dj-page-loading`, navigation goes from "feels like 2010 Django" to "feels like a native app" — a critical perception for adoption.*

**Nested LiveComponents with targeted events** — LiveComponents within LiveComponents with event bubbling through the component tree. Each component maintains its own VDOM tree for independent diffing. Events target their owning component via a `dj-target="component_id"` attribute (Phoenix's `@myself`). Named slots for composition (`<slot:header>`, `<slot:footer>`). Declarative assigns with validation. This is the foundation for building complex UIs from reusable pieces.

**Direct-to-S3 uploads** — The core upload system (chunked binary WS frames, drag-and-drop, progress, validation) is complete. Add optional direct-to-S3/GCS with pre-signed URLs via `presign_upload()` callback, bypassing the server for large files. Django `UploadedFile` compatibility so existing model `FileField`/`ImageField` patterns work unchanged with the pre-signed flow.

**Stream enhancements** — The existing `StreamsMixin` handles basic append/replace, but needs parity with Phoenix streams: `:limit` option to cap client-side DOM elements (enables virtual scrolling with minimal memory), `dj-viewport-top` / `dj-viewport-bottom` events that fire when the first/last stream child enters the viewport (enables bidirectional infinite scroll), and `stream_configure()` for per-stream options. Combined, these let you build infinite-scroll feeds, chat histories, and large data tables without keeping items in server memory.

**`handle_info` pattern (promoted from v0.6.0)** — Explicit handler for external messages (Celery task completion, webhook notifications, admin broadcasts) that arrive via Channels layer but aren't user-initiated events. Phoenix's `handle_info/2` is the standard pattern for reacting to external signals. *Promoted because any production app with background tasks (Celery, webhooks) needs this immediately. Currently djust handles this implicitly through broadcasting, but a dedicated `handle_info()` method with typed message dispatching is cleaner and more discoverable.* API: `self.subscribe('topic')` in mount, `def handle_info(self, message)` with `match` dispatch.

**Template fragments (static subtree tracking)** — Track which parts of a template are static (never change between renders) and which are dynamic. Only re-render and diff the dynamic subtrees. Phoenix does this at compile time with HEEx — static parts are sent once and never re-transmitted. For djust, the Rust template engine can fingerprint static subtrees on first render, then skip them in subsequent VDOM diffs. This is the single biggest wire-size optimization possible — most templates are 80%+ static HTML. Combined with selective re-rendering, this makes large pages as efficient as small ones. Implementation: Rust-side static tree fingerprinting + client-side fragment cache. *This is how Phoenix achieves sub-millisecond updates on complex pages — we need it for parity.*

**`dj-feedback` (promoted from v1.0.0)** — Show validation errors only after user interaction, not on pristine fields. `<div dj-feedback="email">` only shows error styling after the user has blurred or submitted the email field. Currently, real-time validation can show errors before the user has even started typing. Phoenix's `phx-feedback-for` solves this. *Promoted because form UX is critical for app quality and this is a common complaint from djust users building forms with `FormMixin`.*

**Nested form handling (`inputs_for`)** — Phoenix's `inputs_for` allows rendering sub-forms for associated records (e.g., an order with line items). Map to Django's formset/inline-formset patterns with LiveView-aware wrappers: `self.inputs_for('line_items', LineItemForm, queryset=self.order.items.all())`. Auto-generates add/remove UI, maintains form indexes across VDOM patches, validates nested forms together. This is a prerequisite for the post-1.0 "Dynamic Form Fields" feature but the basic case (render existing associations, add/remove) is needed much sooner for real app development.

**Scoped loading states (`dj-loading`)** — Show loading indicators scoped to specific events or components rather than the entire page. `<div dj-loading="search">Searching...</div>` only shows while the `search` event is in-flight. Phoenix scopes loading via `phx-loading` classes. Currently, djust's `@loading` decorator applies globally — you can't show a spinner on one button while the rest of the page stays interactive. Implementation: client tracks in-flight event names, toggles `dj-loading` elements by matching their attribute value to the pending event.

**Error boundaries** — Inspired by React's `<ErrorBoundary>`. If a LiveComponent's event handler or render raises an exception, isolate the failure to that component — show an error fallback UI for the broken component while the rest of the page continues working. Currently, any unhandled error crashes the entire LiveView. Implementation: wrap component `render()` and `handle_event()` in try/except, render a configurable error template on failure, and allow retry.

**Selective re-rendering** — Only re-render and diff components whose state actually changed. Currently, every event triggers a full template re-render and VDOM diff for the entire view. For pages with many components, this is wasteful. Track which instance attributes changed in each event handler and only re-render affected component subtrees. *React does this via reconciliation; Phoenix does this via separate component VDOM trees. This is the key to scaling to complex UIs.*

**`@computed` decorator for derived state** — Memoize derived values that depend on other state, re-computing only when dependencies change. React's `useMemo` equivalent. Avoids redundant computation in `get_context_data()` and makes the dependency graph explicit.

```python
from djust.decorators import computed

class ProductView(LiveView):
    def mount(self, request, **kwargs):
        self.items = []
        self.tax_rate = 0.08

    @computed('items', 'tax_rate')
    def total_price(self):
        subtotal = sum(i['price'] * i['qty'] for i in self.items)
        return subtotal * (1 + self.tax_rate)
    # Only recomputed when self.items or self.tax_rate changes
```

**`dj-spread` / attribute rest** — Pass remaining HTML attributes through to the root element of a component. Essential for building reusable component libraries where consumers need to add `class`, `id`, `aria-*`, `data-*` attributes without the component explicitly declaring each one. Phoenix's `{@rest}` pattern is heavily used in component libraries. API: `<div {{ attrs|spread }}>` in the component template, where `attrs` is a dict of extra attributes passed by the parent. Implementation: Rust template filter that serializes a dict to safe HTML attribute pairs.

```python
# Component usage in parent template:
{% component "button" variant="primary" class="mt-4" aria-label="Save" data-testid="save-btn" %}
# Component template renders: <button class="btn btn-primary mt-4" aria-label="Save" data-testid="save-btn">
```

**`dj-lazy` — Lazy component loading** — Defer rendering of below-fold or hidden components until they enter the viewport. `<div dj-lazy="load_comments" dj-lazy-threshold="200px">Loading...</div>` renders the placeholder HTML, then fires `load_comments` when the element is within 200px of the viewport (via IntersectionObserver). The handler populates state and the component renders in-place. Use cases: tab content that's hidden by default, below-fold dashboard widgets, heavy data tables in scrollable containers. React's `lazy()` + `<Suspense>` and Astro's `client:visible` solve the same problem. Pairs naturally with `assign_async` — lazy-load the component, then async-load its data. ~40 lines JS + Python decorator. *This is essential for complex pages with 10+ panels — loading everything on mount wastes server resources and slows initial render. Lazy loading is table-stakes for React apps; djust should match.*

**Component context sharing** — React's Context API equivalent for passing data through the component tree without explicit prop drilling. `self.provide_context('theme', self.theme)` in a parent view makes `theme` available to all descendant components via `self.consume_context('theme')`. Use cases: theme/dark mode, current user, locale, feature flags, permissions — any data that many components need but shouldn't be passed through every intermediate component. Phoenix handles this via assigns on the socket; React has `useContext`. Implementation: context dict on the LiveView session, accessible to all child components. ~80 lines Python. *Component context is the difference between a component system that works for demos and one that works for real apps with 20+ components deep.*

**`dj-trigger-action` — Bridge live validation to standard form POST** — Trigger a standard HTML form submission after LiveView validation passes. `<form dj-trigger-action="submit_form">` — the LiveView validates in real-time, and when validation passes, triggers a standard POST to the form's `action` URL. Use cases: OAuth flows, payment gateways, file downloads — any action that requires a full HTTP POST but benefits from live validation. Phoenix's `phx-trigger-action` is essential for integrating with external services that expect standard form submissions. ~30 lines JS + Python flag.

**Rust template engine parity** — Close the remaining gaps: model attribute access via PyO3 `getattr` fallback, `&quot;` escaping in attribute context, broader custom tag handler support.

**Service worker core improvements** — Instant page shell (cached head/nav/footer served instantly, swap `<main>` on response). WebSocket reconnection bridge (buffer events in SW during disconnect, replay on reconnect).

### Milestone: v0.6.0 — Production Hardening & Interactivity

**Goal**: Make djust production-ready for teams deploying real apps, and close the remaining interactivity gap with client-side frameworks.

**Animations & transitions** — Declarative `dj-transition` attribute for enter/leave CSS transitions with three-phase class application (start → active → end), matching Phoenix's `JS.transition`. `dj-remove` attribute for exit animations before element removal. FLIP technique for list reordering animations. `dj-transition-group` for animating list items entering/leaving (React's `<TransitionGroup>` / Vue's `<transition-group>` equivalent — essential for todo lists, kanban boards, search results). Skeleton/shimmer loading state components. *(View Transitions API integration promoted to v0.5.0.)*

**Sticky LiveViews** — Mark a LiveView as `sticky=True` in `live_render()` to keep it alive across live navigations. Use case: persistent audio/video player, sidebar, notification center. The sticky view doesn't unmount/remount when the user navigates — it stays connected and retains state. Phoenix added this and it's a big win for app-shell patterns.

**`dj-mutation` — DOM mutation events** — Fire a server event when specific DOM attributes or children change via MutationObserver. `<div dj-mutation="handle_change" dj-mutation-attr="class,style">`. Use case: third-party JS libraries (charts, maps, rich text editors) that modify the DOM outside djust's control — the server needs to know about these changes to keep state in sync. Currently requires a custom `dj-hook` for every integration. One declarative attribute replaces boilerplate in every third-party-widget integration. Implementation: MutationObserver config from attributes, debounced event push. ~50 lines of JS.

**`dj-sticky-scroll` — Auto-scroll preservation** — Automatically keep a scrollable container pinned to the bottom when new content is appended (chat messages, logs, terminal output), but stop auto-scrolling if the user scrolls up to read history. Resume auto-scroll when they scroll back to bottom. This is the #1 asked-for behavior in chat and log-viewer apps and currently requires a custom `dj-hook` with scroll position math. `<div dj-sticky-scroll>` handles it declaratively. ~40 lines of JS.

**Graceful degradation** — Handle Redis unavailability without crashing. Fall back to in-memory state with a warning. Health check endpoint (`/djust/health/`) for load balancer integration. Connection pool metrics via Django's `check` framework.

**Monitoring integration** — First-class Sentry integration. OpenTelemetry spans for template rendering, VDOM diffing, and WebSocket message handling. Lifecycle telemetry events (mount, handle_event, render) matching Phoenix's telemetry pattern. Custom telemetry hooks for application-specific metrics.

**CSP nonce support** — Content-Security-Policy nonce propagation for inline scripts and styles. djust's client JS and any inline handlers should respect CSP nonces passed via template context.

**`dj-intersection` — Viewport visibility events** — Fire server events when elements enter/leave the viewport via IntersectionObserver. `<div dj-intersection="load_more" dj-intersection-threshold="0.5">`. Use cases beyond infinite scroll: lazy-load expensive components, analytics (track which sections users actually see), pause/resume animations, load images on demand. This is the modern replacement for scroll listeners and powers the best infinite scroll / lazy-loading patterns. React libraries like `react-intersection-observer` are extremely popular — having this built-in is a meaningful DX win.

**State undo/redo** — Built-in undo stack for state changes with `self.undo()` and `self.redo()` methods. `<button dj-click="undo" dj-shortcut="ctrl+z:undo">Undo</button>`. Opt-in per view via `undo_fields = ['items', 'layout']` — only tracked fields are snapshotted. Configurable history depth (default: 50 steps). Use cases: kanban board reordering, form wizards, diagram editors, any app where "oops, undo that" is expected behavior. React apps use libraries like `use-undo` or `immer` patches; Phoenix has no built-in equivalent — making this a djust differentiator. Implementation: state snapshot ring buffer (~100 lines Python), paired with `@event_handler(undoable=True)` to mark which actions create undo points. *Every productivity app needs undo. Building it into the framework means it works correctly with VDOM diffing, broadcasting, and optimistic updates — getting these interactions right is non-trivial and shouldn't be left to each developer.*

**Connection multiplexing** — Share a single WebSocket connection across multiple LiveView instances on the same page. Currently, each `{% live_render %}` opens its own WebSocket — a page with 5 live components makes 5 connections. Multiplexing routes messages by view ID over one connection, reducing server resources and connection overhead. Phoenix does this via its channel multiplexer. Implementation: client-side message router + server-side consumer dispatch. ~200 lines JS + Python. *Essential for pages with multiple independent live sections (dashboard widgets, sidebar + main content, notification badge + page body).*

**Batch state updates** — Multiple `self.x = ...` assignments within a single event handler should always produce exactly one re-render and one VDOM diff. This already works for synchronous handlers, but ensure it's guaranteed for `start_async` callbacks, `handle_info`, and component-to-parent communication. React batches state updates automatically; djust should too, everywhere.

**Multi-tab state sync** — Use the BroadcastChannel API to keep state synchronized across browser tabs without additional WebSocket connections. When a user performs an action in one tab (e.g., marks a notification as read, toggles dark mode, completes a task), all other tabs update instantly. Currently, each tab is an independent LiveView instance — changes in one tab require a full page refresh or server broadcast to reflect in others. Implementation: client-side BroadcastChannel listener that receives state diffs and applies them via the existing VDOM patch pipeline. Opt-in per view via `sync_across_tabs = ['notifications_count', 'theme']` class attribute. ~60 lines of JS.

**Offline mutation queue** — When the WebSocket disconnects, queue user events in IndexedDB instead of dropping them. On reconnect, replay the queue in order. Combined with optimistic UI (`@optimistic`), this creates a seamless offline experience — users can keep clicking, typing, and submitting; the UI responds instantly; and everything syncs when connectivity returns. Phoenix doesn't have this (Erlang assumes connectivity), making it a differentiator for djust in mobile/spotty-connection scenarios. Implementation: IndexedDB event queue + replay logic in the reconnection handler. ~150 lines of JS + Python replay validation.

**Streaming initial render (chunked HTTP)** — Send the page shell (head, nav, footer) immediately as a chunked HTTP response, then stream expensive content sections as they complete — without waiting for the slowest query. Different from WebSocket streaming (which requires JS) — this works on the initial HTTP request before any JS loads. The browser renders the shell instantly, then progressively fills in content. React 18's streaming SSR (`renderToPipeableStream`) popularized this; Rails Turbo uses a similar "page shell" pattern. Implementation: Django `StreamingHttpResponse` with template fragment rendering + Rust engine support for `{% dj_stream_placeholder "section_name" %}` markers that get replaced as data arrives. ~150 lines Python + Rust template tag. *This eliminates the biggest perceived-performance problem in server-rendered apps: the blank screen while the slowest database query runs. Users see the navigation and layout immediately, with skeleton states for pending content. Combined with `assign_async` (WebSocket-based), this covers both initial load and subsequent interactions.*

**Time-travel debugging** — Extend the existing debug panel with state snapshot recording and replay. Every state change (event handler, async result, handle_info) captures a before/after snapshot. Developers can scrub through the timeline, inspect state at any point, and replay from a snapshot to reproduce bugs. Export snapshots as JSON for bug reports. React DevTools' "Components" tab and Redux DevTools' time-travel are the gold standard here. Implementation: state diff ring buffer in the debug panel's JS (~100 lines), snapshot serialization via existing JIT serializer. *The current debug panel shows event history but can't answer "what was the state when the user clicked X?" — time-travel fills that gap and makes djust's debugging story best-in-class.*

**`dj-resize` — Element resize events** — Fire a server event when an element's dimensions change via ResizeObserver. `<div dj-resize="handle_resize" dj-resize-debounce="200">`. Use cases: responsive component behavior (switch from table to card layout at breakpoints), chart re-rendering on container resize, editor pane resize handling. Unlike CSS media queries (which watch viewport), ResizeObserver watches individual elements — essential for components that live inside resizable containers (split panes, sidebars). Implementation: ResizeObserver + debounced event push. ~40 lines of JS.

**Advanced service worker features** — VDOM patch caching (cache last rendered DOM per page; diff against fresh response on back-navigation). LiveView state snapshots (serialize on unmount, restore on back-nav). Request batching for multi-component pages.

### Milestone: v0.7.0 — Navigation, Smart Rendering & AI Patterns

**Goal**: Make navigation feel like a SPA and establish djust as the best framework for AI-powered applications.

**`live_session` enhancements** — Basic `live_session()` routing is implemented (shared WS connections, route map injection). Remaining work: shared `on_mount` hooks per session, root layout declaration, and automatic full-HTTP navigation when crossing session boundaries. This is how Phoenix structures apps — each session is a logical unit with shared auth, layout, and state lifecycle.

**Push navigation from server** — `self.push_navigate('/new-path/')` to trigger SPA-like navigation from an event handler. Different from `redirect()` (full HTTP) — this keeps the WebSocket alive and mounts a new LiveView without a page reload. Combined with `self.push_patch('/same-view/?page=2')` (update URL without remount, triggers `handle_params()`), this gives full Phoenix navigation parity.

**Portal rendering** — Render content into a DOM container outside the component's tree. Use case: modals, toasts, tooltips, and dropdowns that are logically owned by a deeply nested component but need to render at `<body>` level for z-index/overflow reasons. Template directive: `{% dj_portal target="#modal-container" %}...{% enddj_portal %}`.

**Back/forward state restoration** — When the user navigates with browser back/forward, restore the previous view state from a serialized snapshot rather than remounting from scratch. The URL `popstate` event triggers `handle_params()` with the previous parameters, but expensive state (search results, scroll position, expanded accordions) should be cached client-side and restored instantly. *React Router does this with loader caching; Phoenix does it with `push_patch` state. This is what makes SPA navigation feel native.*

**Stale-while-revalidate pattern** — Show the previous/cached render instantly on mount, then update asynchronously when fresh data loads. Combined with `assign_async`, this creates instant page transitions — the user sees the last-known state immediately (< 16ms) while the server fetches current data. API: `self.assign_stale('metrics', self._load_metrics, stale_ttl=30)` — serves cached value within TTL, fetches fresh in background, re-renders on completion. *React Query / SWR popularized this pattern; it's the key to making server-rendered apps feel as fast as client-side SPAs. Phoenix doesn't have this natively, making it a djust differentiator.*

**Islands of interactivity** — Mark specific sections of a page as "live" while the rest stays static HTML. `{% dj_island %}...{% enddj_island %}` wraps interactive zones that establish their own WebSocket connections. The surrounding page renders once via HTTP and is never re-rendered. Use case: a blog post (static) with a comment section (live), a product page (static) with an add-to-cart button (live). Reduces WebSocket connections, server memory, and VDOM tree size dramatically for content-heavy pages. *Astro popularized this pattern; Fresh (Deno) and Qwik use it. React Server Components achieve similar results. This is the most requested architectural pattern for content-heavy sites that need small interactive zones.* Implementation: each island is a lightweight LiveView with its own VDOM tree, mounted lazily on scroll-into-view (reusing existing `dj-lazy` infrastructure).

**Server-only components** — Components that render once on HTTP and never establish a WebSocket connection. Use case: static headers, footers, marketing sections, and any content that doesn't need interactivity. Reduces WebSocket connection count and server memory for pages that mix interactive and static content. *React Server Components popularized this pattern — not everything needs to be live.*

**AI application primitives** — First-class patterns for building AI-powered applications, djust's strongest vertical. Building on the existing `StreamingMixin`, add: `{% dj_ai_stream %}` template component with built-in markdown rendering, code syntax highlighting, and copy buttons. `self.stream_ai(stream_name, llm_generator)` helper that handles backpressure, token batching, and error recovery for any LLM API (OpenAI, Anthropic, local models). Typing indicator that auto-shows during AI generation. Conversation history component with scroll anchoring. Tool-use visualization (show when AI is "thinking" or calling tools). *Django's ORM + Celery + djust's streaming is already the best stack for AI apps — these primitives make it 10x faster to build ChatGPT-like interfaces. No other framework has purpose-built AI streaming components.*

**Django admin LiveView widgets** — Drop-in LiveView-powered widgets for Django's admin interface. `DjustAdminMixin` on any `ModelAdmin` enables real-time dashboards, live search/filter, inline editing, and bulk action progress within the admin. Use cases: real-time order status dashboards, live log viewers, monitoring panels, AI-powered admin actions with streaming output. This is a unique djust differentiator — no other LiveView-style framework integrates with an existing admin like Django's. Implementation: admin template overrides + a `DjustAdminWidget` base class that renders a mini LiveView inside admin change forms/list views. ~300 lines Python. *Django's admin is used by 90%+ of Django projects. Making it reactive with zero config is the single most effective demo of djust's value proposition — "add one mixin and your admin goes live."*

**Prefetch on hover/intent** — Pre-load the next page's data when the user hovers over a link or shows navigation intent (mouse movement toward link, touch start). `<a dj-prefetch href="/dashboard">Dashboard</a>` triggers a lightweight prefetch request on hover, so the page loads instantly on click. Different from existing `22-prefetch.js` (which pre-fetches all visible links) — this is intent-based and targeted. Remix, Next.js, and Astro all use hover-prefetch as their primary strategy for fast navigation. Implementation: `mouseenter` listener with 65ms delay (avoids prefetch on fly-over), prefetch via `<link rel="prefetch">` or fetch API with abort on `mouseleave`. ~50 lines JS. *Combined with View Transitions API, this makes navigation feel literally instant — the page is already loaded before the user clicks.*

**Server functions (RPC-style calls, promoted from post-v0.7.0 consideration)** — Call server-side Python functions from client JS and get structured results back, without defining an event handler or managing state. `const result = await djust.call('search_users', {query: 'john'})` invokes a decorated Python function and returns JSON. Different from event handlers (which trigger re-renders) — server functions are pure request/response, ideal for typeahead suggestions, autocomplete, validation checks, and any pattern where you need data but don't want a full re-render. React Server Actions and tRPC popularized this pattern. API: `@server_function` decorator on view methods, client-side `djust.call()` with promise return. ~100 lines Python + ~30 lines JS.

### Milestone: v0.8.0 — Server Actions, Async Streams & Form Patterns (NEW)

**Goal**: Bridge the gap between Phoenix 1.0's async primitives and React 19's server actions model. Make djust the most ergonomic framework for forms, data mutation, and async data flows.

**Async Streams (Phoenix 1.0 parity)** — Phoenix 1.0 introduced `stream/3` with `:reset` and async enumeration. djust's `StreamsMixin` covers basic append/replace but lacks: async stream sources (wrap an async generator and stream items as they arrive), `:reset` to clear and replace all items in a stream, bulk insert/delete operations, and stream-level error handling. This is the foundation for infinite scroll, real-time feeds, and large dataset rendering without loading everything into memory. Implementation: extend `StreamsMixin` with `stream_async(name, async_generator)`, `stream_reset(name, items)`, `stream_delete(name, item_id)`, `stream_insert_at(name, index, item)`. ~200 lines Python + Rust VDOM support for stream containers.

```python
class FeedView(LiveView):
    def mount(self, request, **kwargs):
        self.assign_async('posts', self._load_posts)

    async def _load_posts(self):
        async for batch in Post.objects.filter(published=True).aiter(chunk_size=50):
            self.stream_insert('feed', batch)
```

**Server Actions (React 19 pattern)** — React 19's `useActionState` and form actions provide a pattern where form submissions automatically handle pending states, error states, and optimistic updates. Map this to djust: `@action` decorator on methods that receive form data, automatically set `action.pending`, `action.error`, `action.result` states accessible in templates. Combined with `@optimistic`, this gives React 19-level form ergonomics without any client JS. Different from `@event_handler` — actions are specifically for mutations that should have standardized pending/error/success states.

```python
from djust.decorators import action

class TodoView(LiveView):
    @action
    def create_todo(self, title: str = "", **kwargs):
        # action.pending is True in template while this runs
        todo = Todo.objects.create(title=title, user=self.request.user)
        self.todos.append(todo)
        return {"created": todo.id}  # Sets action.result in template
        # If this raises, action.error is set automatically
```

**`dj-model` two-way binding improvements** — Currently `dj-model` sends every change to the server. Add client-side validation hooks (`dj-model-validate="pattern"` for regex, `dj-model-transform="uppercase"` for input transformation) that run before the server round-trip. Phoenix's form bindings with changesets are the gold standard — we need the equivalent ergonomics using Django's form/model validation. Also: `dj-model-lazy` that only syncs on blur (not every keystroke), reducing WS traffic for text inputs.

**Form recovery improvements** — Beyond basic reconnection recovery: serialize the entire form state (all field values, validation errors, dirty flags, focus position) on disconnect and restore it atomically on reconnect. Phoenix 1.0 handles this seamlessly. Also add `dj-recover="custom_handler"` for views with complex state that can't be inferred from DOM form values alone.

**`self.stream_to(component_id, ...)` — Targeted streaming** — Stream updates to a specific LiveComponent rather than the whole page. Use case: a dashboard with 6 panels where only one is receiving real-time data — currently streaming re-diffs the entire VDOM tree. With targeted streaming, only the receiving component's subtree is patched. Implementation: route stream operations through the component hierarchy, diff only the target subtree. ~100 lines Python + Rust component-scoped VDOM.

### Milestone: v1.0.0 — Stable Release

**Chrome DevTools extension** — Standalone extension showing VDOM tree alongside DOM, WebSocket message stream, component hierarchy with state, performance profiling.

**Documentation consolidation** — Single navigable `docs/` site with getting-started guide, auto-generated API reference, common pitfalls page, migration guides, cookbook of patterns. Migration guides from htmx, Laravel Livewire, and Phoenix LiveView for developers switching frameworks.

**Performance benchmarks** — Published, reproducible benchmarks comparing djust vs Phoenix LiveView, Laravel Livewire, and htmx on template render time, VDOM diff time, WebSocket message size, client JS bundle size, time-to-interactive.

**Plugin/extension system** — Third-party packages can register custom decorators, LiveComponent libraries, state backend implementations, and Rust template tag handlers.

**Starter templates** — 3-5 production-quality starter apps: blog with real-time comments, dashboard with live charts, e-commerce product browser, chat application, kanban board.

**VS Code / IDE extension** — Syntax highlighting and autocomplete for `dj-*` attributes in HTML templates. Go-to-definition from `dj-click="handler_name"` to the Python method. Template validation warnings for missing handlers. Snippet library for common patterns.

**TypeScript definitions for client hooks** — Type-safe `dj-hook` development with `.d.ts` files for the djust client API. Developers writing custom JS hooks should get autocomplete for lifecycle methods (`mounted`, `updated`, `destroyed`) and the hook API (`this.pushEvent`, `this.el`, etc.).

**Dead View / Progressive Enhancement (promoted from post-1.0)** — Initial HTTP render produces fully functional HTML that works without JavaScript (forms submit via POST, links navigate via HTTP). When JS loads, the LiveView takes over seamlessly. Phoenix calls this the "dead view" → "live view" transition. Combined with `dj-trigger-action`, this enables true progressive enhancement. *Promoted because progressive enhancement is a 1.0-worthy feature — shipping a framework that requires JS for basic form submissions in 2026 is a hard sell for government, accessibility-mandated, and SEO-critical projects. Django already renders forms server-side; we just need to keep them working without JS.*

**Accessibility audit & ARIA integration** — Ensure all built-in components and patterns meet WCAG 2.1 AA. `dj-live-region` attribute for automatic `aria-live` announcements when content updates via WebSocket (screen readers don't detect DOM mutations by default). `dj-focus-trap` for modals/dialogs. Built-in keyboard navigation for `dj-show`/`dj-hide` targets. Audit all `dj-*` attributes for screen reader compatibility. *Accessibility is a 1.0 requirement, not a nice-to-have. Phoenix LiveView was criticized for shipping without `aria-live` support — djust should lead here.*

---

## Future (Post-1.0)

### Framework Portability (Flask/FastAPI)

The Rust crates (`djust_vdom`, `djust_templates`, `djust_core`) are already framework-agnostic. Post-1.0, adapter packages could bring djust's rendering to other Python frameworks:

- **FastAPI adapter** (~800-1500 lines) — Starlette WebSocket handler, route registration, session bridge
- **Flask adapter** (~800-1500 lines) — Quart/Flask-SocketIO WebSocket handler, Blueprint routing
- **Standalone Rust crates** — Published independently for non-Python use

The `StateBackend` ABC and `TemplateLoader` trait provide clean abstraction boundaries. Status: gauging community interest via [GitHub Discussions](https://github.com/johnrtipton/djust/discussions).

### Advanced Collaboration

- Operational Transform (OT) or CRDT for conflict-free multi-user editing
- Real-time cursor positions and user-specific selections
- Collaborative form editing with field-level locking

### AI Integration

- `manage.py djust_generate` — Scaffold a LiveView from natural language description
- `.cursorrules` / IDE integration files for popular AI coding assistants
- Expanded MCP server coverage for all scaffolding patterns

### Binary Protocol

- MessagePack wire format (WebSocket binary frames) replacing JSON for smaller payloads and faster serialization. The client JS and Rust serializer already have stubs for this. Target: 30-50% reduction in WebSocket message size.

### Dynamic Form Fields

First-class API for adding/removing form fields dynamically. Phoenix supports this with `sort_param` and `drop_param`. Map to Django's formset patterns with LiveView-aware wrappers: `self.add_form_field()`, `self.remove_form_field()`, automatic re-indexing. Use case: invoice line items, survey builders, multi-step wizards with conditional fields.

### Distributed Presence (CRDTs)

Current presence tracking works within a single Channels layer. Phoenix Presence uses CRDTs for distributed consistency across clustered nodes. Evaluate whether Django Channels' group layer provides sufficient distribution or if we need a CRDT-based approach for multi-node deployments.

### Embedded LiveView (WebComponent export)

Package a LiveView as a standalone `<dj-widget>` Web Component that can be embedded in any HTML page (WordPress, Shopify, static sites). The Web Component bundles the djust client JS, establishes its own WebSocket connection, and renders inside a shadow DOM. Use case: add a live chat widget, a real-time price calculator, or a booking form to any existing site without rewriting it in Django. Implementation: `manage.py djust_export WidgetView --tag dj-booking-widget` generates a `.js` bundle + deployment instructions. ~300 lines JS + Python management command. *This is how djust escapes the "must be a Django project" constraint — any site can embed a djust-powered interactive widget. Stimulus, Turbo, and LiveWire all stay locked inside their framework; Web Component export is a unique distribution advantage.*

### Collaborative Cursor/Selection Sync

Real-time cursor positions, text selections, and pointer tracking across connected users. `CursorTracker` (presence.py) exists but is limited to basic position broadcasting. Full implementation needs: per-field text selection ranges (not just cursor position), pointer tracking on arbitrary elements (not just text inputs), visual overlays with user colors/names (like Google Docs), and conflict resolution for simultaneous edits. Builds on the existing `PresenceMixin` infrastructure.

### Predictive Prefetch (ML-Based)

Go beyond hover-based prefetch with intent prediction. Use client-side ML (TensorFlow.js micro-model, <50KB) to predict which link the user will click based on mouse trajectory, scroll velocity, and navigation history. Pre-fetch the predicted page's data before the user even hovers. Speculative — research needed on model size vs accuracy tradeoff. *Could be a genuine differentiator if the model is small enough — no framework does this today.*

### SSE Fallback — ✅ Completed (v0.3.8)

Implemented in `03b-sse.js`. Server → EventSource (GET), Client → HTTP POST. Same message handler interface as WebSocket.

---

## djust Differentiators

Features where djust leads rather than follows — things Phoenix LiveView and React don't offer natively:

| Feature | Why It Matters | Status |
|---------|---------------|--------|
| **Rust-powered rendering** | 10-100x faster templates than any Python framework; sub-ms VDOM diffs | **Done** |
| **Multi-tenant built-in** | Automatic data isolation per tenant — no third-party package needed | **Done** |
| **AI streaming primitives** | Purpose-built `StreamingMixin` for LLM token streaming with 60fps batching | **Done** (basic), v0.7.0 (full) |
| **Django ecosystem** | ORM, admin, auth, Celery, 10K+ packages — Phoenix/Elixir ecosystem is tiny by comparison | **Done** |
| **Offline mutation queue** | Queue events in IndexedDB during disconnect, replay on reconnect. Phoenix assumes connectivity | v0.6.0 |
| **Stale-while-revalidate** | Instant page transitions with cached renders + async refresh. Neither Phoenix nor React have this natively | v0.7.0 |
| **State undo/redo** | Built-in undo stack for event handlers. No equivalent in Phoenix | v0.6.0 |
| **Server functions (RPC)** | Call Python functions from JS, get structured results without re-render. Like tRPC but zero config | v0.7.0 |
| **MCP server** | AI coding assistants can introspect and scaffold djust code via Model Context Protocol | **Done** |
| **~5KB client JS** | Entire client runtime smaller than React's `useState` hook. No build step, no node_modules | **Done** |
| **`dj-copy` clipboard** | Built-in copy-to-clipboard — not available in Phoenix or React without libraries | v0.4.0 |
| **`dj-shortcut` keyboard** | Declarative keyboard shortcuts — Phoenix requires custom JS hooks | v0.4.0 |
| **`dj-page-loading`** | Built-in navigation loading bar — neither Phoenix nor React include this natively | v0.4.0 |
| **Django admin LiveView widgets** | Real-time admin dashboards — no other LiveView framework integrates with an existing admin | v0.7.0 |
| **Streaming initial render** | Chunked HTTP page shell + progressive content — faster perceived load than full-page wait | v0.6.0 |
| **Time-travel debugging** | State snapshot recording + replay in debug panel — beyond Phoenix's debug tools | v0.6.0 |

---

## Investigate & Decide

Open questions that inform future direction:

- **Session/state storage** — Can template context be reconstructed from DB rather than stored in memory/Redis? Can any state move client-side (signed cookies, JWT)? What is typical session size at scale?
- **Debug toolbar completeness** — State size visualization is done (v0.3.7). Remaining: panel state persistence across TurboNav navigation (30s sessionStorage window implemented but edge cases remain).
- **VDOM edge cases** — Investigate remaining edge cases surfaced by proptest fuzzing.
- **Rust-side WASM compilation** — Could the VDOM diffing run client-side via WASM for even faster patches? Tradeoffs: larger JS bundle vs eliminating server round-trip for pure UI changes. Investigate feasibility and performance impact.
- **Django Ninja / DRF interop** — Some teams use djust for UI but need REST/GraphQL APIs alongside. Document recommended patterns; evaluate whether djust views can expose API endpoints without duplication.

---

## Phoenix LiveView Parity Tracker

Features tracked against Phoenix LiveView 1.1 and React where applicable.

| Feature | Phoenix | React Equivalent | djust Status | Milestone |
|---------|---------|-----------------|--------------|-----------|
| Server-side event handling | `handle_event` | Server Actions | **Done** | — |
| Real-time form validation | `phx-change` | `useActionState` | **Done** | — |
| Debounce / throttle | `phx-debounce/throttle` | `useDeferredValue` | **Done** | — |
| Presence tracking | `Phoenix.Presence` | — | **Done** | — |
| PubSub / broadcasting | `Phoenix.PubSub` | — | **Done** | — |
| Streaming collections | `stream/4` | — | **Done** (basic) | — |
| Optimistic UI | `JS` commands | `useOptimistic` | **Done** (`@optimistic`) | — |
| Background async | `start_async` | `useTransition` | **Done** (`start_async`) | — |
| JS hooks (lifecycle) | `phx-hook` | `useEffect` | **Done** (`dj-hook`) | — |
| Live navigation (patch) | `push_patch` | React Router | **Done** (`live_patch`) | — |
| File uploads + progress | `allow_upload` | — | **Done** (`UploadMixin`) | — |
| Drag-and-drop uploads | `phx-drop-target` | — | **Done** (`dj-upload`) | — |
| SSE fallback | — | — | **Done** (`03b-sse.js`) | — |
| `live_session` routing | `live_session/3` | — | **Done** (basic) | — |
| Streaming (LLM/partial) | — | Server Components | **Done** (`StreamingMixin`) | — |
| Dead view → live view | Built-in | SSR hydration | Partial (HTTP fallback) | Post-1.0 |
| **Form `_target` param** | **`_target` in params** | — | **Not started** | **v0.4.0** |
| **Navigation loading bar** | — | NProgress | **Not started** | **v0.4.0** |
| **Static event params** | **`phx-value-*`** | `data-*` attrs | **Not started** | **v0.4.0** |
| **Handle params callback** | **`handle_params/3`** | React Router loaders | **Not started** | **v0.4.0** |
| **JS Commands** | **`JS.*` module** | — | **Not started** | **v0.4.0** |
| **Connection CSS classes** | **`phx-connected`** | — | **Not started** | **v0.4.0** |
| **Form recovery** | **Auto on reconnect** | — | **Not started** | **v0.4.0** |
| **Stable conditional DOM** | **HEEx anchors** | — | **Broken (#559)** | **v0.4.0** |
| **Event ordering** | **Erlang mailbox** | — | **Broken (#560)** | **v0.4.0** |
| **Focus preservation** | **Auto (morph)** | **Reconciliation** | **Not started** | **v0.4.0** |
| **Confirm dialog** | **`data-confirm`** | — | **Not started** | **v0.4.0** |
| **Disable with** | **`phx-disable-with`** | — | **Not started** | **v0.4.0** |
| **Window/doc events** | **`phx-window-*`** | — | **Not started** | **v0.4.0** |
| **Debounce/throttle attrs** | **`phx-debounce`** | — | **Decorator only** | **v0.4.0** |
| **Dynamic page title** | **`live_title`** | `document.title` | **Not started** | **v0.4.0** |
| **Mounted event** | **`phx-mounted`** | `useEffect` | **Not started** | **v0.4.0** |
| **Click-away** | — | `useClickOutside` | **Not started** | **v0.4.0** |
| **Lock (prevent double-fire)** | **Event ack protocol** | — | **Not started** | **v0.4.0** |
| **Auto-recover (custom)** | **`phx-auto-recover`** | — | **Not started** | **v0.4.0** |
| **Cloak (FOUC prevention)** | — | **`v-cloak` (Vue)** | **Not started** | **v0.4.0** |
| **`on_mount` hooks** | **`on_mount/1`** | — | **Not started** | **v0.4.0** |
| **Flash messages** | **`put_flash/3`** | **Toast libraries** | **Not started** | **v0.4.0** |
| Latency simulator | Built-in | — | Not started | v0.4.0 |
| Keyboard shortcuts | — | `react-hotkeys-hook` | **Not started** | **v0.4.0** |
| Copy to clipboard | — | `navigator.clipboard` | **Not started** | **v0.4.0** |
| `assign_async` / `AsyncResult` | `assign_async/3` | `<Suspense>` | **Not started** | **v0.5.0** |
| Component `update` callback | `update/2` | `getDerivedStateFromProps` | Not started | v0.5.0 |
| View Transitions API | — | View Transitions | Not started | v0.5.0 |
| Nested components | `LiveComponent` | Component tree | Not started | v0.5.0 |
| Targeted events (`@myself`) | `phx-target` | — | Not started | v0.5.0 |
| Named slots | `slot/3` macro | `children` / slots | Not started | v0.5.0 |
| Direct-to-S3 uploads | `presign_upload` | — | Not started | v0.5.0 |
| Stream limits + viewport | `:limit`, viewport events | Virtualization | Not started | v0.5.0 |
| `handle_info` | `handle_info/2` | — | Not started | v0.5.0 |
| Template fragments | HEEx static tracking | — | Not started | v0.5.0 |
| Feedback-for (pristine fields) | `phx-feedback-for` | — | Not started | v0.5.0 |
| Nested forms | `inputs_for/4` | Formik nested | Not started | v0.5.0 |
| Scoped loading states | `phx-loading` | Suspense per-query | Not started | v0.5.0 |
| Error boundaries | — | `<ErrorBoundary>` | Not started | v0.5.0 |
| Selective re-rendering | Per-component diff | Reconciliation | Not started | v0.5.0 |
| Computed/derived state | — | `useMemo` | Not started | v0.5.0 |
| Attribute spread (`@rest`) | `{@rest}` | `...props` | Not started | v0.5.0 |
| Lazy component loading | — | `React.lazy()` | Not started | v0.5.0 |
| Component context sharing | — | `useContext` | Not started | v0.5.0 |
| Trigger form action | `phx-trigger-action` | — | Not started | v0.5.0 |
| Animations / transitions | `JS.transition` | `<AnimatePresence>` | Not started | v0.6.0 |
| Transition groups (lists) | — | `<TransitionGroup>` | Not started | v0.6.0 |
| Exit animations | `phx-remove` | `<AnimatePresence>` | Not started | v0.6.0 |
| Streaming initial render | — | `renderToPipeableStream` | Not started | v0.6.0 |
| Time-travel debugging | — | Redux DevTools | Not started | v0.6.0 |
| Sticky LiveViews | `sticky: true` | — | Not started | v0.6.0 |
| DOM mutation events | — | MutationObserver | Not started | v0.6.0 |
| Sticky scroll | — | Chat/log UX | Not started | v0.6.0 |
| CSP nonce | Built-in | — | Not started | v0.6.0 |
| Viewport events | — | `IntersectionObserver` | Not started | v0.6.0 |
| Multi-tab sync | — | BroadcastChannel | Not started | v0.6.0 |
| Offline mutation queue | — | Service Worker | Not started | v0.6.0 |
| Element resize events | — | ResizeObserver | Not started | v0.6.0 |
| State undo/redo | — | `use-undo` | Not started | v0.6.0 |
| Connection multiplexing | Channel multiplexer | — | Not started | v0.6.0 |
| Stale-while-revalidate | — | SWR / React Query | Not started | v0.7.0 |
| `live_session` enhancements | `live_session/3` | — | Basic done | v0.7.0 |
| Push navigate (SPA nav) | `push_navigate` | — | Not started | v0.7.0 |
| Portal rendering | — | `createPortal` | Not started | v0.7.0 |
| Back/forward restoration | `push_patch` state | Loader cache | Not started | v0.7.0 |
| Server-only components | — | Server Components | Not started | v0.7.0 |
| Islands of interactivity | — | Astro islands | Not started | v0.7.0 |
| AI streaming primitives | — | — | Not started | v0.7.0 |
| Server functions (RPC) | — | Server Actions | Not started | v0.7.0 |
| Django admin LiveView widgets | — | — | Not started | v0.7.0 |
| Prefetch on hover/intent | — | Remix prefetch | Not started | v0.7.0 |
| Dynamic form fields | `sort_param`/`drop_param` | — | Not started | Post-1.0 |

---

## Priority Matrix

| Milestone | Theme | Key Deliverables | Priority |
|-----------|-------|-----------------|----------|
| v0.4.0-beta | Stability & DX | Fix #559/#560, **JS Commands**, focus preservation, connection CSS, form recovery, `dj-confirm`, `dj-disable-with`, `dj-lock`, `dj-auto-recover`, window events, `dj-debounce`/`dj-throttle` attrs, `live_title`, `dj-mounted`, `dj-click-away`, `dj-cloak`, **`on_mount` hooks**, **flash messages**, **`dj-value-*`**, **`handle_params`**, **`dj-shortcut`**, **`dj-copy`**, **`_target` param**, **`dj-page-loading`**, error messages, `dj-key`, `djust_doctor`, latency simulator | **Critical** |
| v0.5.0 | Components, Async & Streams | **`assign_async`/`AsyncResult`**, nested LiveComponents + targeted events + slots, **component `update` callback**, `dj-spread`/attribute rest, **`dj-lazy`**, **component context sharing**, **`dj-trigger-action`**, **View Transitions API**, direct-to-S3 uploads, stream enhancements, **`handle_info`**, **template fragments**, **`dj-feedback`**, **nested forms**, **scoped loading**, error boundaries, selective re-rendering, `@computed`, Rust engine parity | **Critical** |
| v0.6.0 | Production & Interactivity | Animations/transitions + **`dj-transition-group`**, **streaming initial render**, **time-travel debugging**, **state undo/redo**, **connection multiplexing**, sticky LiveViews, `dj-mutation`, `dj-sticky-scroll`, monitoring, graceful degradation, CSP nonce, batch state updates, multi-tab sync, offline mutation queue, `dj-resize` | **High** |
| v0.7.0 | Navigation, AI & Smart Rendering | **stale-while-revalidate**, **AI streaming primitives**, **server functions (RPC)**, **Django admin LiveView widgets**, **prefetch on hover/intent**, `live_session` enhancements, push navigate, portal rendering, back/forward restoration, server-only components, islands of interactivity | **High** |
| v1.0.0 | Stable Release | DevTools extension, docs site, benchmarks, plugin system, starters, VS Code extension, TypeScript hook definitions | **High** |
| Post-1.0 | Ecosystem | Dead views, framework portability, CRDT collab, AI generation, binary protocol, dynamic form fields | **Medium** |

---

## Contributing

Want to help? See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

High-impact areas for contributions:

#### Quick Wins (< 1 day, great first contributions)
1. **`dj-value-*` static params** — ~20 lines JS, used in every template
2. **`dj-confirm`** — Browser confirm dialog before events, ~15 lines JS
3. **`dj-disable-with`** — Auto-disable buttons during submission, ~20 lines JS
4. **`dj-copy`** — Copy to clipboard, ~30 lines JS
5. **`dj-cloak`** — FOUC prevention, ~5 lines JS + 1 line CSS
6. **Connection state CSS classes** — `dj-connected`/`dj-disconnected` on body, ~10 lines JS
7. **`live_title`** — Dynamic page title via WS message, ~30 lines total
8. **`dj-click-away`** — Click outside handler, ~20 lines JS
9. **`dj-lock`** — Prevent concurrent event execution, ~30 lines JS
10. **`_target` param in change events** — ~10 lines JS + ~5 lines Python, essential for forms
11. **`dj-page-loading`** — NProgress-style loading bar, ~40 lines JS + 10 lines CSS

#### Medium Effort (1-3 days)
12. **`dj-shortcut`** — Keyboard shortcut binding, ~60 lines JS
13. **`dj-debounce`/`dj-throttle` HTML attributes** — Client-side timer per element, ~50 lines JS
14. **`on_mount` hooks** — Cross-cutting mount logic, ~100 lines Python
15. **Flash messages** — `FlashMixin` + `{% dj_flash %}` + client JS auto-dismiss
16. **`handle_params` callback** — URL param change handler, ~50 lines Python
17. **`dj-mounted`** — Element entered DOM event, ~30 lines JS
18. **`dj-sticky-scroll`** — Auto-scroll chat/log containers, ~40 lines JS
19. **`dj-lazy` viewport loading** — Lazy component rendering, ~40 lines JS
20. **Multi-tab sync** — BroadcastChannel API integration, ~60 lines JS
21. **View Transitions API** — Animated page transitions, ~60 lines JS

#### Major Features
22. **JS Commands** — Biggest DX win; needs Python builder + client JS executor
23. **VDOM structural patching** (#559) — Rust experience helpful
24. **`assign_async`/`AsyncResult`** — High-level async data loading, ~200 lines Python
25. **Template fragments** — Rust-side static subtree fingerprinting for wire-size optimization
26. **Connection multiplexing** — Share one WS across multiple LiveViews, ~200 lines JS + Python
27. **Rust template engine parity** — Close the model attribute access gap
28. **AI streaming primitives** — Purpose-built LLM streaming components
29. **Streaming initial render** — Chunked HTTP response with progressive content loading
30. **Django admin LiveView widgets** — Real-time admin dashboards and inline editing

#### Always Welcome
31. **Starter templates** — Build example apps that showcase djust patterns
32. **Documentation** — Improve guides, fix gaps, add cookbook recipes
33. **Test coverage** — Edge cases in VDOM diffing, WebSocket reconnection, state backends

Open an issue or discussion to propose features or ask questions.
