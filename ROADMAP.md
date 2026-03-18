ca# djust Roadmap

> Current version: **0.3.8rc1** (Alpha) — Last updated: March 18, 2026 (roadmap refresh: Phoenix LV 1.1 parity audit, React 19.2 patterns, v0.5.0 scope split into v0.5.0/v0.5.1, new features: `handle_async`, declarative assigns, `used_input?`, programmable JS hook commands, server actions, keep-alive/activity, keyed for-loop change tracking, type-safe template validation, streaming markdown renderer, `self.defer()`, document metadata, temporary assigns, `djust_gen_live` scaffolding, transition/priority updates, suspense boundaries, named slots with attributes, static asset tracking, database change notifications, WebSocket compression, `dj-scroll-into-view`, multi-step wizard, virtual/windowed lists, `dj-paste`, runtime layout switching, i18n live switching)

This roadmap outlines what has been built, what is actively being worked on, and where djust is headed. Priorities are shaped by real-world usage across [djust.org](https://djust.org) and [djustlive](https://djustlive.com), and by feature parity goals with Phoenix LiveView 1.0 and React 19-level interactivity.

### Priority Matrix — What Moves the Needle Most

| Priority | Feature | Why | Milestone |
|----------|---------|-----|-----------|
| ~~**P0**~~ | ~~VDOM structural patching (#559)~~ ✅ | ~~Blocks all conditional rendering — every new user hits this~~ | v0.4.0 |
| ~~**P0**~~ | ~~Focus preservation across re-renders~~ ✅ | ~~Forms feel broken without it — table-stakes for any interactive framework~~ | v0.4.0 |
| ~~**P0**~~ | ~~Event sequencing (#560)~~ ✅ | ~~User events silently dropped during ticks — trust-destroying~~ | v0.4.0 |
| ~~**P0**~~ | ~~`dj-value-*` static event params~~ ✅ | ~~Most underrated Phoenix feature; used on virtually every event binding~~ | v0.4.0 |
| ~~**P0**~~ | ~~`handle_params` callback (complete)~~ ✅ | ~~`live_patch` is half-implemented without it — partial impl exists, needs finish~~ | v0.4.0 |
| **P1** | JS Commands (`dj.push`, `dj.show`, etc.) | Biggest DX gap vs Phoenix; eliminates server round-trip for UI interactions | v0.4.1 |
| **P1** | Flash messages (`put_flash`) | Every app reinvents this; 40 lines to eliminate universal boilerplate | v0.4.0 |
| **P1** | `on_mount` hooks | Cross-cutting auth/telemetry without copy-pasting into every mount() | v0.4.0 |
| **P1** | Function Components (stateless) | Cheap render-only components without WS overhead — Phoenix.Component parity | v0.5.0 |
| **P1** | `assign_async` / AsyncResult | Foundation for responsive dashboards — independent loading boundaries | v0.5.0 |
| **P1** | Template fragments (static subtree) | Biggest wire-size optimization; how Phoenix achieves sub-ms updates | v0.5.0 |
| **P1** | LiveView testing utilities | `assert_push_event()`, `assert_patch()`, `render_async()` — test DX is adoption-critical | v0.5.0 |
| **P1** | Keyed for-loop change tracking | O(changed) not O(total) for list re-renders — foundation for large-list performance | v0.5.0 |
| **P1** | Temporary assigns | Phoenix's #1 memory optimization — without it, large lists (chat, feeds) leak memory unboundedly | v0.5.0 |
| **P1** | `manage.py djust_gen_live` scaffolding | Phoenix's generators are the #1 onboarding DX feature; scaffold views/templates/tests from a model | v0.4.0 |
| **P1** | Transition/priority updates | React 18/19 `startTransition` concept — mark re-renders as low-priority so user events always win | v0.4.0 |
| **P1** | Suspense boundaries (`{% dj_suspense %}`) | Template-level loading boundaries wrapping `assign_async` — React Suspense parity | v0.5.0 |
| **P2** | Named slots with attributes | Phoenix's `<:slot>` with slot attrs — foundation for composable component libraries | v0.5.0 |
| **P2** | Server Actions (`@action` decorator) | React 19 parity; standardized pending/error/success for mutations | v0.8.0 |
| **P2** | Async Streams | Phoenix 1.0 parity; infinite scroll and real-time feeds at scale | v0.8.0 |
| **P2** | Connection multiplexing | Pages with 5+ live sections need this to not waste connections | v0.6.0 |
| **P2** | Dead View / Progressive Enhancement | 1.0 requirement for government/accessibility projects | v1.0.0 |
| **P2** | Accessibility (ARIA/WCAG) | 1.0 requirement; Phoenix was criticized for shipping without this | v1.0.0 |
| **P2** | Type-safe template validation | Catch template variable typos at CI — unique differentiator vs all competitors | v0.5.1 |
| **P2** | Keep-Alive / `dj-activity` | Pre-render hidden routes, preserve state — React 19.2 parity | v0.7.0 |
| **P2** | Streaming markdown renderer | Incremental markdown for LLM output — strongest AI vertical signal | v0.7.0 |
| **P1** | Database change notifications (pg_notify) | PostgreSQL LISTEN/NOTIFY → LiveView push — killer feature for reactive dashboards | v0.5.0 |
| **P1** | Virtual/windowed lists (`dj-virtual`) | DOM virtualization for 100K+ rows at 60fps — mandatory for data-heavy apps | v0.5.0 |
| **P2** | Multi-step wizard (`WizardMixin`) | #2 most common UI pattern after CRUD — no framework has this natively | v0.5.1 |
| **P2** | Error overlay (dev mode) | In-browser error display like Next.js/Vite — faster debugging loop | v0.5.1 |
| **P2** | WebSocket compression | `permessage-deflate` for 60-80% bandwidth reduction — cheapest optimization available | v0.6.0 |
| **P2** | Static asset tracking (`dj-track-static`) | Detect stale JS/CSS on reconnect, prompt reload — Phoenix `phx-track-static` parity | v0.6.0 |
| **P3** | View Transitions API | Cheapest way to make navigation feel native | v0.5.0 |
| **P3** | Islands of interactivity | Content-heavy sites with small interactive zones | v0.7.0 |
| **P3** | Offline mutation queue | Mobile/spotty-connection differentiator | v0.6.0 |
| **P3** | Native `<dialog>` integration | Browser-native modals with better a11y than custom implementations | v0.5.0 |

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
- Keyed VDOM diff with LIS optimization (`dj-key` / `data-key`), proptest/fuzzing coverage
- `dj-confirm` attribute — browser confirmation dialog before event execution
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

### Milestone: v0.4.0 — Stability & Core DX (Scope Trimmed)

**Goal**: Make djust reliable enough that developers don't hit surprising breakage in normal use. Fix the sharp edges that make new users bounce. *Scope intentionally trimmed from the previous 28-feature v0.4.0 — ship the must-haves, then iterate. JS Commands moved to v0.4.1.*

#### Critical Bug Fixes

**VDOM structural patching** (#559) ✅ — Fixed in PR #563. Comment node placeholders (`<!--dj-if-->`) are now included in client-side child index resolution (`getSignificantChildren`, `getNodeByPath`, `createNodeFromVNode`), matching the Rust VDOM's index computation. Conditional `{% if %}` blocks no longer break surrounding element patches.

**Event sequencing during ticks** (#560) ✅ — Fixed in PR #566. Render lock serializes tick/event operations; ticks yield to user events; client buffers tick patches during pending event round-trips; monotonic event ref for request/response matching.

**Focus preservation across re-renders** ✅ PR #564 (2026-03-18) — When the VDOM patches the DOM, focused elements lose focus and cursor position. This makes typing in forms feel broken when other parts of the page update. Fix: capture `document.activeElement`, selection range, and scroll position before patching; restore after. *Phoenix preserves focus automatically via `phx-update="ignore"` and morph internals; React preserves it via reconciliation. This is table-stakes for feeling like a real app.*

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

**`dj-confirm` attribute** — ✅ Already implemented in `09-event-binding.js`. Native browser confirmation dialog before executing an event.

**`dj-disable-with` attribute** — Automatically disable submit buttons and replace text during form submission. `<button type="submit" dj-disable-with="Saving...">Save</button>`. Prevents double-submit and provides instant visual feedback. Phoenix's `phx-disable-with` is one of its most-loved small features.

**`dj-key` attribute** — ✅ Already implemented. Keyed VDOM diff with LIS optimization.

**Window/document event scoping** — `dj-window-keydown`, `dj-window-scroll`, `dj-document-click` attributes for binding events to `window` or `document` rather than the element itself. Phoenix has `phx-window-*`. Essential for keyboard shortcuts, infinite scroll triggers, and click-outside-to-close patterns.

**`dj-debounce` / `dj-throttle` as HTML attributes** — Currently debounce/throttle only works as Python decorators on event handlers, applying the same delay to every caller. Phoenix allows per-element control: `<input dj-change="search" dj-debounce="300">` vs `<select dj-change="filter" dj-debounce="0">`. This is strictly more flexible — the Python decorator becomes the default, the attribute becomes the override. Implementation: client-side timer per element+event pair, ~50 lines of JS.

**`live_title` & document metadata** — Update `<title>` and `<meta>` tags from the server without a page reload. Phoenix's `live_title_tag` is trivial but surprisingly impactful — it enables unread counts, status indicators, and notification badges in browser tabs. React 19 went further with native document metadata support (title, link, meta hoisted to `<head>` automatically). API: `self.page_title = "Chat (3 unread)"` and `self.page_meta = {"description": "...", "og:image": "..."}` in any event handler, sent as a lightweight WS message that updates `document.title` and `<meta>` tags without a VDOM diff. The meta tag support is especially valuable for SPAs that need dynamic Open Graph tags for link previews. ~50 lines total.

**`dj-mounted` event** — Fire a server event when an element enters the DOM (after VDOM patch inserts it). Use cases: scroll-into-view for new chat messages, trigger data loading when a tab becomes active, animate elements on appearance. Phoenix has `phx-mounted`. Pairs naturally with `dj-remove` (exit event). Implementation: MutationObserver watching for elements with `dj-mounted` attribute.

**`dj-click-away`** — Fire an event when the user clicks outside an element. `<div dj-click-away="close_dropdown">`. This is the single most common pattern developers manually implement in every interactive app (dropdowns, modals, popovers). Currently requires `dj-window-click` + manual coordinate checking or a JS hook. One attribute, ~20 lines of JS, eliminates boilerplate in every project.

**`dj-lock` — Prevent concurrent event execution** — Disable an element until its event handler completes. `<button dj-click="save" dj-lock>Save</button>` prevents double-clicks and concurrent submissions. Different from `dj-disable-with` (which is cosmetic) — `dj-lock` actually blocks the event from firing again until the server acknowledges completion. Phoenix handles this implicitly via its event acknowledgment protocol. Implementation: client-side `disabled` flag per element, cleared on server response. ~30 lines of JS. Pairs with `dj-disable-with` for the full pattern: lock + visual feedback.

**`dj-auto-recover` — Custom reconnection recovery** — Fires a custom server event on WebSocket reconnect instead of the default form-value replay. `<div dj-auto-recover="restore_state">`. Use case: views with complex state (drag positions, canvas state, multi-step wizard progress) that can't be recovered from form values alone. The handler receives `params` with whatever the client can serialize from the DOM. Phoenix's `phx-auto-recover` solves the same problem — not every reconnection fits the "replay form values" pattern.

**`dj-value-*` — Static event parameters** — Pass static values alongside events without `data-*` attributes or hidden inputs. `<button dj-click="delete" dj-value-id="{{ item.id }}" dj-value-type="soft">Delete</button>` sends `{"id": "42", "type": "soft"}` as params. Phoenix's `phx-value-*` is used everywhere — it's the standard way to pass context with events. Currently djust requires either `data-*` attributes (which the client must extract) or hidden form fields. This is ~20 lines of JS (collect `dj-value-*` attributes on the trigger element and merge into event params) but eliminates boilerplate in every template. *This is arguably the single most underrated Phoenix feature — once developers have it, they use it on every event.*

**`handle_params` callback** ✅ PR #567 (2026-03-18) — Invoked when URL parameters change via `live_patch` or browser navigation. Phoenix's `handle_params/3` is the standard pattern for URL-driven state (pagination, filters, search, tab selection). Currently, `live_patch` updates the URL but there's no server-side callback to react to the change — developers must manually parse `request.GET` in event handlers. API: `def handle_params(self, params, url, **kwargs)` called after `mount()` on initial render and on every subsequent URL change. This enables bookmark-friendly state: users can share URLs like `/dashboard?tab=metrics&range=7d` and the view reconstructs itself from params. ~50 lines Python. *Without this, `live_patch` is only half-implemented — you can push URLs but can't react to them.*

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

**`dj-scroll-into-view` — Auto-scroll to element on render** — Declaratively scroll an element into the viewport when it appears or updates. `<div dj-scroll-into-view>` on a new chat message scrolls it visible. Supports modifiers: `dj-scroll-into-view.smooth` (smooth scroll), `dj-scroll-into-view.instant` (jump), `dj-scroll-into-view.nearest` (minimal scroll). Fires after VDOM patch, so it targets the newly-inserted element. Use cases: chat messages, form validation errors ("scroll to first error"), anchor navigation within LiveViews, notification toasts. Currently requires a `dj-hook` with `this.el.scrollIntoView()` — this is boilerplate in every app that does list appending. ~25 lines JS (MutationObserver + `scrollIntoView()`). *This pairs with `dj-sticky-scroll` (container-level) but operates at the element level — both are needed for complete scroll UX.*

**`dj-page-loading` — Navigation loading bar** — A thin animated progress bar at the top of the viewport during `live_redirect`, `live_patch`, and TurboNav navigation. `<div dj-page-loading class="my-loading-bar">`. Auto-shows when navigation starts, auto-hides on completion. Configurable appearance via CSS (color, height, animation). YouTube, GitHub, and every modern SPA use this pattern (NProgress). Currently djust navigations have no visual feedback — the page appears to freeze. ~40 lines JS + 10 lines CSS. *This is the single cheapest way to make navigation feel fast — a visible progress indicator makes 200ms feel instant while no indicator makes 100ms feel broken. Neither Phoenix nor React include this natively.*

**Flash messages (promoted from v0.5.0)** — Built-in ephemeral notification pattern with `self.put_flash(level, message)` and auto-dismissing client-side rendering. Phoenix's `put_flash` is used in virtually every app. *Promoted to v0.4.0 because this is the #1 pattern developers reinvent in every project. A `FlashMixin` with `put_flash('info', 'Saved!')`, a `{% dj_flash %}` template tag, and ~40 lines of client JS for appear/auto-dismiss animations. Flash messages survive `live_patch` but clear on `live_redirect`. Without this, every djust app ships with a slightly different homegrown toast system.*

#### Transition / Priority Updates (React 18/19 `startTransition` concept)

**Priority-aware event queue** — React 18 introduced `startTransition` to mark state updates as non-urgent so they don't block user interaction. djust has the same fundamental problem (#560): server-initiated ticks (polling, broadcast re-renders) collide with user events, silently dropping input. The fix isn't just event sequencing — it's *priority*. User-initiated events (`dj-click`, `dj-submit`, `dj-change`) should always preempt server-initiated updates (ticks, broadcasts, `handle_info` re-renders). Implementation: tag every event with a priority level (`user` vs `background`). The WebSocket consumer processes the queue priority-first — if a user event arrives while a background re-render is in-flight, the background render is discarded and the user event processes instead. On the client, if a server push arrives while a user event is pending acknowledgment, buffer the server push and apply it after the user event round-trip completes. This architecturally prevents #560 instead of just patching the symptom. ~100 lines Python + ~40 lines JS. *React learned this lesson the hard way — concurrent rendering exists because blocking user input on background work destroys perceived performance. djust should learn from React's insight rather than repeating the mistake.*

#### Scaffolding

**`manage.py djust_gen_live` — Model-to-LiveView scaffolding** — Phoenix's `mix phx.gen.live` is the #1 onboarding accelerator: give it a model and it generates a LiveView, templates, and tests for CRUD operations in seconds. djust has the MCP server for AI-assisted scaffolding, but a CLI command is essential for developers who aren't using AI tools. `manage.py djust_gen_live posts Post title:string body:text published:boolean` generates: (1) a LiveView class with `mount()`, `handle_event()` for create/edit/delete, (2) index/show/form templates with `dj-model` bindings and `dj-submit`, (3) URL patterns via `live_session()`, (4) test file with `LiveViewTestClient` smoke tests. Respects the project's existing patterns (detects whether the project uses function-based or class-based views, which CSS framework, etc.). Optional `--no-tests`, `--api` (JSON responses), `--belongs-to=User` flags. ~400 lines Python management command + Jinja2 templates. *Every framework with fast adoption has a generator: Rails scaffold, Phoenix gen.live, Laravel make:livewire. This is how new developers go from "installed" to "productive" in under 5 minutes. The MCP server is great for AI-assisted dev, but the CLI command is the universal onramp.*

#### Developer Tooling

**Error message quality** — Replace silent HTML comments (`<!-- djust: unsupported tag -->`) with visible warnings in DEBUG mode. Surface Rust template engine fallback reasons in the debug panel and server logs. Improve VDOM path error messages to show which element failed and suggest fixes.

**`manage.py djust_doctor`** — Single diagnostic command that verifies: Rust extension loaded, Channels configured, Redis reachable (if configured), template compatibility scan, Python/Django version support.

**Latency simulator** — Dev-only tool (in debug panel) to add artificial latency to WebSocket messages. Essential for testing loading states, optimistic updates, and transitions under real-world conditions. Phoenix includes this built-in.

**Profile & improve performance** — Use existing benchmarks in `tests/benchmarks/` as baselines. Profile the full request path: HTTP render, WebSocket mount, event, VDOM diff, patch. Target: <2ms per patch, <5ms for list updates.

#### Reconnection Resilience

**Form recovery on reconnect** — When WebSocket reconnects after a disconnect, the client should auto-fire `dj-change` with current DOM form values to restore server state. Phoenix does this automatically — users type into a form, lose connection briefly, reconnect, and nothing is lost. Currently djust loses all form state on reconnect.

**Reconnection backoff with jitter** — Exponential backoff with random jitter on WebSocket reconnection to prevent thundering herd after a server restart. Display reconnection attempt count in the connection status UI.

### Milestone: v0.4.1 — JS Commands & Interaction Polish

**Goal**: Close the biggest DX gap vs Phoenix (JS Commands) and ship the remaining quick wins that didn't fit in v0.4.0's bug-fix focus.

**JS Commands (`dj.push`, `dj.show`, `dj.hide`, `dj.toggle`, `dj.addClass`, `dj.removeClass`, `dj.transition`, `dj.dispatch`, `dj.focus`, `dj.set_attr`, `dj.remove_attr`)** — Moved from v0.4.0 to give the core bug fixes room to ship. This is still the single biggest DX gap vs Phoenix LiveView. See v0.4.0 section for full design notes. *Ship this as a fast follow — ideally within 2-3 weeks of v0.4.0.*

**Programmable JS Commands from hooks (Phoenix 1.0 parity)** — Expose the JS Command API (`dj.show`, `dj.hide`, `dj.addClass`, `dj.transition`, etc.) to `dj-hook` lifecycle callbacks, so custom hooks can programmatically trigger the same commands available in templates. Phoenix 1.0 exposed this and it's essential for integrating third-party JS libraries that need to coordinate with LiveView's DOM patching. API: `this.js().show('#modal').addClass('active', '#overlay').exec()` inside any hook method. The commands integrate with server DOM patching so classes/visibility set by hooks persist across re-renders. ~60 lines JS.

**`to: {:inner, selector}` and `to: {:closest, selector}` JS Command targets (Phoenix 1.0 parity)** — JS Commands currently target elements by CSS selector. Phoenix 1.0 added `:inner` (select within the triggering element's children) and `:closest` (select up the DOM tree from the trigger). These scoped selectors are essential for component-scoped UI: a "close" button inside a modal can target `{:closest, '.modal'}` without needing a unique ID. API: `DJ.hide(closest='.modal')` / `DJ.show(inner='.content')`. ~30 lines JS.

**`page_loading` option on `dj.push` (Phoenix 1.0 parity)** — Phoenix 1.0 recommends `JS.push("event", page_loading: true)` to trigger the page loading indicator during event processing. This bridges the gap between navigation-level loading bars (`dj-page-loading`) and per-event scoped loading — when an event triggers a heavy operation that affects the whole page, show the top loading bar. API: `dj.push('generate_report', {page_loading: true})` triggers `dj-page-loading` elements during the event round-trip. ~15 lines JS.

**`dj-paste` — Paste event handling** — Fire a server event when the user pastes content (text, images, files) into an element. `<textarea dj-paste="handle_paste">`. The client extracts paste payload: plain text via `clipboardData.getData('text/plain')`, images via `clipboardData.files` (auto-routed to `UploadMixin` if an upload slot is configured), and rich HTML via `getData('text/html')`. Sends structured params: `{"text": "...", "html": "...", "has_files": true}`. Use cases: paste images into chat (Slack/Discord-style), paste formatted text into rich editors, paste CSV data into tables, paste code snippets with language detection. Currently requires a `dj-hook` for every paste target. ~40 lines JS. *Every chat app and content editor needs paste handling. Combined with `UploadMixin` for image paste, this is the complete clipboard-to-server pipeline.*

**Remaining v0.4.0 quick wins** — Any items from the v0.4.0 quick wins list that didn't ship in the initial release (`dj-shortcut`, `dj-copy`, `dj-page-loading`, `dj-click-away`, `dj-lock`, `dj-auto-recover`, `dj-cloak`, `dj-mounted`, `live_title`, `dj-scroll-into-view`) ship here.

### Milestone: v0.5.0 — Async Loading, Core Components & Streams

**Goal**: Ship the async data loading and core component primitives that production apps need. Scope intentionally trimmed — DX features (testing, error overlay, computed state) moved to v0.5.1.

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

**Temporary assigns** — Phoenix's most critical memory optimization, completely absent from djust today. `temporary_assigns` resets specified attributes to a default value *after every render*, so the server doesn't hold large collections in memory between events. Without this, a chat app with 10,000 messages keeps all 10,000 in server memory for every connected user — even though only the last 50 are visible. With temporary assigns, the server renders the full list once, sends the diff, then resets `self.messages = []` — the client already has the DOM, the server doesn't need the data anymore. New messages append via streams. API: `temporary_assigns = {'messages': [], 'search_results': []}` class attribute, or `self.temporary_assign('messages', [])` in `mount()`. The render pipeline checks `temporary_assigns` after each render cycle and resets the values. ~60 lines Python. *This is not optional for production apps with large lists. Phoenix has had this since 0.4.0 (2019) and it's used in virtually every app that displays collections. Without it, djust apps will hit memory limits at modest scale. A chat room with 100 concurrent users × 10,000 messages × ~1KB per message = ~1GB of memory just for message state. With temporary assigns: ~0. This is the single highest-ROI feature for production readiness.*

```python
# Target API
class ChatView(LiveView):
    temporary_assigns = {'messages': []}

    def mount(self, request, **kwargs):
        self.messages = Message.objects.order_by('-created')[:50]
        # After first render, self.messages resets to []
        # Client already has the DOM — server doesn't need the data

    def handle_info(self, message):
        if message['type'] == 'new_message':
            self.messages = [message['data']]  # Append one, reset after render
```

**Suspense boundaries (`{% dj_suspense %}`)** — Template-level loading boundaries that wrap sections dependent on `assign_async` data. When the async data is loading, the suspense boundary renders a fallback (skeleton, spinner, or custom template). When data arrives, the boundary swaps to the real content with an optional transition. React's `<Suspense>` transformed how developers think about loading states — instead of `{% if data.loading %}` conditionals scattered through templates, you wrap sections declaratively. API: `{% dj_suspense fallback="skeleton.html" %}{{ metrics }}{% enddj_suspense %}` or inline: `{% dj_suspense %}<div class="skeleton h-20">{% enddj_suspense %}...{% enddj_suspense_content %}{{ metrics }}{% enddj_suspense_content %}`. Multiple suspense boundaries on one page load independently — a slow query in one section doesn't block the others. Nested suspense boundaries cascade (inner resolves independently of outer). Implementation: the Rust template engine emits placeholder markers for unresolved `AsyncResult` values; the client swaps them when the server pushes resolved data. ~80 lines Python + ~40 lines JS + Rust template tag. *This is the declarative counterpart to `assign_async` — without it, every async section needs manual `{% if x.loading %}` / `{% if x.ok %}` conditionals, which is verbose and error-prone. React proved that Suspense boundaries are the right abstraction for async rendering.*

```html
<!-- Target API in templates -->
<div class="dashboard">
  {% dj_suspense fallback="components/metric_skeleton.html" %}
    <div class="metric-card">{{ metrics.total_users }}</div>
  {% enddj_suspense %}

  {% dj_suspense fallback="components/chart_skeleton.html" %}
    <canvas dj-hook="Chart" data-values="{{ chart_data }}"></canvas>
  {% enddj_suspense %}
  <!-- Each section loads independently — fast data shows instantly -->
</div>
```

**Named slots with attributes (Phoenix `<:slot>` parity)** — Phoenix's slot system lets parent templates pass named content blocks *with attributes* into components. This is strictly more powerful than Django's `{% block %}` (which has no attributes) or basic `children` passing. Named slots enable composable patterns like tables where the parent defines columns with headers and cell renderers. API: `{% slot header label="Name" sortable=True %}{{ item.name }}{% endslot %}` in the parent, `{% render_slot header %}` in the component template with access to slot attributes via `{{ slot.label }}`. Multiple slots of the same name create a list (essential for table columns, tab panels, accordion sections). Implementation: slots are collected during template parsing and passed as structured data to the component's context. ~120 lines Python + Rust template support. *This is the missing piece for building real component libraries. Without named slots with attributes, components can't express patterns like "here are my columns, each with a header label and a cell renderer" — which is the foundation of every table, tab, and accordion component. Phoenix's slot system is what made HEEx components genuinely composable.*

```python
# Parent template usage:
# {% component "data_table" rows=users %}
#   {% slot col label="Name" sortable=True %}{{ row.name }}{% endslot %}
#   {% slot col label="Email" %}{{ row.email }}{% endslot %}
#   {% slot col label="Role" %}{{ row.get_role_display }}{% endslot %}
#   {% slot empty %}No users found.{% endslot %}
# {% endcomponent %}

# Component template (data_table.html):
# <table>
#   <thead><tr>
#     {% for col in slots.col %}
#       <th {% if col.attrs.sortable %}dj-click="sort" dj-value-field="{{ col.attrs.label }}"{% endif %}>
#         {{ col.attrs.label }}
#       </th>
#     {% endfor %}
#   </tr></thead>
#   <tbody>
#     {% for row in rows %}
#       <tr>{% for col in slots.col %}<td>{% render_slot col %}</td>{% endfor %}</tr>
#     {% empty %}
#       <tr><td colspan="{{ slots.col|length }}">{% render_slot slots.empty.0 %}</td></tr>
#     {% endfor %}
#   </tbody>
# </table>
```

**Component `update` callback** — Phoenix's `update/2` on LiveComponents lets you transform assigns before render — essential for components that need to derive internal state from parent-provided props. Without this, components must put derivation logic in `render()` or `get_context_data()`, mixing state transformation with presentation. API: `def update(self, assigns)` called before every render when parent assigns change. The component can transform, validate, or ignore incoming assigns. ~40 lines Python. *This is the key to building reusable component libraries — components need to control how external data maps to internal state. React's `getDerivedStateFromProps` / `useMemo` + `useEffect` serve the same purpose.*

**View Transitions API integration (promoted from v0.6.0)** — Use the browser's native View Transitions API for animated page transitions during `live_redirect` and TurboNav navigation. `<main dj-transition="slide-left">` applies a named view transition when the content changes. Falls back gracefully in unsupported browsers. Low implementation effort (~60 lines JS), supported in Chrome, Edge, Safari, and Firefox (implementing). *Promoted because this is the single biggest perceived-quality improvement available — animated transitions make server-rendered apps feel like native apps. No other server-side framework has first-class View Transitions API support yet. Combined with `dj-page-loading`, navigation goes from "feels like 2010 Django" to "feels like a native app" — a critical perception for adoption.*

**Nested LiveComponents with targeted events** — LiveComponents within LiveComponents with event bubbling through the component tree. Each component maintains its own VDOM tree for independent diffing. Events target their owning component via a `dj-target="component_id"` attribute (Phoenix's `@myself`). Named slots for composition (`<slot:header>`, `<slot:footer>`). Declarative assigns with validation. This is the foundation for building complex UIs from reusable pieces.

**Direct-to-S3 uploads** — The core upload system (chunked binary WS frames, drag-and-drop, progress, validation) is complete. Add optional direct-to-S3/GCS with pre-signed URLs via `presign_upload()` callback, bypassing the server for large files. Django `UploadedFile` compatibility so existing model `FileField`/`ImageField` patterns work unchanged with the pre-signed flow.

**Stream enhancements** — The existing `StreamsMixin` handles basic append/replace, but needs parity with Phoenix streams: `:limit` option to cap client-side DOM elements (enables virtual scrolling with minimal memory), `dj-viewport-top` / `dj-viewport-bottom` events that fire when the first/last stream child enters the viewport (enables bidirectional infinite scroll), and `stream_configure()` for per-stream options. Combined, these let you build infinite-scroll feeds, chat histories, and large data tables without keeping items in server memory.

**`handle_async` callback (Phoenix 1.0 parity)** — When `start_async()` or `assign_async()` completes, invoke `handle_async(name, result)` with `result` being either `(ok=value)` or `(error=exception)`. Phoenix 1.0's `handle_async/3` lets developers transform async results before they hit the template — essential for error mapping, data normalization, and retry logic. Currently djust's `start_async` either auto-assigns the result or calls a raw callback; there's no standardized completion handler with typed success/failure. API: `def handle_async(self, name, result)` — receives the task name and an `AsyncResult` with `.ok`, `.failed`, `.loading` states. If a task with the same name is started while one is in-flight, the previous result is discarded (last-write-wins). ~80 lines Python. *This is the missing counterpart to `assign_async` — without it, developers can't cleanly handle errors or transform results from async operations.*

```python
# Target API
class DashboardView(LiveView):
    def mount(self, request, **kwargs):
        self.start_async('metrics', self._load_metrics)

    def handle_async(self, name, result):
        if name == 'metrics':
            if result.ok:
                self.metrics = result.value
            elif result.failed:
                self.metrics_error = str(result.error)
                self.put_flash('error', 'Failed to load metrics')

    async def _load_metrics(self):
        return await expensive_query()
```

**`handle_info` pattern (promoted from v0.6.0)** — Explicit handler for external messages (Celery task completion, webhook notifications, admin broadcasts) that arrive via Channels layer but aren't user-initiated events. Phoenix's `handle_info/2` is the standard pattern for reacting to external signals. *Promoted because any production app with background tasks (Celery, webhooks) needs this immediately. Currently djust handles this implicitly through broadcasting, but a dedicated `handle_info()` method with typed message dispatching is cleaner and more discoverable.* API: `self.subscribe('topic')` in mount, `def handle_info(self, message)` with `match` dispatch.

**Keyed for-loop change tracking (Phoenix 1.1 parity)** — Phoenix LiveView 1.1 performs automatic change tracking in comprehensions — when items in a `for` loop have keys, only changed items are re-rendered and diffed. Without keys, it falls back to index-based tracking (still better than re-rendering everything). djust's `dj-key` attribute already enables keyed VDOM diffing on the *client*, but the *server* still re-renders the entire `{% for %}` loop body for every item on every event. Implementation: the Rust template engine tracks which loop items' context changed between renders and only emits VDOM nodes for changed items. Unchanged items are sent as a stable fingerprint reference. Combined with template fragments, this is the key to O(changed) rendering instead of O(total) — essential for lists with 100+ items. ~200 lines Rust. *This is how Phoenix achieves sub-millisecond updates on large lists. Without it, a 500-item list re-renders 500 items even when only 1 changed.*

**`self.defer(callback)` — Post-render server-side work** — Schedule a callback to run *after* the current render has been sent to the client. Use cases: sending notifications after a successful save, updating analytics counters, cleaning up temporary state, triggering follow-up async work. Different from `start_async` (which runs concurrently with the render) — `defer` guarantees the client has received the render before the callback executes, so any state changes in the callback trigger a *second* render. Phoenix's `send(self(), :after_render)` pattern serves the same purpose. API: `self.defer(self._send_notification, user_id=42)` in any event handler. ~40 lines Python. *This is the clean way to do "render first, then do work" — currently developers hack this with `start_async` + a sleep, which is fragile.*

**Template fragments (static subtree tracking)** — Track which parts of a template are static (never change between renders) and which are dynamic. Only re-render and diff the dynamic subtrees. Phoenix does this at compile time with HEEx — static parts are sent once and never re-transmitted. For djust, the Rust template engine can fingerprint static subtrees on first render, then skip them in subsequent VDOM diffs. This is the single biggest wire-size optimization possible — most templates are 80%+ static HTML. Combined with selective re-rendering, this makes large pages as efficient as small ones. Implementation: Rust-side static tree fingerprinting + client-side fragment cache. *This is how Phoenix achieves sub-millisecond updates on complex pages — we need it for parity.*

**`used_input?` / server-side feedback control (Phoenix 1.0 parity)** — Phoenix 1.0 *removed* the client-side `phx-feedback-for` attribute and replaced it with `Phoenix.Component.used_input?/2` — a server-side function that checks whether a form input has been interacted with. This is strictly better: it's testable, works without JS, and doesn't flash errors during re-renders. djust equivalent: `self.field_touched(field_name)` returning `True` after the field has received any `dj-change` or `dj-blur` event. Template usage: `{% if form.email.errors and field_touched.email %}`. ~40 lines Python + ~10 lines JS. *This supersedes the earlier `dj-feedback` proposal — server-side is more robust and aligns with Phoenix 1.0's direction.*

**Selective re-rendering** — Only re-render and diff components whose state actually changed. Currently, every event triggers a full template re-render and VDOM diff for the entire view. Track which instance attributes changed and only re-render affected component subtrees. *React does this via reconciliation; Phoenix does this via separate component VDOM trees.*

**`dj-spread` / attribute rest** — Pass remaining HTML attributes through to the root element of a component. Phoenix's `{@rest}` pattern. API: `<div {{ attrs|spread }}>`. Implementation: Rust template filter. Essential for reusable component libraries.

```python
# Component usage in parent template:
{% component "button" variant="primary" class="mt-4" aria-label="Save" data-testid="save-btn" %}
# Component template renders: <button class="btn btn-primary mt-4" aria-label="Save" data-testid="save-btn">
```

**Function Components (stateless render functions)** — Lightweight components that are just a Python function returning HTML, with no WebSocket connection, no state, and no lifecycle. Phoenix's `Phoenix.Component` module (added in 0.18) transformed how people write UIs — most "components" are stateless and don't need the overhead of a LiveComponent. API: `@component` decorator on a function that takes a dict of assigns and returns a string. Callable from templates via `{% call button variant="primary" %}Click me{% endcall %}`. The Rust engine resolves these at render time with zero overhead. ~150 lines Python + Rust template support.

```python
from djust import component

@component
def button(assigns):
    variant = assigns.get('variant', 'default')
    children = assigns['children']
    return f'<button class="btn btn-{variant}">{children}</button>'
```

*This is the missing middle ground between "write raw HTML" and "create a full LiveComponent." 80% of reusable UI pieces (buttons, cards, badges, icons, alert boxes) are stateless. Forcing developers to create a LiveComponent class for a styled button is the kind of friction that makes people reach for React instead. Phoenix learned this lesson and added function components — djust should too.*

**Declarative component assigns (Phoenix 1.0 parity)** — Declare expected assigns with types, defaults, and required/optional status on LiveComponents and function components. Phoenix's `attr :name, :string, required: true` and `slot :inner_block, required: true` macros catch misconfiguration at compile time. djust equivalent: class-level `assigns` declaration validated at mount time, with clear error messages in DEBUG mode. This enables: auto-generated component documentation, IDE autocomplete for component attributes, runtime validation that catches typos early, and automatic type coercion (string → int for numeric assigns). ~120 lines Python.

```python
from djust import LiveComponent, Assign

class Button(LiveComponent):
    assigns = [
        Assign('variant', type=str, default='default', values=['default', 'primary', 'danger']),
        Assign('size', type=str, default='md'),
        Assign('disabled', type=bool, default=False),
    ]
    slots = ['inner_block']  # Required slot (children)
    template_name = 'components/button.html'
```

**`JS.ignore_attributes` equivalent (Phoenix 1.1 parity)** — Mark specific HTML attributes as client-owned so VDOM patching doesn't overwrite them. `<dialog dj-ignore-attrs="open">` prevents the server from resetting the `open` attribute that the browser manages. Essential for integrating with browser-native elements (`<dialog>`, `<details>`) and third-party JS libraries that set attributes the server doesn't know about. Phoenix 1.1 added `JS.ignore_attributes/1` for exactly this. ~20 lines JS.

**Colocated JS hooks with namespacing (Phoenix 1.1 parity)** — Write hook JavaScript inline alongside the template that uses it, instead of in a separate file. Phoenix 1.1's `ColocatedHook` was their most requested DX feature. For djust, since there's no build step, the extraction can happen at collectstatic time or via the Rust pre-processor. Also includes **hook namespacing** (Phoenix 1.1) — automatically prefix hook names with the view/component module path to prevent name collisions. When two components both define a `Chart` hook, they become `myapp.DashboardView.Chart` and `myapp.AnalyticsView.Chart` internally. ~150 lines Python + Rust.

**`UploadWriter` — Raw upload byte stream access (Phoenix 1.0 parity)** — Access raw upload byte streams during chunked transfer for server-to-server streaming (e.g., pipe directly to S3 without buffering to disk). `UploadWriter` class with `write_chunk(chunk)` and `close()` methods, passed to `allow_upload(writer=MyWriter)`. ~100 lines Python.

**Rust template engine parity** — Close the remaining gaps: model attribute access via PyO3 `getattr` fallback, `&quot;` escaping in attribute context, broader custom tag handler support.

**Database change notifications (PostgreSQL LISTEN/NOTIFY → LiveView push)** — Subscribe to PostgreSQL NOTIFY channels and automatically push updates to connected LiveViews when database rows change. `self.listen('table_changes', channel='orders_updated')` in mount, `def handle_info(self, message)` receives the notification payload. Combined with Django signals or database triggers, this creates truly reactive UIs where a change in one user's session (or a Celery task, or a management command) instantly reflects in all connected views without any explicit broadcasting code. Phoenix achieves this via PubSub + Ecto; Django has no built-in equivalent, but PostgreSQL's `LISTEN/NOTIFY` is a perfect fit. Implementation: async listener on the Channels layer that bridges `pg_notify` → `channel_layer.group_send()`. Optional `@notify_on_save` model mixin that auto-sends NOTIFY on `post_save`. ~150 lines Python. *This is the killer feature for dashboards, admin panels, and collaborative apps — "change the database, all connected users see it instantly" with zero explicit pub/sub wiring. No other Python framework has this built-in. Phoenix gets it implicitly from Ecto's PubSub; Rails has it via ActionCable + PostgreSQL triggers but it's not first-class. Making this a one-liner in djust is a genuine adoption driver.*

```python
# Target API
from djust import LiveView
from djust.db import notify_on_save

@notify_on_save  # Auto-sends pg_notify on Order.save()
class Order(models.Model):
    status = models.CharField(max_length=20)

class OrderDashboardView(LiveView):
    def mount(self, request, **kwargs):
        self.orders = list(Order.objects.filter(status='pending'))
        self.listen('orders')  # Subscribe to pg_notify channel

    def handle_info(self, message):
        if message['type'] == 'db_notify':
            self.orders = list(Order.objects.filter(status='pending'))
```

**Virtual/windowed lists (`dj-virtual`)** — Render only the visible portion of large lists, recycling DOM elements as the user scrolls. `<div dj-virtual="items" dj-virtual-item-height="48" dj-virtual-overscan="5">` renders ~20-30 visible items plus overscan, even if `items` has 10,000 entries. The server sends the full list to the client once (or uses temporary assigns + streams for truly large datasets), and the client handles windowing purely client-side — no server round-trip per scroll. Different from streams with `:limit` (which manages server memory) — virtual lists manage *client DOM performance*. The two complement each other: streams feed data in, virtual lists render it efficiently. React's `react-window` and `react-virtuoso` are the most popular React libraries by download count after React itself — this is the #1 performance pattern for data-heavy apps. Implementation: CSS `transform: translateY()` positioning with a sentinel element for scroll tracking, recycled DOM nodes via the existing morph pipeline. ~120 lines JS + ~30 lines Python (context helper for slice calculation). *Without virtual lists, djust apps with 500+ row tables visibly lag. With virtual lists, 100K rows render at 60fps. This is not optional for admin panels, log viewers, or any data-intensive application.*

**`dj-viewport-top` / `dj-viewport-bottom` — Bidirectional infinite scroll (Phoenix 1.0 streams parity)** — Fire server events when the first or last child of a stream container enters the viewport. `<div dj-stream="messages" dj-viewport-top="load_older" dj-viewport-bottom="load_newer">`. Combined with stream `:limit` (cap DOM elements to N), this enables memory-efficient bidirectional infinite scroll — load older messages when scrolling up, newer when scrolling down, and garbage-collect off-screen items. Phoenix 1.0 added `phx-viewport-top` and `phx-viewport-bottom` specifically for this pattern. Implementation: IntersectionObserver on first/last stream children, event push on intersection, stream `limit` option to remove elements from the opposite end. ~60 lines JS + ~40 lines Python. *This is the foundation for chat apps, activity feeds, and log viewers — the three most common real-time UI patterns.*

**Service worker core improvements** — Instant page shell (cached head/nav/footer served instantly, swap `<main>` on response). WebSocket reconnection bridge (buffer events in SW during disconnect, replay on reconnect).

### Milestone: v0.5.1 — Developer Experience, Testing & Form Patterns

**Goal**: Make djust a joy to develop with. Ship the testing utilities, error overlay, form patterns, and computed state that transform the daily development experience. These were split from v0.5.0 to ship the core async/component primitives faster.

**LiveView testing utilities** — (Moved from v0.5.0) The existing `LiveViewTestClient` covers basic mount/event/render. Production apps need: `assert_push_event(event_name, params)` to verify server→client push events, `assert_patch(path)` / `assert_redirect(path)` for navigation testing, `render_async()` that waits for `start_async` callbacks to complete before asserting, `follow_redirect()` for chaining navigation, `assert_stream_insert(stream, item)` for testing streaming operations, and `trigger_info(message)` for testing `handle_info` handlers. Phoenix's `LiveViewTest` is considered best-in-class testing DX — we need parity. ~300 lines Python.

```python
# Target API
from djust.testing import LiveViewTestClient

async def test_search_with_debounce(self):
    view = await LiveViewTestClient.mount(SearchView, user=self.user)
    await view.type('#search-input', 'django')  # simulates dj-model input
    await view.assert_has_element('.search-results')
    await view.assert_push_event('highlight', {'query': 'django'})
```

**Error overlay (development mode)** — (Moved from v0.5.0) In-browser Python stack traces with syntax-highlighted source, relevant view state, and the event that triggered the error. ~100 lines Python + ~80 lines JS.

**`@computed` decorator for derived state** — (Moved from v0.5.0) Memoize derived values that depend on other state, re-computing only when dependencies change. React's `useMemo` equivalent.

```python
from djust.decorators import computed

class ProductView(LiveView):
    @computed('items', 'tax_rate')
    def total_price(self):
        subtotal = sum(i['price'] * i['qty'] for i in self.items)
        return subtotal * (1 + self.tax_rate)
```

**`dj-lazy` — Lazy component loading** — (Moved from v0.5.0) Defer rendering of below-fold or hidden components until they enter the viewport via IntersectionObserver. ~40 lines JS + Python decorator.

**Component context sharing** — (Moved from v0.5.0) React's Context API equivalent. `self.provide_context('theme', self.theme)` in a parent, `self.consume_context('theme')` in descendants. ~80 lines Python.

**`dj-trigger-action` — Bridge live validation to standard form POST** — (Moved from v0.5.0) Trigger standard HTML form submission after LiveView validation passes. Essential for OAuth flows, payment gateways. ~30 lines JS.

**Scoped loading states (`dj-loading`)** — (Moved from v0.5.0) Show loading indicators scoped to specific events. `<div dj-loading="search">Searching...</div>` only shows while the `search` event is in-flight.

**Error boundaries** — (Moved from v0.5.0) If a LiveComponent raises, isolate failure to that component with error fallback UI while the rest of the page continues working.

**Nested form handling (`inputs_for`)** — (Moved from v0.5.0) Django formset/inline-formset patterns with LiveView-aware wrappers. Auto add/remove, index maintenance across patches.

**Stable component IDs (React 19 `useId` equivalent)** — (Moved from v0.5.0) `self.unique_id(suffix)` returning deterministic IDs stable across renders. ~30 lines Python.

**Native `<dialog>` element integration** — (Moved from v0.5.0) Browser-native modals with `dj-dialog="open|close"`. Built-in focus trapping, backdrop, Escape handling. ~20 lines JS.

**Automatic dirty tracking** — Track which view attributes have changed since mount or the last event, exposing `self.changed_fields` and `self.is_dirty`. Template: `{% if is_dirty %}<button dj-click="save">Save changes</button>{% endif %}`. Use cases: "unsaved changes" warnings (`beforeunload`), conditional save buttons, optimized `handle_event` that skips work when nothing changed. Combined with selective re-rendering, dirty tracking is the foundation for efficient large views. ~60 lines Python.

**Type-safe template validation (`manage.py djust_typecheck`)** — Static analysis that validates template variables against the view's `get_context_data()` return type at startup or CI time. The Rust template engine already parses templates into an AST — extract all variable references (`{{ user.name }}`, `{% if is_admin %}`) and verify they exist in the view's context. Catches typos like `{{ usre.name }}` before they hit production. API: `manage.py djust_typecheck` scans all LiveView templates and reports mismatches. Optional `strict_context = True` class attribute for per-view opt-in. ~200 lines Python + Rust AST extraction. *Neither Phoenix nor React catches template variable typos statically without TypeScript. This is a genuine differentiator — Python's dynamic typing makes template bugs the #1 source of "it renders blank and I don't know why" issues. Catching them at CI time transforms the debugging experience.*

**Multi-step form wizard primitive (`WizardMixin`)** — Built-in support for multi-step forms (onboarding flows, checkout, surveys, registration). `WizardMixin` manages step index, per-step validation, back/forward navigation with state preservation, and URL sync via `live_patch` so each step is bookmarkable. API: `steps = [Step1Form, Step2Form, Step3Form]` class attribute, `self.current_step`, `self.next_step()`, `self.prev_step()`, `self.step_data` (accumulated across steps), `self.is_complete`. Per-step validation runs the current step's Django form before advancing — if invalid, errors display and the user stays on that step. Template helpers: `{% dj_wizard_progress %}` renders a step indicator, `{% dj_wizard_nav %}` renders back/next buttons. On final step, `self.wizard_complete(all_data)` is called with merged data from all steps. ~200 lines Python + template tags. *Multi-step forms are the #2 most common UI pattern after CRUD (every SaaS onboarding, every checkout flow, every survey tool). Currently, every djust developer builds this from scratch with manual step tracking, per-step validation, and back-button state management. Phoenix doesn't have this either — making it a djust differentiator. React has `react-hook-form` multi-step patterns but nothing built-in. A first-class wizard primitive that integrates with Django forms, `live_patch` URLs, and VDOM patching is a genuine value-add that no framework offers natively.*

```python
# Target API
from djust import LiveView
from djust.wizard import WizardMixin, Step

class OnboardingView(WizardMixin, LiveView):
    steps = [
        Step('account', AccountForm, template='onboarding/account.html'),
        Step('profile', ProfileForm, template='onboarding/profile.html'),
        Step('preferences', PrefsForm, template='onboarding/prefs.html'),
    ]

    def wizard_complete(self, data):
        user = User.objects.create(**data['account'])
        Profile.objects.create(user=user, **data['profile'])
        self.put_flash('success', 'Welcome!')
        self.live_redirect(f'/dashboard/')
```

**`dj-no-submit` — Prevent enter-key form submission** — Prevent forms from submitting when the user presses Enter in a text input. `<form dj-submit="save" dj-no-submit="enter">`. This is the #1 form UX annoyance in LiveView-style apps — users press Enter expecting to confirm a field, and accidentally submit the entire form. Currently requires a `dj-hook` or JavaScript. ~10 lines JS. *Tiny feature, huge DX impact — every multi-field form needs this.*

### Milestone: v0.6.0 — Production Hardening & Interactivity

**Goal**: Make djust production-ready for teams deploying real apps, and close the remaining interactivity gap with client-side frameworks.

**Animations & transitions** — Declarative `dj-transition` attribute for enter/leave CSS transitions with three-phase class application (start → active → end), matching Phoenix's `JS.transition`. `dj-remove` attribute for exit animations before element removal. FLIP technique for list reordering animations. `dj-transition-group` for animating list items entering/leaving (React's `<TransitionGroup>` / Vue's `<transition-group>` equivalent — essential for todo lists, kanban boards, search results). Skeleton/shimmer loading state components. *(View Transitions API integration promoted to v0.5.0.)*

**Sticky LiveViews** — Mark a LiveView as `sticky=True` in `live_render()` to keep it alive across live navigations. Use case: persistent audio/video player, sidebar, notification center. The sticky view doesn't unmount/remount when the user navigates — it stays connected and retains state. Phoenix added this and it's a big win for app-shell patterns.

**`dj-mutation` — DOM mutation events** — Fire a server event when specific DOM attributes or children change via MutationObserver. `<div dj-mutation="handle_change" dj-mutation-attr="class,style">`. Use case: third-party JS libraries (charts, maps, rich text editors) that modify the DOM outside djust's control — the server needs to know about these changes to keep state in sync. Currently requires a custom `dj-hook` for every integration. One declarative attribute replaces boilerplate in every third-party-widget integration. Implementation: MutationObserver config from attributes, debounced event push. ~50 lines of JS.

**`dj-sticky-scroll` — Auto-scroll preservation** — Automatically keep a scrollable container pinned to the bottom when new content is appended (chat messages, logs, terminal output), but stop auto-scrolling if the user scrolls up to read history. Resume auto-scroll when they scroll back to bottom. This is the #1 asked-for behavior in chat and log-viewer apps and currently requires a custom `dj-hook` with scroll position math. `<div dj-sticky-scroll>` handles it declaratively. ~40 lines of JS.

**`dj-track-static` — Static asset change detection (Phoenix `phx-track-static` parity)** — Mark `<link>` and `<script>` tags with `dj-track-static` to record their fingerprinted URLs at mount time. On WebSocket reconnect, the client compares current asset URLs against the stored versions. If any have changed (server deployed new code while the user was disconnected), show a prompt: "App updated — click to reload" or auto-reload based on configuration. Without this, users on long-lived WebSocket connections silently run stale JavaScript after a deploy — the server sends new HTML referencing new JS bundles, but the client still has the old JS loaded. This causes subtle breakage that's impossible to debug. Phoenix has had `phx-track-static` since 0.15 and it's used on every production deploy. Implementation: client stores a hash of `[dj-track-static]` element `src`/`href` values on connect; on reconnect, re-hash and compare. If different, fire a `dj:stale-assets` event (or auto-reload if `dj-track-static="reload"` is set). ~30 lines JS + ~10 lines Python template tag. *This is a production-critical feature that every deployed app needs. Without it, zero-downtime deploys are a myth — you get zero downtime on the server but broken behavior on connected clients.*

**WebSocket per-message compression (permessage-deflate)** — Enable `permessage-deflate` WebSocket extension for automatic compression of all WS messages. VDOM patches are highly compressible (repetitive HTML fragments, JSON structure) — typical compression ratios of 60-80% reduction in wire size. Django Channels/Daphne supports this via configuration; djust needs to: (1) enable the extension in the consumer, (2) ensure the client negotiates it, (3) document the memory tradeoff (each connection holds a zlib context, ~64KB per connection — fine for most deployments, configurable via `DJUST_WS_COMPRESSION = True/False`). Implementation: ~20 lines of consumer configuration + documentation. *This is the single cheapest bandwidth optimization available — just turning it on reduces WebSocket traffic by 60-80% with no code changes. Combined with template fragments (which reduce what's sent), compression reduces the wire format of what remains. Every production deployment should have this; it should be on by default.*

**Runtime layout switching** — Change the base layout template during a LiveView session without a full page reload. `self.set_layout('layouts/fullscreen.html')` in an event handler swaps the surrounding layout (nav, sidebar, footer) while preserving the inner LiveView state. Use cases: toggle between admin layout and public layout, switch to fullscreen mode for a presentation or editor, show a minimal layout during onboarding then switch to the full app layout. Phoenix 1.1 added runtime layout support. Implementation: the layout is rendered server-side and sent as a special WS message; the client replaces everything outside `[data-djust-root]`. ~80 lines Python + ~30 lines JS. *This is how real apps work — layouts aren't static. A document editor that goes fullscreen, a dashboard that hides the sidebar, an onboarding flow that uses a minimal layout then switches to the app layout on completion. Without runtime layout switching, these patterns require a full page reload, losing all state.*

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

**CSS `@starting-style` animations (zero-JS entry animations)** — The CSS `@starting-style` rule (supported in Chrome 117+, Safari 17.5+, Firefox 129+) enables entry animations without any JavaScript — the browser animates from the `@starting-style` values to the normal values when an element enters the DOM. For djust, this means elements inserted by VDOM patches can animate in with pure CSS, no `dj-transition` JS needed: `@starting-style { .card { opacity: 0; transform: translateY(10px); } }`. djust's role: (1) document the pattern, (2) ensure VDOM morphing creates new DOM nodes (not just attribute updates) when `dj-animate` is present so `@starting-style` triggers, (3) ship a small `djust-animations.css` utility file with common entry patterns. ~0 lines JS, ~30 lines CSS. *This is the modern replacement for JS-driven enter animations. Combined with `dj-transition` for exit animations (which still need JS), this gives a complete animation story with minimal framework code. djust would be the first server-rendered framework to document and optimize for `@starting-style`.*

**Hot View Replacement (dev mode)** — When a developer saves a Python view file during development, push the re-rendered template to connected clients without a full page reload or WebSocket reconnect. Currently, Python changes require a manual page refresh (and daphne requires a full server restart). Implementation: file watcher detects `.py` changes → hot-reload the module → call `render()` with existing state → push the diff. The existing hot-reload infrastructure handles template changes; extending it to view classes requires re-importing the module and re-instantiating the view with preserved state. ~200 lines Python. *React's Fast Refresh and Phoenix's code reloading both preserve state across saves. This is the difference between "I'm developing a web app" and "I'm developing a web app that fights me" — every save-refresh-scroll-back cycle breaks flow state.*

**Advanced service worker features** — VDOM patch caching (cache last rendered DOM per page; diff against fresh response on back-navigation). LiveView state snapshots (serialize on unmount, restore on back-nav). Request batching for multi-component pages.

### Milestone: v0.7.0 — Navigation, Smart Rendering & AI Patterns

**Goal**: Make navigation feel like a SPA and establish djust as the best framework for AI-powered applications.

**Keep-Alive / `dj-activity` (React 19.2 `<Activity>` parity)** — React 19.2's `<Activity>` component is one of the most significant additions to any framework in 2025: it pre-renders hidden routes in the background and maintains their state when navigating away. Map this to djust: `{% dj_activity "settings-panel" visible=show_settings %}...{% enddj_activity %}` wraps a section that stays mounted (WebSocket alive, state preserved) even when hidden. Hidden activities pause effects and defer updates until visible. Use cases: tab panels where switching tabs preserves form input and scroll position, dashboard widgets that pre-load data before the user clicks, multi-step wizards where going "back" doesn't lose state. Different from `sticky=True` (which keeps a LiveView alive during navigation) — Activity is about *within-page* show/hide with preserved state and background pre-rendering. Implementation: server-side activity registry tracks hidden views, client sends visibility changes, hidden activities skip VDOM patches until shown. ~150 lines Python + ~60 lines JS. *This is how React makes navigations feel instant — the destination is already rendered. Combined with `live_session` shared connections, djust can pre-render the next likely page while the user reads the current one. No other server-rendered framework has this.*

```python
# Target API
class DashboardView(LiveView):
    def mount(self, request, **kwargs):
        self.active_tab = 'overview'

    @event_handler
    def switch_tab(self, tab: str = "", **kwargs):
        self.active_tab = tab
        # Settings panel stays mounted, form state preserved
        # Charts panel pre-renders data in background
```

**`live_session` enhancements** — Basic `live_session()` routing is implemented (shared WS connections, route map injection). Remaining work: shared `on_mount` hooks per session, root layout declaration, and automatic full-HTTP navigation when crossing session boundaries. This is how Phoenix structures apps — each session is a logical unit with shared auth, layout, and state lifecycle.

**Push navigation from server** — `self.push_navigate('/new-path/')` to trigger SPA-like navigation from an event handler. Different from `redirect()` (full HTTP) — this keeps the WebSocket alive and mounts a new LiveView without a page reload. Combined with `self.push_patch('/same-view/?page=2')` (update URL without remount, triggers `handle_params()`), this gives full Phoenix navigation parity.

**Portal rendering** — Render content into a DOM container outside the component's tree. Use case: modals, toasts, tooltips, and dropdowns that are logically owned by a deeply nested component but need to render at `<body>` level for z-index/overflow reasons. Template directive: `{% dj_portal target="#modal-container" %}...{% enddj_portal %}`.

**Back/forward state restoration** — When the user navigates with browser back/forward, restore the previous view state from a serialized snapshot rather than remounting from scratch. The URL `popstate` event triggers `handle_params()` with the previous parameters, but expensive state (search results, scroll position, expanded accordions) should be cached client-side and restored instantly. *React Router does this with loader caching; Phoenix does it with `push_patch` state. This is what makes SPA navigation feel native.*

**Stale-while-revalidate pattern** — Show the previous/cached render instantly on mount, then update asynchronously when fresh data loads. Combined with `assign_async`, this creates instant page transitions — the user sees the last-known state immediately (< 16ms) while the server fetches current data. API: `self.assign_stale('metrics', self._load_metrics, stale_ttl=30)` — serves cached value within TTL, fetches fresh in background, re-renders on completion. *React Query / SWR popularized this pattern; it's the key to making server-rendered apps feel as fast as client-side SPAs. Phoenix doesn't have this natively, making it a djust differentiator.*

**Islands of interactivity** — Mark specific sections of a page as "live" while the rest stays static HTML. `{% dj_island %}...{% enddj_island %}` wraps interactive zones that establish their own WebSocket connections. The surrounding page renders once via HTTP and is never re-rendered. Use case: a blog post (static) with a comment section (live), a product page (static) with an add-to-cart button (live). Reduces WebSocket connections, server memory, and VDOM tree size dramatically for content-heavy pages. *Astro popularized this pattern; Fresh (Deno) and Qwik use it. React Server Components achieve similar results. This is the most requested architectural pattern for content-heavy sites that need small interactive zones.* Implementation: each island is a lightweight LiveView with its own VDOM tree, mounted lazily on scroll-into-view (reusing existing `dj-lazy` infrastructure).

**Server-only components** — Components that render once on HTTP and never establish a WebSocket connection. Use case: static headers, footers, marketing sections, and any content that doesn't need interactivity. Reduces WebSocket connection count and server memory for pages that mix interactive and static content. *React Server Components popularized this pattern — not everything needs to be live.*

**AI application primitives** — First-class patterns for building AI-powered applications, djust's strongest vertical. Building on the existing `StreamingMixin`, add: `{% dj_ai_stream %}` template component with built-in markdown rendering, code syntax highlighting, and copy buttons. `self.stream_ai(stream_name, llm_generator)` helper that handles backpressure, token batching, and error recovery for any LLM API (OpenAI, Anthropic, local models). Typing indicator that auto-shows during AI generation. Conversation history component with scroll anchoring. Tool-use visualization (show when AI is "thinking" or calling tools). *Django's ORM + Celery + djust's streaming is already the best stack for AI apps — these primitives make it 10x faster to build ChatGPT-like interfaces. No other framework has purpose-built AI streaming components.*

**Streaming markdown renderer** — Purpose-built incremental markdown parser that renders tokens as they arrive from an LLM stream, without waiting for the full response. Handles the hard edge cases: incomplete code fences (don't close the `<pre>` until the closing ``` arrives), partial links, incremental table rendering, and nested list continuation. Includes syntax highlighting via a lightweight Rust-side highlighter (no external JS dependency). API: `self.stream_markdown('response', llm_generator)` — the template renders `<div dj-stream="response" dj-markdown>` and tokens are parsed and rendered incrementally. Compare to the current approach where developers must either (a) send raw text and parse client-side with a 50KB+ JS library, or (b) wait for the full response and render server-side. Implementation: Rust-side incremental CommonMark parser (~500 lines Rust) + client-side `dj-markdown` attribute that applies syntax highlighting classes. ~200 lines total Python/JS glue. *Every AI chatbot needs streaming markdown with code highlighting. Making this a framework primitive — not a third-party dependency — is the strongest possible signal that djust is the AI application framework.*

**i18n live language switching** — Switch the active language/locale in a LiveView session without a page reload. `self.set_locale('es')` in an event handler activates Django's `translation.activate()` for the current session, re-renders the template with the new locale's translations, and sends the diff. All connected views in the same `live_session` switch together. Django's i18n infrastructure (`{% trans %}`, `{% blocktrans %}`, `gettext`) works unchanged — the Rust template engine delegates `{% trans %}` tags to Python for resolution. API: `self.set_locale(lang_code)` + `self.current_locale` property. Template: `<select dj-change="set_locale">{% for lang in LANGUAGES %}<option value="{{ lang.0 }}">{{ lang.1 }}</option>{% endfor %}</select>`. ~60 lines Python. *Internationalized apps are 40%+ of all Django deployments. Currently, language switching requires a full page reload (Django's `set_language` view). Making it instant via LiveView is a meaningful UX improvement — the user clicks a language selector and the entire page re-renders in the new language with a smooth transition. No other LiveView framework has this built-in.*

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

**Form status awareness (React 19 `useFormStatus` equivalent)** — Child components inside a form should be able to read whether the parent form is currently submitting, without prop drilling. React 19's `useFormStatus` lets any nested component access `{ pending, data, method, action }` from the nearest `<form>`. djust equivalent: any element with `dj-form-pending` attribute auto-toggles visibility/class based on whether its ancestor form's `dj-submit` event is in-flight. Template: `<button type="submit"><span dj-form-pending="hide">Save</span><span dj-form-pending="show">Saving...</span></button>`. Works with the existing `dj-lock` and `dj-disable-with` but provides a more general-purpose pattern for any element — not just the submit button. ~30 lines JS. *This is how React 19 handles loading states in forms, and it's more composable than per-button solutions.*

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
| **`dj-page-loading`** | Built-in navigation loading bar — neither Phoenix nor React include this natively | v0.4.1 |
| **`dj-paste` clipboard** | Built-in paste handling (text + images) — routes images to UploadMixin automatically | v0.4.1 |
| **Database change notifications** | PostgreSQL LISTEN/NOTIFY → LiveView push — one-liner reactive database UIs | v0.5.0 |
| **Virtual/windowed lists** | Built-in DOM virtualization for 100K+ row lists at 60fps — react-window equivalent | v0.5.0 |
| **Multi-step wizard** | First-class `WizardMixin` with per-step validation, URL sync, progress — no framework has this natively | v0.5.1 |
| **Function components** | Stateless render functions — Phoenix has this, but djust's Rust engine resolves them at near-zero cost | v0.5.0 |
| **Error overlay (dev)** | In-browser Python stack traces — Phoenix shows errors in terminal only; this matches Next.js DX | v0.5.1 |
| **Native `<dialog>` integration** | First LiveView framework with first-class browser-native modal support | v0.5.1 |
| **Dirty tracking** | Built-in `is_dirty` / `changed_fields` for unsaved-changes UX — no equivalent in Phoenix or React without manual tracking | v0.5.1 |
| **Server Actions (`@action`)** | React 19-style mutation handlers with auto pending/error/success states — combines best of Phoenix events + React 19 patterns | v0.8.0 |
| **Type-safe template validation** | Static analysis catches template variable typos at CI time — neither Phoenix nor React has this without TypeScript | v0.5.1 |
| **Streaming markdown renderer** | Incremental Rust-side CommonMark parser for LLM streaming — no framework has a built-in solution | v0.7.0 |
| **Keep-Alive / Activity** | Pre-render hidden routes, preserve state across tab switches — React 19.2 has this, Phoenix doesn't | v0.7.0 |
| **Keyed for-loop change tracking** | Only re-render changed items in loops — Rust engine makes this O(changed) not O(total) | v0.5.0 |
| **`self.defer()` post-render** | Clean "render first, then do work" pattern — no other LiveView framework has a first-class API for this | v0.5.0 |
| **CSS `@starting-style` animations** | Zero-JS entry animations using modern CSS — no framework has optimized for this yet | v0.6.0 |
| **Hot View Replacement** | Edit Python → see result without refresh. Phoenix reloads but loses state; React Fast Refresh preserves it | v0.6.0 |
| **Django admin LiveView widgets** | Real-time admin dashboards — no other LiveView framework integrates with an existing admin | v0.7.0 |
| **WebSocket compression** | `permessage-deflate` for 60-80% bandwidth reduction — on by default, zero code changes | v0.6.0 |
| **Runtime layout switching** | Swap nav/sidebar/layout without reload — Phoenix 1.1 has it, React doesn't natively | v0.6.0 |
| **i18n live language switching** | Switch locale without page reload — no LiveView framework has this built-in | v0.7.0 |
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
- **Navigation API** — The Navigation API (`navigation.navigate()`) is the modern replacement for `pushState`/`popstate`, with better interception, transition tracking, and abort support. Chrome ships it; Safari/Firefox are implementing. Evaluate whether djust's `live_patch`/`live_redirect` should use it where available for cleaner SPA navigation and better integration with View Transitions API.
- **WebTransport** — Next-generation transport after WebSocket: lower latency, supports unreliable delivery (useful for cursor positions where dropped updates are fine), and multiplexing built-in. Chrome supports it. Evaluate as a third transport option alongside WebSocket and SSE for low-latency use cases.
- **Content-Visibility CSS property** — `content-visibility: auto` lets the browser skip rendering of off-screen content entirely. For djust pages with long lists or many components, this is free performance — the browser handles "virtual scrolling" natively. Evaluate documenting this pattern and ensuring VDOM patching doesn't interfere with content-visibility optimizations.
- **Popover API** — The HTML `popover` attribute provides browser-native popovers with light dismiss, top-layer rendering, and accessibility built-in. Evaluate integrating with `djust-components` dropdown/tooltip components as a progressive enhancement.
- **React Compiler-style auto-memoization** — React 19's compiler automatically inserts `useMemo`/`useCallback` equivalents. Evaluate whether the Rust template engine can automatically detect pure (side-effect-free) template subtrees and cache their rendered output across re-renders without developer annotation. This would be a "zero-config" version of template fragments.
- **Speculation Rules API** — Chrome's Speculation Rules API (`<script type="speculationrules">`) enables browser-native prefetching and prerendering of likely navigation targets. More powerful than `<link rel="prefetch">` — the browser actually pre-renders the entire page in a hidden tab. Evaluate generating speculation rules from `live_session` route maps so the browser pre-renders likely next pages automatically.
- **Cross-document View Transitions (Level 2)** — View Transitions API Level 2 supports cross-document transitions (MPA, not just SPA). This means djust's full-HTTP navigations (not just `live_redirect`) can animate smoothly. Evaluate whether djust should inject `@view-transition` CSS and `pagereveal`/`pageswap` event handlers automatically for `live_redirect` targets.
- **Shared Element Transitions** — Chrome's shared element transitions allow specific elements (images, cards, headers) to animate smoothly between pages/states. Combined with View Transitions API, this creates native-app-quality navigation. Evaluate generating `view-transition-name` from `dj-key` attributes so keyed elements animate between renders automatically.
- **WebGPU compute for VDOM diffing** — WebGPU is shipping in all major browsers. Evaluate whether large VDOM tree diffs (1000+ nodes) could benefit from GPU-accelerated parallel comparison. Speculative — the overhead of GPU dispatch may exceed the diff cost for typical tree sizes.
- **Django async views integration** — Django 4.1+ supports `async def` views natively. Evaluate deeper integration: `async def mount()`, `async def handle_event()`, native `await` in event handlers without `start_async` wrapper. Could simplify the async story significantly for Django 5.0+ projects.
- **Trusted Types API** — Chrome enforces Trusted Types to prevent DOM XSS. Evaluate ensuring all djust client-side DOM writes (`innerHTML` in morph, streaming HTML injection) go through Trusted Types policies. This would make djust the first LiveView framework with Trusted Types compliance — a selling point for enterprise/security-conscious teams.
- **Federated LiveView (cross-origin embedding)** — Evaluate a protocol for embedding a LiveView from one Django app inside another app's page, with cross-origin WebSocket communication. Use case: microservices architecture where each team owns a LiveView widget. Related to the WebComponent export idea but more dynamic.

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
| **Handle params callback** | **`handle_params/3`** | React Router loaders | **Partial** (in navigation mixin) | **v0.4.0** |
| **JS Commands** | **`JS.*` module** | — | **Not started** | **v0.4.0** |
| **Connection CSS classes** | **`phx-connected`** | — | **Not started** | **v0.4.0** |
| **Form recovery** | **Auto on reconnect** | — | **Not started** | **v0.4.0** |
| **Stable conditional DOM** | **HEEx anchors** | — | **Broken (#559)** | **v0.4.0** |
| **Event ordering** | **Erlang mailbox** | — | **Broken (#560)** | **v0.4.0** |
| **Focus preservation** | **Auto (morph)** | **Reconciliation** | **Not started** | **v0.4.0** |
| **Confirm dialog** | **`data-confirm`** | — | **Done** | — |
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
| **JS Commands from hooks** | **Programmable JS API** | — | **Not started** | **v0.4.1** |
| **Scoped JS selectors** | **`to: {:closest}`** | — | **Not started** | **v0.4.1** |
| **`page_loading` on push** | **`page_loading: true`** | — | **Not started** | **v0.4.1** |
| `assign_async` / `AsyncResult` | `assign_async/3` | `<Suspense>` | **Not started** | **v0.5.0** |
| **`handle_async` callback** | **`handle_async/3`** | — | **Not started** | **v0.5.0** |
| Component `update` callback | `update/2` | `getDerivedStateFromProps` | Not started | v0.5.0 |
| View Transitions API | — | View Transitions | Not started | v0.5.0 |
| Nested components | `LiveComponent` | Component tree | Not started | v0.5.0 |
| Targeted events (`@myself`) | `phx-target` | — | Not started | v0.5.0 |
| Named slots | `slot/3` macro | `children` / slots | Not started | v0.5.0 |
| Direct-to-S3 uploads | `presign_upload` | — | Not started | v0.5.0 |
| Stream limits + viewport | `:limit`, viewport events | Virtualization | Not started | v0.5.0 |
| **Viewport top/bottom (streams)** | **`phx-viewport-top/bottom`** | — | **Not started** | **v0.5.0** |
| `handle_info` | `handle_info/2` | — | Not started | v0.5.0 |
| Template fragments | HEEx static tracking | — | Not started | v0.5.0 |
| **`used_input?` (server-side)** | **`used_input?/2`** | — | **Not started** | **v0.5.0** |
| **Declarative assigns** | **`attr/3`, `slot/3`** | **PropTypes/TS** | **Not started** | **v0.5.0** |
| **Function components** | **`Phoenix.Component`** | **Function components** | **Not started** | **v0.5.0** |
| Selective re-rendering | Per-component diff | Reconciliation | Not started | v0.5.0 |
| Attribute spread (`@rest`) | `{@rest}` | `...props` | Not started | v0.5.0 |
| **Ignore attributes (client-owned)** | **`JS.ignore_attributes`** | — | **Not started** | **v0.5.0** |
| **Colocated JS hooks + namespacing** | **`ColocatedHook`** | — | **Not started** | **v0.5.0** |
| **`UploadWriter` (stream upload)** | **`UploadWriter`** | — | **Not started** | **v0.5.0** |
| **Keyed for-loop change tracking** | **Auto in comprehensions** | — | **Not started** | **v0.5.0** |
| **`self.defer()` (post-render)** | **`send(self(), ...)`** | `useEffect` (post-render) | **Not started** | **v0.5.0** |
| **Testing utilities** | **`LiveViewTest`** | **Testing Library** | **Basic** (`LiveViewTestClient`) | **v0.5.1** |
| **Error overlay (dev)** | Error page | **Next.js overlay** | **Not started** | **v0.5.1** |
| Computed/derived state | — | `useMemo` | Not started | v0.5.1 |
| Lazy component loading | — | `React.lazy()` | Not started | v0.5.1 |
| Component context sharing | — | `useContext` | Not started | v0.5.1 |
| Trigger form action | `phx-trigger-action` | — | Not started | v0.5.1 |
| Nested forms | `inputs_for/4` | Formik nested | Not started | v0.5.1 |
| Scoped loading states | `phx-loading` | Suspense per-query | Not started | v0.5.1 |
| Error boundaries | — | `<ErrorBoundary>` | Not started | v0.5.1 |
| **Native `<dialog>`** | — | — | **Not started** | **v0.5.1** |
| **Stable component IDs** | — | **`useId`** | **Not started** | **v0.5.1** |
| **Form status awareness** | — | **`useFormStatus`** | **Not started** | **v0.8.0** |
| **Dirty tracking** | — | — | **Not started** | **v0.5.1** |
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
| **CSS `@starting-style`** | — | Framer Motion | **Not started** | **v0.6.0** |
| **Hot View Replacement** | Code reloading | Fast Refresh | **Not started** | **v0.6.0** |
| Stale-while-revalidate | — | SWR / React Query | Not started | v0.7.0 |
| `live_session` enhancements | `live_session/3` | — | Basic done | v0.7.0 |
| Push navigate (SPA nav) | `push_navigate` | — | Not started | v0.7.0 |
| Portal rendering | **`<.portal>`** (1.1) | `createPortal` | Not started | v0.7.0 |
| Back/forward restoration | `push_patch` state | Loader cache | Not started | v0.7.0 |
| Server-only components | — | Server Components | Not started | v0.7.0 |
| Islands of interactivity | — | Astro islands | Not started | v0.7.0 |
| AI streaming primitives | — | — | Not started | v0.7.0 |
| Server functions (RPC) | — | Server Actions | Not started | v0.7.0 |
| Django admin LiveView widgets | — | — | Not started | v0.7.0 |
| Prefetch on hover/intent | — | Remix prefetch | Not started | v0.7.0 |
| **Keep-Alive / Activity** | — | **`<Activity>`** (19.2) | **Not started** | **v0.7.0** |
| **Document metadata** | `live_title` | **Native** (React 19) | **Not started** | **v0.4.0** |
| **Type-safe template validation** | — | TypeScript | **Not started** | **v0.5.1** |
| **Streaming markdown renderer** | — | — | **Not started** | **v0.7.0** |
| **DB change notifications** | **PubSub + Ecto** | — | **Not started** | **v0.5.0** |
| **Virtual/windowed lists** | — | **`react-window`** | **Not started** | **v0.5.0** |
| **Multi-step wizard** | — | **`react-hook-form`** | **Not started** | **v0.5.1** |
| **Paste event handling** | — | **`onPaste`** | **Not started** | **v0.4.1** |
| **Scroll into view** | — | **`scrollIntoView`** | **Not started** | **v0.4.0** |
| **WS compression** | **Built-in (Cowboy)** | — | **Not started** | **v0.6.0** |
| **Runtime layout switching** | **Runtime layouts (1.1)** | — | **Not started** | **v0.6.0** |
| **i18n live switching** | — | — | **Not started** | **v0.7.0** |
| Dynamic form fields | `sort_param`/`drop_param` | — | Not started | Post-1.0 |

---

## Priority Matrix

| Milestone | Theme | Key Deliverables | Priority |
|-----------|-------|-----------------|----------|
| v0.4.0 | Stability & Core DX | Fix #559/#560, focus preservation, **`dj-value-*`**, **`handle_params`** (complete), **`on_mount` hooks**, **flash messages**, **`_target` param**, **`dj-scroll-into-view`**, connection CSS, form recovery, `dj-disable-with`, window events, `dj-debounce`/`dj-throttle` attrs, error messages, `djust_doctor`, latency simulator | **Critical** |
| v0.4.1 | JS Commands & Polish | **JS Commands**, programmable JS from hooks, scoped selectors (`closest`/`inner`), `page_loading` on push, **`dj-paste`**, `dj-shortcut`, `dj-copy`, `dj-page-loading`, `dj-click-away`, `dj-lock`, `dj-auto-recover`, `dj-cloak`, `dj-mounted`, `live_title` | **Critical** |
| v0.5.0 | Async, Core Components & Streams | **`assign_async`/`AsyncResult`**, **`handle_async`**, **function components**, **declarative assigns**, **`used_input?`**, nested LiveComponents + targeted events + slots, **component `update` callback**, `dj-spread`, **View Transitions API**, direct-to-S3 uploads, stream enhancements + **`dj-viewport-top/bottom`**, **`handle_info`**, **template fragments**, **keyed for-loop change tracking**, **`self.defer()`**, selective re-rendering, Rust engine parity, **database change notifications (pg_notify)**, **virtual/windowed lists** | **Critical** |
| v0.5.1 | Developer Experience & Forms | **Testing utilities**, **error overlay**, **`@computed`**, **`dj-lazy`**, **component context sharing**, **`dj-trigger-action`**, **scoped loading**, **error boundaries**, **nested forms**, **stable IDs**, **native `<dialog>`**, **dirty tracking**, **`dj-no-submit`**, **type-safe template validation**, **multi-step wizard (`WizardMixin`)** | **Critical** |
| v0.6.0 | Production & Interactivity | Animations/transitions + **`dj-transition-group`**, **CSS `@starting-style`**, **hot view replacement**, **streaming initial render**, **time-travel debugging**, **state undo/redo**, **connection multiplexing**, sticky LiveViews, `dj-mutation`, `dj-sticky-scroll`, monitoring, graceful degradation, CSP nonce, batch state updates, multi-tab sync, offline mutation queue, `dj-resize`, **WebSocket compression (permessage-deflate)**, **runtime layout switching** | **High** |
| v0.7.0 | Navigation, AI & Smart Rendering | **keep-alive/`dj-activity`**, **stale-while-revalidate**, **AI streaming primitives**, **streaming markdown renderer**, **server functions (RPC)**, **Django admin LiveView widgets**, **prefetch on hover/intent**, **i18n live language switching**, `live_session` enhancements, push navigate, portal rendering, back/forward restoration, server-only components, islands of interactivity | **High** |
| v0.8.0 | Server Actions & Async Streams | **Server Actions (`@action`)**, **form status awareness**, **async stream enumeration**, **`dj-model` improvements**, **form recovery improvements**, **targeted streaming** | **High** |
| v1.0.0 | Stable Release | DevTools extension, docs site, benchmarks, plugin system, starters, VS Code extension, TypeScript hook definitions, dead view/progressive enhancement, accessibility audit | **High** |
| Post-1.0 | Ecosystem | Framework portability, CRDT collab, AI generation, binary protocol, dynamic form fields, embedded LiveView (WebComponent) | **Medium** |

---

## Contributing

Want to help? See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

High-impact areas for contributions:

#### Quick Wins (< 1 day, great first contributions)
1. **`dj-value-*` static params** — ~20 lines JS, used in every template (P0)
2. **`_target` param in change events** — ~10 lines JS + ~5 lines Python, essential for forms (P0)
3. **`dj-disable-with`** — Auto-disable buttons during submission, ~20 lines JS
4. **Connection state CSS classes** — `dj-connected`/`dj-disconnected` on body, ~10 lines JS
5. **`dj-copy`** — Copy to clipboard, ~30 lines JS
6. **`dj-cloak`** — FOUC prevention, ~5 lines JS + 1 line CSS
7. **`live_title`** — Dynamic page title via WS message, ~30 lines total
8. **`dj-click-away`** — Click outside handler, ~20 lines JS
9. **`dj-lock`** — Prevent concurrent event execution, ~30 lines JS
10. **`dj-page-loading`** — NProgress-style loading bar, ~40 lines JS + 10 lines CSS
11. **Native `<dialog>` integration** — `dj-dialog="open|close"`, ~20 lines JS
12. **`dj-no-submit`** — Prevent enter-key form submission, ~10 lines JS
13. **`page_loading` on `dj.push`** — Trigger loading bar during heavy events, ~15 lines JS
14. **`dj-scroll-into-view`** — Auto-scroll element into viewport on render, ~25 lines JS

#### Medium Effort (1-3 days)
14. **`self.defer(callback)`** — Post-render work scheduling, ~40 lines Python
15. **`dj-shortcut`** — Keyboard shortcut binding, ~60 lines JS
15. **`dj-debounce`/`dj-throttle` HTML attributes** — Client-side timer per element, ~50 lines JS
16. **`on_mount` hooks** — Cross-cutting mount logic, ~100 lines Python
17. **Flash messages** — `FlashMixin` + `{% dj_flash %}` + client JS auto-dismiss
18. **`handle_params` callback** — URL param change handler, ~50 lines Python
19. **`dj-mounted`** — Element entered DOM event, ~30 lines JS
20. **`dj-sticky-scroll`** — Auto-scroll chat/log containers, ~40 lines JS
21. **`dj-lazy` viewport loading** — Lazy component rendering, ~40 lines JS
22. **Multi-tab sync** — BroadcastChannel API integration, ~60 lines JS
23. **View Transitions API** — Animated page transitions, ~60 lines JS
24a. **`dj-paste`** — Paste event handling (text + images), ~40 lines JS
24. **`dj-viewport-top`/`dj-viewport-bottom`** — Bidirectional infinite scroll, ~60 lines JS + ~40 lines Python
25. **`used_input?` (server-side feedback)** — Server-side field touched tracking, ~40 lines Python + ~10 lines JS
26. **Programmable JS Commands from hooks** — Expose DJ command API to dj-hook callbacks, ~60 lines JS
27. **Stable component IDs** — Deterministic `self.unique_id()` for ARIA/label matching, ~30 lines Python
28. **Dirty tracking** — `self.changed_fields` / `self.is_dirty` for conditional save UX, ~60 lines Python
29. **`dj-ignore-attrs`** — Prevent VDOM from overwriting client-owned attributes, ~20 lines JS

#### Major Features
30. **JS Commands** — Biggest DX win; needs Python builder + client JS executor
30. ~~**VDOM structural patching** (#559)~~ ✅ Fixed in PR #563
31. **Function components** — Stateless render functions with Rust engine support, ~150 lines Python + Rust
32. **`assign_async`/`AsyncResult`** — High-level async data loading, ~200 lines Python
33. **`handle_async` callback** — Typed async completion handler (Phoenix 1.0 parity), ~80 lines Python
34. **Declarative component assigns** — Type-checked attrs with defaults/validation, ~120 lines Python
35. **LiveView testing utilities** — `assert_push_event`, `assert_patch`, `render_async`, ~300 lines Python
36. **Error overlay (dev mode)** — In-browser Python stack traces, ~100 lines Python + ~80 lines JS
37. **Template fragments** — Rust-side static subtree fingerprinting for wire-size optimization
38. **Connection multiplexing** — Share one WS across multiple LiveViews, ~200 lines JS + Python
39. **Rust template engine parity** — Close the model attribute access gap
40. **AI streaming primitives** — Purpose-built LLM streaming components
41. **Streaming initial render** — Chunked HTTP response with progressive content loading
42. **Django admin LiveView widgets** — Real-time admin dashboards and inline editing
43. **Hot View Replacement** — State-preserving Python code reload in dev mode, ~200 lines Python
44. **Server Actions (`@action`)** — React 19-style mutation handlers with auto pending/error states
45. **Keyed for-loop change tracking** — Rust-side per-item change detection in `{% for %}` loops, ~200 lines Rust
46. **Type-safe template validation** — Static analysis matching template vars to view context, ~200 lines Python + Rust
47. **Streaming markdown renderer** — Incremental Rust-side CommonMark parser for LLM streaming, ~500 lines Rust
48. **Keep-Alive / `dj-activity`** — Pre-render hidden routes with preserved state (React 19.2 parity), ~150 lines Python + ~60 lines JS
49. **Database change notifications** — PostgreSQL LISTEN/NOTIFY → LiveView push, ~150 lines Python
50. **Virtual/windowed lists** — DOM virtualization for large lists, ~120 lines JS + ~30 lines Python
51. **Multi-step wizard (`WizardMixin`)** — Per-step validation, URL sync, progress, ~200 lines Python + template tags
52. **i18n live language switching** — Switch locale without page reload, ~60 lines Python

#### Always Welcome
45. **Starter templates** — Build example apps that showcase djust patterns
46. **Documentation** — Improve guides, fix gaps, add cookbook recipes
47. **Test coverage** — Edge cases in VDOM diffing, WebSocket reconnection, state backends

Open an issue or discussion to propose features or ask questions.
