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
| **P1** | ✅ `manage.py djust_gen_live` scaffolding | Phoenix's generators are the #1 onboarding DX feature; scaffold views/templates/tests from a model | v0.4.0 |
| **P1** | ✅ Transition/priority updates | React 18/19 `startTransition` concept — mark re-renders as low-priority so user events always win | v0.4.0 |
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
| ~~**P0**~~ | ~~`push_commands` + `djust:exec` auto-executor~~ ✅ ([ADR-002](docs/adr/002-backend-driven-ui-automation.md) Phase 1a) | ~~Foundation primitive for every backend-driven UI feature in ADRs 002-006~~ | v0.4.2 |
| ~~**P0**~~ | ~~`wait_for_event` async primitive~~ ✅ ([ADR-002](docs/adr/002-backend-driven-ui-automation.md) Phase 1b) | ~~Lets background handlers pause until real user actions — required for TutorialMixin~~ | v0.4.2 |
| ~~**P0**~~ | ~~`TutorialMixin` + `{% tutorial_bubble %}`~~ ✅ ([ADR-002](docs/adr/002-backend-driven-ui-automation.md) Phase 1c) | ~~Declarative guided tours with zero custom JS — v0.4.2 headline feature~~ | v0.4.2 |
| ~~**P1**~~ | ~~Scaffold `DEBUG=False` default + `.env.example`~~ ✅ (#637) | ~~Security-adjacent carry-over; fails-safe default complements A014 static check~~ | v0.4.2 |
| ~~**P1**~~ | ~~Defer `reinitAfterDOMUpdate` for pre-rendered mount~~ ✅ (#619) | ~~Visible layout-flash bugfix carried over from v0.4.1~~ | v0.4.2 |
| ~~**P3**~~ | ~~Dependabot batch carry-over (v0.4.2)~~ ✅ | ~~Vitest/jsdom/tokio/indexmap/etc. — single "ci: bump deps" PR~~ | v0.4.2 |
| ~~**P1**~~ | ~~Private `_` attributes wiped between WebSocket events (#627)~~ ✅ | ~~Core state management broken — any `_private` attr is lost after each event~~ | v0.4.2 |
| ~~**P1**~~ | ~~Pre-rendered WS reconnect drops `_private` attributes (#611)~~ ✅ | ~~State loss on reconnect after HTTP GET pre-render — related to #627~~ | v0.4.2 |
| ~~**P1**~~ | ~~VDOM patcher calls element methods on text nodes (#622)~~ ✅ | ~~`setAttribute`/`appendChild` crash on `#text` nodes — breaks conditional rendering~~ | v0.4.2 |
| ~~**P1**~~ | ~~`as_live_field()` ignores `widget.attrs` (#683)~~ ✅ | ~~Form fields lose `type`, `placeholder`, `pattern` — forms DX broken~~ | v0.4.2 |
| ~~**P2**~~ | ~~`form.cleaned_data` Python types serialized to null (#628)~~ ✅ | ~~`date`, `Decimal`, `UUID` in cleaned_data become `null` in public state~~ | v0.4.2 |
| ~~**P2**~~ | ~~`set()` not JSON-serializable as public state (#626)~~ ✅ | ~~`set` in view state crashes serialization — common Python type~~ | v0.4.2 |
| ~~**P2**~~ | ~~`dict` state deserialized as `list` after Rust sync (#612)~~ ✅ | ~~Round-trip through Rust state sync corrupts dict → list~~ | v0.4.2 |
| ~~**P2**~~ | ~~VDOM patcher should handle `autofocus` on inserted elements (#617)~~ ✅ | ~~Dynamically inserted inputs don't receive focus even with `autofocus` attr~~ | v0.4.2 |
| ~~**P2**~~ | ~~Debug panel SVG attributes double-escaped (#613)~~ ✅ | ~~`viewBox`, `path d` attributes rendered garbled in the debug toolbar~~ | v0.4.2 |
| ~~**P3**~~ | ~~docs: `data-*` attribute naming convention undocumented (#623)~~ ✅ | ~~How `data-foo-bar` maps to `foo_bar` event params — every new user asks~~ | v0.4.2 |
| ~~**P3**~~ | ~~chore: reduce system check noise — T002, V008, C003 (#603)~~ ✅ | ~~Noisy checks on every `manage.py` invocation annoy developers~~ | v0.4.2 |
| **P2** | Fold `djust-auth` + `djust-tenants` into core ([ADR-007](docs/adr/007-package-taxonomy-and-consolidation.md) Phase 1) | Eliminate theoretical-audience package fragmentation; extras pattern + compat shim | v0.5.0 |
| **P2** | Fold `djust-theming` into core ([ADR-007](docs/adr/007-package-taxonomy-and-consolidation.md) Phase 2) | Unified CSS/theming story with core; compat shim for plain-Django users | v0.5.1 |
| **P2** | Fold `djust-components` into core ([ADR-007](docs/adr/007-package-taxonomy-and-consolidation.md) Phase 3) | Largest fold — 64K LOC — dedicated release window in v0.5.2 | v0.5.2 |
| **P2** | Consolidation sunset — remove compat shims ([ADR-007](docs/adr/007-package-taxonomy-and-consolidation.md) Phase 4) | Archive standalone packages on PyPI; one full minor cycle of compat is sufficient | v0.6.0 |
| **P1** | `broadcast_commands` + multi-user sync ([ADR-002](docs/adr/002-backend-driven-ui-automation.md) Phase 4) | Instructor → students UI sync in a single primitive; novel for Python frameworks | v0.5.x |
| **P1** | Consent envelope for remote control ([ADR-005](docs/adr/005-consent-envelope-for-remote-control.md)) | Security-critical primitive for support handoffs, accessibility caregivers, AI assist | v0.5.x |
| **P0** | `AssistantMixin` + LLM provider abstraction ([ADR-002](docs/adr/002-backend-driven-ui-automation.md) Phase 5, [ADR-003](docs/adr/003-llm-provider-abstraction.md), [ADR-004](docs/adr/004-undo-for-llm-driven-actions.md)) | Voice/chat-driven djust apps; market window is ~12 months; largest revenue angle | v0.5.x |
| **P0** | AI-generated UIs with capture-and-promote ([ADR-006](docs/adr/006-ai-generated-uis-with-capture-and-promote.md)) | "User builds an app with an LLM" — v0.6.0 headline feature; lossless export to Python | v0.6.0 |

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

*Goal:* Make djust reliable enough that developers don't hit surprising breakage in normal use. Fix the sharp edges that make new users bounce. *Scope intentionally trimmed from the previous 28-feature v0.4.0 — ship the must-haves, then iterate. JS Commands moved to v0.4.1.*

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

**Connection state CSS classes** ✅ — Auto-apply `dj-connected` / `dj-disconnected` CSS classes to the body element based on WebSocket/SSE state. Phoenix does this with `phx-connected`/`phx-disconnected` — trivial to implement, big DX win for showing connection status without custom JS.

**`dj-confirm` attribute** — ✅ Already implemented in `09-event-binding.js`. Native browser confirmation dialog before executing an event.

**`dj-disable-with` attribute** ✅ — Automatically disable submit buttons and replace text during form submission. `<button type="submit" dj-disable-with="Saving...">Save</button>`. Prevents double-submit and provides instant visual feedback. Phoenix's `phx-disable-with` is one of its most-loved small features.

**`dj-key` attribute** — ✅ Already implemented. Keyed VDOM diff with LIS optimization.

**Window/document event scoping** ✅ — `dj-window-keydown`, `dj-window-scroll`, `dj-document-click` attributes for binding events to `window` or `document` rather than the element itself. Phoenix has `phx-window-*`. Essential for keyboard shortcuts, infinite scroll triggers, and click-outside-to-close patterns.

**`dj-debounce` / `dj-throttle` as HTML attributes** ✅ — Currently debounce/throttle only works as Python decorators on event handlers, applying the same delay to every caller. Phoenix allows per-element control: `<input dj-change="search" dj-debounce="300">` vs `<select dj-change="filter" dj-debounce="0">`. This is strictly more flexible — the Python decorator becomes the default, the attribute becomes the override. Implementation: client-side timer per element+event pair, ~50 lines of JS.

**`live_title` & document metadata** ✅ — Update `<title>` and `<meta>` tags from the server without a page reload. Phoenix's `live_title_tag` is trivial but surprisingly impactful — it enables unread counts, status indicators, and notification badges in browser tabs. React 19 went further with native document metadata support (title, link, meta hoisted to `<head>` automatically). API: `self.page_title = "Chat (3 unread)"` and `self.page_meta = {"description": "...", "og:image": "..."}` in any event handler, sent as a lightweight WS message that updates `document.title` and `<meta>` tags without a VDOM diff. The meta tag support is especially valuable for SPAs that need dynamic Open Graph tags for link previews. ~50 lines total.

**`dj-mounted` event** ✅ — Fire a server event when an element enters the DOM (after VDOM patch inserts it). Use cases: scroll-into-view for new chat messages, trigger data loading when a tab becomes active, animate elements on appearance. Phoenix has `phx-mounted`. Pairs naturally with `dj-remove` (exit event). Uses a WeakSet in `bindLiveViewEvents()` to detect newly-added elements after VDOM patches (not initial page load).

**`dj-click-away`** ✅ — Fire an event when the user clicks outside an element. `<div dj-click-away="close_dropdown">`. This is the single most common pattern developers manually implement in every interactive app (dropdowns, modals, popovers). Currently requires `dj-window-click` + manual coordinate checking or a JS hook. One attribute, ~20 lines of JS, eliminates boilerplate in every project.

**`dj-lock` — Prevent concurrent event execution** ✅ — Disable an element until its event handler completes. `<button dj-click="save" dj-lock>Save</button>` prevents double-clicks and concurrent submissions. Different from `dj-disable-with` (which is cosmetic) — `dj-lock` actually blocks the event from firing again until the server acknowledges completion. Phoenix handles this implicitly via its event acknowledgment protocol. Uses `data-djust-locked` marker attribute and `disabled` for form elements or `djust-locked` CSS class for non-form elements. All locked elements unlocked on server response.

**`dj-auto-recover` — Custom reconnection recovery** ✅ — Fires a custom server event on WebSocket reconnect instead of the default form-value replay. `<div dj-auto-recover="restore_state">`. Use case: views with complex state (drag positions, canvas state, multi-step wizard progress) that can't be recovered from form values alone. The handler receives `params` with whatever the client can serialize from the DOM. Phoenix's `phx-auto-recover` solves the same problem — not every reconnection fits the "replay form values" pattern.

**`dj-value-*` — Static event parameters** — Pass static values alongside events without `data-*` attributes or hidden inputs. `<button dj-click="delete" dj-value-id="{{ item.id }}" dj-value-type="soft">Delete</button>` sends `{"id": "42", "type": "soft"}` as params. Phoenix's `phx-value-*` is used everywhere — it's the standard way to pass context with events. Currently djust requires either `data-*` attributes (which the client must extract) or hidden form fields. This is ~20 lines of JS (collect `dj-value-*` attributes on the trigger element and merge into event params) but eliminates boilerplate in every template. *This is arguably the single most underrated Phoenix feature — once developers have it, they use it on every event.*

**`handle_params` callback** ✅ PR #567 (2026-03-18) — Invoked when URL parameters change via `live_patch` or browser navigation. Phoenix's `handle_params/3` is the standard pattern for URL-driven state (pagination, filters, search, tab selection). Currently, `live_patch` updates the URL but there's no server-side callback to react to the change — developers must manually parse `request.GET` in event handlers. API: `def handle_params(self, params, url, **kwargs)` called after `mount()` on initial render and on every subsequent URL change. This enables bookmark-friendly state: users can share URLs like `/dashboard?tab=metrics&range=7d` and the view reconstructs itself from params. ~50 lines Python. *Without this, `live_patch` is only half-implemented — you can push URLs but can't react to them.*

**`dj-shortcut` — Keyboard shortcut binding** ✅ — Declarative keyboard shortcuts on any element. `<div dj-shortcut="ctrl+k:open_search, escape:close_modal">`. Use cases: command palettes (`Ctrl+K`), close modals (`Escape`), save (`Ctrl+S`), undo (`Ctrl+Z`), navigation (`j`/`k` for list items). Currently requires `dj-window-keydown` + manual key checking in Python event handlers — a round-trip for every keypress. `dj-shortcut` handles matching client-side and only fires the event on match. Supports modifier keys (`ctrl`, `shift`, `alt`, `meta`), key combos, and `prevent` modifier to suppress browser defaults (`dj-shortcut="ctrl+s:save" dj-shortcut-prevent`). ~60 lines of JS. *Every productivity app needs keyboard shortcuts. React developers use `react-hotkeys-hook`; this is the built-in equivalent.*

**`dj-copy` — Copy to clipboard** ✅ — Copy text content to clipboard on click without a server round-trip. `<button dj-copy="#code-block">Copy</button>` copies the text content of `#code-block`. `<button dj-copy="literal text here">Copy</button>` copies the literal string. Optionally fires a server event for analytics: `dj-copy="#code-block" dj-copy-event="copied"`. Shows visual feedback (configurable CSS class, default: `dj-copied` for 2s). Use cases: code snippets, share links, API keys, referral codes. Currently requires a `dj-hook` for every copy button. ~30 lines of JS. *This is the kind of small built-in that makes developers think "this framework gets it" — every documentation site, every admin panel needs copy buttons.*

**`dj-cloak` — Prevent flash of unstyled content** ✅ — Elements with `dj-cloak` are hidden (`display: none !important`) until the WebSocket/SSE mount response is received. CSS is injected automatically by client.js. Vue has `v-cloak`, Alpine has `x-cloak` — this is expected in any framework that enhances server-rendered HTML.

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

**`_target` param in form change events** ✅ — When multiple fields share one `dj-change="validate"` handler, the `_target` parameter identifies which field triggered the change. Essential for efficient per-field validation without needing separate handlers per field. The client includes the triggering element's `name` (or `id`, or `null`) as `_target` in the event params for `dj-change`, `dj-input`, and `dj-submit` (submitter button name). Matches Phoenix LiveView's `_target` convention.

**`dj-scroll-into-view` — Auto-scroll to element on render** ✅ — Elements with `dj-scroll-into-view` are automatically scrolled into view after DOM updates (mount, VDOM patch). Supports scroll behavior via attribute value: `""` (smooth/nearest), `"instant"`, `"center"`, `"start"`, `"end"`. One-shot per DOM node via WeakSet tracking. VDOM-replaced fresh nodes scroll again correctly. Use cases: chat messages, form validation errors, notification toasts.

**`dj-page-loading` — Navigation loading bar** ✅ — NProgress-style thin loading bar at the top of the page during TurboNav and `live_redirect` navigation. Always active by default. Exposed as `window.djust.pageLoading` with `start()`, `finish()`, and `enabled` for manual control. Disable via `window.djust.pageLoading.enabled = false` or CSS override.

**Flash messages (promoted from v0.5.0)** — Built-in ephemeral notification pattern with `self.put_flash(level, message)` and auto-dismissing client-side rendering. Phoenix's `put_flash` is used in virtually every app. *Promoted to v0.4.0 because this is the #1 pattern developers reinvent in every project. A `FlashMixin` with `put_flash('info', 'Saved!')`, a `{% dj_flash %}` template tag, and ~40 lines of client JS for appear/auto-dismiss animations. Flash messages survive `live_patch` but clear on `live_redirect`. Without this, every djust app ships with a slightly different homegrown toast system.*

#### Transition / Priority Updates (React 18/19 `startTransition` concept)

**✅ Priority-aware event queue** *(completed v0.4.0)* — Server-initiated broadcasts (`server_push`) and async completions (`_run_async_work`) are now tagged with `source="broadcast"` and `source="async"` respectively, and the client buffers them during pending user event round-trips (same as tick buffering from #560). `server_push` acquires the render lock and yields to in-progress user events to prevent version interleaving. Client-side pending event tracking upgraded from single ref to `Set`-based tracking, supporting multiple concurrent pending events. Buffer flushes only when all pending events resolve.

#### Scaffolding

**✅ `manage.py djust_gen_live` — Model-to-LiveView scaffolding** *(completed v0.4.0)* — Phoenix's `mix phx.gen.live` is the #1 onboarding accelerator: give it a model and it generates a LiveView, templates, and tests for CRUD operations in seconds. djust has the MCP server for AI-assisted scaffolding, but a CLI command is essential for developers who aren't using AI tools. `manage.py djust_gen_live posts Post title:string body:text published:boolean` generates: (1) a LiveView class with `mount()`, `handle_event()` for create/edit/delete, (2) index/show/form templates with `dj-model` bindings and `dj-submit`, (3) URL patterns via `live_session()`, (4) test file with `LiveViewTestClient` smoke tests. Respects the project's existing patterns (detects whether the project uses function-based or class-based views, which CSS framework, etc.). Optional `--no-tests`, `--api` (JSON responses), `--belongs-to=User` flags. ~400 lines Python management command + Jinja2 templates. *Every framework with fast adoption has a generator: Rails scaffold, Phoenix gen.live, Laravel make:livewire. This is how new developers go from "installed" to "productive" in under 5 minutes. The MCP server is great for AI-assisted dev, but the CLI command is the universal onramp.*

#### Developer Tooling

~~**Error message quality**~~ ✅ — VDOM patch errors now include patch type, `dj-id`, parent element info, and suggested causes. WebSocket `send_error` includes `debug_detail`, `traceback`, and `hint` in DEBUG mode. Debug panel intercepts `[LiveView]` warnings and shows a badge.

~~**`manage.py djust_doctor`**~~ ✅ — Single diagnostic command checking 12 items: djust/Python/Django versions, Rust extension, Channels, ASGI, channel layers, Redis, template dirs, Rust render, static files, ASGI server. Supports `--json`, `--quiet`, `--check NAME`, `--verbose`.

~~**Latency simulator**~~ ✅ — Debug panel latency controls with presets (Off/50/100/200/500ms), custom value, jitter, localStorage persistence. Injected on both WebSocket send and receive. Badge on debug button shows active latency.

**Profile & improve performance** — Use existing benchmarks in `tests/benchmarks/` as baselines. Profile the full request path: HTTP render, WebSocket mount, event, VDOM diff, patch. Target: <2ms per patch, <5ms for list updates.

#### Reconnection Resilience

~~**Form recovery on reconnect**~~ ✅ — After WebSocket reconnects, the client auto-fires `dj-change` with current DOM form values to restore server state. Compares DOM values against server-rendered defaults, skips unchanged fields. Supports `dj-no-recover` opt-out and defers to `dj-auto-recover` containers.

~~**Reconnection backoff with jitter**~~ ✅ — Exponential backoff with random jitter (AWS full-jitter strategy). Min 500ms, max 30s, 10 attempts. Reconnection banner with attempt count, `data-dj-reconnect-attempt` attribute and `--dj-reconnect-attempt` CSS custom property on `<body>`.

### Milestone: v0.4.1 — Security Hardening, JS Commands & Interaction Polish

*Goal:* Close the biggest DX gap vs Phoenix (JS Commands), ship the remaining quick wins that didn't fit in v0.4.0's bug-fix focus, and fix security findings from the 2026-04-10 penetration test (#653, #654, #655, plus the #657/#659/#660/#661 audit-enhancement batch).

*Status (2026-04-11):* Security hardening batch complete (#653/#654/#655 shipped). Audit-enhancement batch complete (#657/#659/#660/#661 all shipped). `{% live_input %}` (#650) shipped. `dj-paste` shipped (PR #671). JS Commands (P1) shipped (PR #672) — full 11-command suite, template/hook/JS/attribute entry points, scoped targets (`to`/`inner`/`closest`), immutable chains, `push` with `page_loading`. v0.4.1 is feature-complete; only any v0.4.0 leftover quick wins remain before release.

#### Security hardening (from pentest findings 2026-04-10)

**✅ Reject cross-origin WebSocket connections by default (#653, CSWSH)** — Shipped as PR #658 (merged 2026-04-10). ⚠️ **High priority.** `djust.websocket.LiveViewConsumer.connect()` calls `self.accept()` without validating the `Origin` header, and no djust helper (`DjustMiddlewareStack`, `live_session`, the scaffold `asgi.py` template) wraps the router in `channels.security.websocket.AllowedHostsOriginValidator`. Every djust application is vulnerable to Cross-Site WebSocket Hijacking by default — any page on the internet can mount a LiveView in a victim's browser, dispatch events, and read VDOM patches back. Demonstrated against a live deployment via `websockets.connect(TARGET, origin="https://evil.example")`. Three complementary fixes: (1) Add an Origin check to `LiveViewConsumer.connect()` that rejects with `close(code=4403)` when the Origin host is not in `settings.ALLOWED_HOSTS` (empty Origin is allowed to keep curl/test scripts working). (2) Update `DjustMiddlewareStack` in `djust/routing.py` to wrap in `AllowedHostsOriginValidator` by default, with an opt-out kwarg for apps that truly need cross-origin access. (3) Update the `ASGI_PY` template in `djust/scaffolding/templates.py` to include origin validation in generated `asgi.py` files. Release notes must call out the interaction with `ALLOWED_HOSTS = ["*"]` (the validator respects `*` as explicit opt-out). ~40 lines Python. *CWE-346, CWE-942, OWASP WSTG-INPV-11. This is the single highest-impact security fix in v0.4.1.*

**✅ Gate VDOM patch timing/performance metadata behind `DEBUG` (#654)** — Shipped as PR #663 (merged 2026-04-10). Every `patch` response from `LiveViewConsumer` previously included `timing` (handler/render/total ms) and `performance` (full nested timing tree with handler names, phase names, durations, and warnings) — unconditionally, regardless of `settings.DEBUG`. This leaks server-side code path structure to any client including unauthenticated cross-origin attackers (see #653). Enables timing-based code path differentiation (DB hit vs cache miss, valid vs invalid CSRF), internal structure disclosure, and load-based DoS timing. Fix: gate both emissions on `settings.DEBUG or getattr(settings, "DJUST_EXPOSE_TIMING", False)` in `djust/websocket.py` around lines 629-640 and line 719. Keep the existing behavior in debug mode so the browser debug panel still gets its data. Add a test asserting that `timing`/`performance` keys are absent from patch responses when `DEBUG=False`. ~15 lines Python + test. *Medium severity alone, but paired with #653 this becomes a real reconnaissance primitive.*

**✅ Nonce-based CSP support — drop `'unsafe-inline'` from `script-src` / `style-src` (#655)** — Shipped as PR #664 (merged 2026-04-10). Low priority enhancement. djust apps currently must allow `'unsafe-inline'` in `CSP_SCRIPT_SRC` and `CSP_STYLE_SRC` because djust's client runtime bootstrap and `djust-theming`'s dynamic `<style>` injection don't carry CSP nonces. This negates most of CSP's XSS defense. Three changes: (1) `djust-theming` — inline `<style>` tags emitted for theme variables accept and render a nonce from `request.csp_nonce` when `django-csp` middleware is active. (2) djust client runtime — wherever the bootstrap `<script>` is emitted (likely a `{% djust_client %}` template tag or the theme head), apply the same nonce. (3) Scaffold `settings.py` defaults — once nonces are supported, update the generated CSP settings to use `CSP_INCLUDE_NONCE_IN = ("script-src", "script-src-elem", "style-src", "style-src-elem")` and drop `'unsafe-inline'`. Requires `django-csp>=4.0`. Document the upgrade path for existing apps in release notes. ~30 lines Python across djust + djust-theming + scaffold templates. *Hardening, not a live vulnerability — but closes the biggest remaining CSP gap for djust apps handling sensitive data.*

#### `djust_audit` enhancements (pentest follow-ups)

The same 2026-04-10 pentest that surfaced #653/#654/#655 also surfaced a broader observation: several of the 17 findings would have been catchable at CI time by an enhanced audit tool. Four follow-up issues extend `djust_audit` with new checkers and modes, each filed separately so they can land incrementally. All four share context (pentest source analysis) but no implementation complexity, so they're grouped here but scoped individually.

**✅ Declarative permissions document for `djust_audit` (#657)** — Shipped as PR #665 (merged 2026-04-11). Adds a `--permissions <file>` flag that validates every LiveView against a committed YAML/TOML permissions document. `djust_audit` today can tell "no auth at all" from "some auth is set," but it cannot tell whether `login_required=True` should have been `permission_required("claims.view_supervisor_dashboard")`. The pentest found that every claim detail view in the NYC Claims app had `login_required=True` and djust_audit reported them all as "protected," but the lowest-privilege authenticated user could still read every claim by ID walk. The fix is to make the expected permission model an **auditable artifact**: a `permissions.yaml` at the project root that lists every view with its expected `public: true` / `roles: [...]` / `permissions: [...]` config, and `djust_audit --permissions permissions.yaml --strict` fails CI on any deviation (undeclared view, mismatched config, or code-level auth that contradicts the document). ~200 lines Python for the parser + validator + diff reporter. *The missing RBAC audit primitive — lets security reviewers sign off on the permission model once and have CI enforce it forever.*

**✅ `djust_audit` — ASGI stack, config, and misc static security checks (#659)** — Shipped as PR #666 (merged 2026-04-11). Seven static check IDs added: A001 (ASGI origin validator), A010/A011/A012 (ALLOWED_HOSTS footguns), A014 (insecure SECRET_KEY), A020 (hardcoded login redirect + multi-group), A030 (admin without brute-force protection). Manifest scanning (k8s/helm/docker-compose) remains out of scope and will land in a follow-up. Four cheap, high-signal static checks added as a batch: (A) ASGI stack validator — parses `asgi.py` to check that the `"websocket"` entry is wrapped in `AllowedHostsOriginValidator` (static-analysis companion to #653 for existing apps not yet rebuilt from the new scaffold). (B) Configuration audit — catches `ALLOWED_HOSTS` footguns, missing `SECURE_PROXY_SSL_HEADER` behind proxies, `DEBUG=True` shipped to prod via `os.environ.get("DEBUG", "True")`, unbounded `CSRF_TRUSTED_ORIGINS`. (C) Misc middleware ordering checks — `SecurityMiddleware` before `CommonMiddleware`, `csp.middleware.CSPMiddleware` present when `CSP_*` settings exist. (D) Recognize djust helper signatures (`djust.routing.live_session`, `DjustMiddlewareStack`) so the ASGI validator handles indirect ASGI app construction. Each check ~15-100 lines Python with essentially zero false-positive risk. *Catches the subset of pentest findings that live in config, not user code.*

**✅ `djust_audit` — AST-based security anti-pattern scanner (#660)** — Shipped as PR #670 (merged 2026-04-10). Seven stable finding codes added under a new `X0xx` prefix so they coexist with the existing `P0xx` permissions-document codes from #657. X001 (IDOR), X002 (unauthenticated state-mutating handler), X003 (SQL string formatting), X004 (open redirect), X005 (unsafe `mark_safe`), X006 (template `|safe`), X007 (template `{% autoescape off %}`). Suppression via `# djust: noqa XNNN` on the offending Python line or `{# djust: noqa XNNN #}` inside templates. New CLI flags: `--ast`, `--ast-path`, `--ast-exclude`, `--ast-no-templates`. Supports `--json` and `--strict`. 52 new tests covering positive + negative cases for every checker, noqa suppression, and management-command integration. ~720 lines Python in `python/djust/audit_ast.py`. Closes the v0.4.1 audit-enhancement batch.

**✅ `djust_audit --live <url>` — runtime security header and WebSocket probe (#661)** — Shipped as PR #667 (merged 2026-04-11). 30 stable finding codes djust.L001–L091 for headers, cookies, path probes, WebSocket CSWSH probe, and connectivity. Zero new runtime dependencies (stdlib urllib + optional websockets package). Add a `--live <url>` mode (or a separate `djust_live_audit` command) that fetches an actual HTTP response from a running deployment and verifies security headers, plus opens a WebSocket handshake with a bogus Origin to verify CSWSH defense end-to-end. This catches the class of issues that **static analysis cannot see** — middleware correctly configured in `settings.py` but the response is stripped, rewritten, or never emitted by the time it reaches the client. The source pentest caught a critical CSP misconfiguration where `django-csp` was correctly configured but the `Content-Security-Policy` header was completely absent from production responses (stripped by an nginx ingress annotation). Validates `Strict-Transport-Security`, `Content-Security-Policy`, `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy`, `Permissions-Policy`, and probes `wss://<host>/ws/` with `Origin: https://evil.example` to confirm the server closes the handshake. Modes: basic, `--json --strict` (CI-friendly), `--paths` (multi-URL), `--no-websocket-probe`. ~250 lines Python. *The only way to catch config-drift between source and production. Two-second feedback loop vs waiting for a pentest.*

#### Feature / polish work

**✅ JS Commands (`dj.push`, `dj.show`, `dj.hide`, `dj.toggle`, `dj.addClass`, `dj.removeClass`, `dj.transition`, `dj.dispatch`, `dj.focus`, `dj.set_attr`, `dj.remove_attr`)** — Shipped as PR #672 (merged 2026-04-11). All 11 commands available from four entry points: (1) Python helper `djust.js.JS` fluent chain builder that stringifies to a JSON command list wrapped in `SafeString`; (2) client-side `window.djust.js` mirror with `camelCase` method names; (3) hook API `this.js()` returning a chain bound to the hook element; (4) attribute dispatcher — `dj-click` detects JSON command lists and executes them locally without a server round-trip. 37 Python tests + 30 JS tests. Full guide in `docs/website/guides/js-commands.md`.

**✅ Programmable JS Commands from hooks (Phoenix 1.0 parity)** — Shipped as part of PR #672. Every `dj-hook` instance has a `this.js()` method that returns a fresh `JSChain`; call `.exec(this.el)` to run it against the hook's element.

**✅ `to: {:inner, selector}` and `to: {:closest, selector}` JS Command targets (Phoenix 1.0 parity)** — Shipped as part of PR #672. Every command accepts at most one of `to=` (absolute selector), `inner=` (scoped to origin descendants), `closest=` (walk up from origin). A single `<button dj-click="{{ JS.hide(closest='.modal') }}">Close</button>` works in every modal with no per-instance IDs.

**✅ `page_loading` option on `dj.push` (Phoenix 1.0 parity)** — Shipped as part of PR #672. `JS.push('generate_report', page_loading=True)` triggers `dj-page-loading` elements during the server round-trip.

**✅ `dj-paste` — Paste event handling** — Shipped as PR #671 (merged 2026-04-11). Fires a server event when the user pastes content (text, images, files) into an element. `<textarea dj-paste="handle_paste">`. The client extracts paste payload: plain text via `clipboardData.getData('text/plain')`, rich HTML via `getData('text/html')`, and file metadata via `clipboardData.files`. Sends structured params: `{"text": "...", "html": "...", "has_files": true, "files": [{name, type, size}, ...]}`. When combined with `dj-upload="<slot>"`, clipboard files are auto-routed through the upload pipeline via a new `window.djust.uploads.queueClipboardFiles(element, fileList)` export. Native paste still happens by default; add `dj-paste-suppress` to intercept fully. Participates in `dj-confirm` / `dj-lock`. 11 JS tests. ~80 lines JS. Docs: `docs/website/guides/dj-paste.md`.

**✅ Standalone `{% live_input %}` template tag for non-form state (#650)** — Shipped as PR #668 (merged 2026-04-11). All 10 design points from the PR #652 review delivered: dedicated tag name, explicit `event=` kwarg, single HTML builder path via new `djust._html.build_tag`, field-type registry, `name=` default from handler, CSS class via `config.get_framework_class('field_class')`, full XSS test matrix, `docs/guides/live-input.md` guide, `debounce=`/`throttle=` forwarding, no `data-field_name` (one handler per field). 12 supported field types. `FormMixin.as_live_field()` and `WizardMixin.as_live_field()` render form fields with proper CSS classes and `dj-input`/`dj-change` bindings for views backed by a Django `Form` class. But non-form views — modals, inline panels, settings pages, search boxes, filter bars, toggles, anywhere state lives directly on view attributes — have no equivalent ergonomic helper. Developers write raw `<input class="form-input" dj-input="set_x" value="{{ x }}">` by hand, forget the class, or use inconsistent event bindings. This is the 80% of UI state that doesn't need a full `forms.Form`. *(GitHub issue #650 tracks the user-facing feature request — claim notes panel, reclassification modal, settlement offer modal, and every other inline form in the NYC Claims app currently uses raw HTML. #650 and the `{% live_input %}` plan below are the same feature from two sides: the user ask and the implementation design.)*

*PR #652 explored an initial implementation by overloading the existing `{% live_field %}` tag with a field-type string as its first argument, dispatching to a standalone path when the first arg is a known type. On review we decided that design has several problems worth fixing before shipping, so that PR is closed and the work will restart cleanly for v0.4.1.*

**Design notes for the clean-slate implementation** (captured from the PR #652 review so we don't re-discover them):

1. **New tag, not an overload.** Use a dedicated `{% live_input %}` (or `{% live_state %}`) tag instead of overloading `{% live_field %}`. The existing `{% live_field %}` stays as the Form-based path that expects `(view, field_name)`. A new tag name makes the call site visually unambiguous at the template and decouples the supported field-type set from argument-dispatch logic — adding a new type is a dict entry, not a change to parsing heuristics.

2. **Explicit event override.** Accept an `event=` kwarg so the caller can opt into `dj-input` (per-keystroke), `dj-change` (blur/selection), or `dj-blur`. Default sensibly per type (`text/textarea` → `dj-input`, `select` → `dj-change`), but never force the caller to bail out of the tag just because they want debounced text or a validate-on-blur select. Pairs naturally with `debounce=`/`throttle=` kwargs that forward to `dj-debounce`/`dj-throttle` attributes already supported in 0.4.0.

3. **Single source of HTML building.** Don't reimplement the escape-by-hand attribute builder. `frameworks.py` has `_build_tag(tag, attrs, content)` which centralises attribute escaping via `django.utils.html.escape`. Either import it directly or promote it to a shared `djust._html` module. Two escape paths is how XSS regressions happen.

4. **Field-type registry, not a hardcoded set.** Define field types as a dict of `{name: render_fn}` so adding a new type (`checkbox`, `number`, `date`, `datetime-local`, `hidden`, `radio`, `range`, `color`) is a one-line registration. Each render function takes `(handler, value, css_class, **kwargs)` and returns an HTML string. First-class types at launch: `text`, `textarea`, `select`, `password`, `email`, `number`, `url`, `tel`, `checkbox`, `radio`, `hidden`. Use cases mapped to types documented in the guide.

5. **Emit `name` attribute by default.** Derive from the handler name or accept an explicit `name=` kwarg. Without a `name`, no-JS form submission doesn't work as a fallback, which is a hidden degradation for users on slow connections / JS failures.

6. **CSS class resolution.** Use `config.get_framework_class("field_class")` (already used by the Form-based path) so Bootstrap/Tailwind/Plain configs are honoured. Fall back to `"form-input"` only if config lookup fails. Narrow the exception catch in the fallback — `except (ImportError, AttributeError)` not bare `Exception`.

7. **XSS test matrix.** Every field type needs a test that injects `<script>alert(1)</script>` into (a) the value, (b) custom kwargs (placeholder, aria-label, title), and (c) choice labels for `select`/`radio`. This is cheap and catches 99% of future regressions.

8. **User-facing documentation.** `docs/website/guides/forms.md` (or a new `guides/state-bound-fields.md`) with a full example showing: a modal with a `{% live_input %}` subject + body + type-select, the corresponding event handlers (`set_subject`, `set_body`, `set_type`), and when to reach for `{% live_input %}` vs `FormMixin` vs `WizardMixin`.

9. **Integration with `dj-debounce`/`dj-throttle` shipped in 0.4.0.** `{% live_input "text" handler="search" debounce="300" %}` should just work by passing `dj-debounce="300"` through.

10. **Conservative decision on `data-field_name`.** The Form-based path emits `data-field_name="..."` so a single validate handler can serve many fields. The standalone path has one handler per field, so `data-field_name` is not strictly needed — but worth documenting the omission so users migrating from `FormMixin` know what changes.

*Ships the ergonomic primitive developers actually want for the 80% of UI state that doesn't need a Django Form — toggles, search inputs, inline editors, modal fields.*

**Remaining v0.4.0 quick wins** — Any items from the v0.4.0 quick wins list that didn't ship in the initial release ship here. (`dj-lock`, `dj-mounted`, `dj-shortcut`, `dj-click-away`, window/document event scoping, connection CSS, `dj-cloak`, `dj-page-loading`, `dj-scroll-into-view`, `dj-copy`, `dj-auto-recover`, `dj-debounce`/`dj-throttle`, and `live_title`/document metadata shipped in v0.4.0.)

### Milestone: v0.4.2 — Backend-Driven UI (Phase 1) & Carry-Over Fixes

*Goal:* Land the MVP of backend-driven UI automation (`push_commands`, `wait_for_event`, `TutorialMixin`) so server-side Python can declaratively drive the browser through guided flows. Fix the open bug backlog: state management bugs (#627, #611), VDOM patcher issues (#622, #617), serialization hardening (#628, #626, #612), forms (#683), debug panel (#613). Clean up docs (#623) and noisy system checks (#603). Ship the dependabot batch and carry-over fixes from v0.4.1.

*Execution order:* The BDUI features shipped first as 1a → 1b → 1c (dependency chain). The remaining bug fixes, docs, and chores are independent — pipeline runners can use `--all --group` to batch related issues (e.g. the serialization cluster #628/#626/#612 ships as one PR, the state management pair #627/#611 ships as one PR).

**✅ `push_commands(chain)` + client-side `djust:exec` auto-executor ([ADR-002](docs/adr/002-backend-driven-ui-automation.md) Phase 1a)** — Shipped. The foundation primitive for every backend-driven UI feature in ADRs 002-006. Adds `self.push_commands(chain)` as a one-line server-side helper that pushes a `JSChain` (v0.4.1) to the current session for immediate execution. Paired with a new ~40-line `djust:exec` auto-executor (framework-provided, no hook needed) registered automatically on every page — users don't write any client code to consume it. In single-user mode the chain is sent via `push_event("djust:exec", {"ops": chain.ops})` over the current WebSocket; presence-group broadcasting lands later in Phase 4. The client auto-executor calls `window.djust.js._executeOps(ops, null)` from v0.4.1's JS Commands module on every payload. Includes: 1 Python module (`djust/server_driven/mixin.py`), 1 JS module (`python/djust/static/djust/src/27-exec-listener.js`), test harness for push-commands round-trip, and one worked example on djust.org's counter demo ("drive it from the server" button that runs a 5-step narration + highlight tour). Branch: `feat/push-commands-server-driven`. *~20 lines Python + 15 lines JS + ~50 lines tests + 1 docs page. Tiny feature, biggest leverage — unblocks everything else.*

**✅ `wait_for_event` async primitive ([ADR-002](docs/adr/002-backend-driven-ui-automation.md) Phase 1b)** — Shipped. Depends on Phase 1a. Adds `await self.wait_for_event(name, timeout, predicate)` as an async primitive for pausing a `@background` handler until the user performs a specific action. The handler suspends on an `asyncio.Event` latch registered in the LiveView's event dispatch layer; when an `@event_handler`-decorated method with the matching name is called, the normal handler runs AND the latch resolves with the handler's kwargs. Optional `predicate(kwargs) -> bool` filter lets callers wait for "the user clicks *this specific* button." Optional `timeout` raises `asyncio.TimeoutError` on elapsed. Required by `TutorialMixin` (Phase 1c) for "wait for the user to actually click Next" without polling. Implementation: ~40 lines in `djust/server_driven/waiters.py` + integration with `djust/live_view.py`'s existing event dispatch + ~80 lines tests (happy path, timeout, predicate, cancellation, concurrent waits). Branch: `feat/wait-for-event-async-primitive`. *~40 lines implementation + tests. Small feature but structurally tricky because it touches the event dispatch path.*

**✅ `TutorialMixin` + `TutorialStep` + `{% tutorial_bubble %}` template tag ([ADR-002](docs/adr/002-backend-driven-ui-automation.md) Phase 1c)** — Shipped. Depends on Phases 1a and 1b. The headline feature of v0.4.2. Ships a declarative state machine for guided tours: apps describe the tutorial as a list of `TutorialStep` dataclasses (target selector, message, position, wait_for event name, optional on_enter/on_exit chains, optional auto-advance timeout), mix in `TutorialMixin`, and call `start_tutorial()` from any event handler. The mixin runs the steps in order: for each step it pushes a "highlight + narrate" chain (add class, show bubble, position it, set message text, focus for accessibility), awaits the `wait_for` event (via `wait_for_event`) or sleeps for the step's timeout, then cleans up the highlight and advances. Ships a default `{% tutorial_bubble %}` template tag so users don't have to style their own overlay unless they want to — honours `config.get_framework_class()` for Bootstrap/Tailwind/Plain apps. Includes: `djust/tutorials/mixin.py`, `djust/tutorials/step.py`, `djust/templatetags/djust_tutorials.py`, `~150 lines tests (happy path, skip, cancel, timeout per step, on_enter/on_exit chains), and a djust.org homepage tour demo (7 steps showing features, captured as the first example app). Branch: `feat/tutorial-mixin`. *~200 lines Python + template tag + tests + demo. The v0.4.2 headline — user-facing, marketable, demoable.*

**✅ #637 — Scaffold defaults `DEBUG=False` and generates `.env.example`** — Shipped. Carry-over bugfix from v0.4.1. Independent of the BDUI track; can ship in parallel. Rebased and cleaned up: the original PR bundled scaffold changes with stale `client.js` edits that would regress #625 and stale `debug-panel.js` edits that duplicate #633. Ship only the scaffold slice: `python/djust/scaffolding/generator.py` + `python/djust/scaffolding/templates.py` + new `.env.example` template. Close the original PR #637 as superseded. Fails-safe default (`DEBUG = os.environ.get("DEBUG", "False")...`, `ALLOWED_HOSTS` from env) complements the A014/A001 static checks from #666. Branch: `fix/scaffold-debug-default-637`. *~30 lines Python. 1-2 days.*

**✅ #619 — Defer `reinitAfterDOMUpdate` via `requestAnimationFrame` on pre-rendered mount** — Shipped. Carry-over bugfix from v0.4.1. Independent of the BDUI track; can ship in parallel. Rebase onto current main, edit `python/djust/static/djust/src/03-websocket.js` to wrap the post-mount block in a `requestAnimationFrame` callback (with synchronous fallback when rAF is unavailable for JSDOM tests), preserve the ordering invariant so form recovery still runs after event binding is complete, rebuild `client.js`. Includes 148-line regression test file `tests/js/mount-deferred-reinit.test.js`. Fixes visible layout-flash on pre-rendered HTTP GET content. Branch: `fix/defer-reinit-after-dom-update-619`. *~30 lines JS (source) + rebuild + 148 lines tests. 1-2 days.*

**✅ Dependabot batch carry-over** — Shipped. Independent chore work that was held behind the v0.4.1 release. Ship as a single "ci: bump deps" PR: Vitest 4.1.0 → 4.1.4, `@vitest/ui` + `@vitest/coverage-v8` to match, jsdom 29.0.1 → 29.0.2, happy-dom, tokio 1.50 → 1.51, indexmap 2.13.0 → 2.13.1, proptest, uuid, html5ever, release-drafter, github-script, astral-sh/setup-uv. Full test suite gates the merge. Branch: `chore/dependabot-batch-v042`. *15 deps in one PR. 1 day.*

#### Open issues added to v0.4.2

**✅ #627 — Private `_` attributes wiped between WebSocket events** — Shipped. Root cause: session save used `get_context_data()` output which strips `_`-prefixed attrs. Fix adds `_get_private_state()`/`_restore_private_state()` helpers and wires them into session persistence. 20 new regression tests. Branch: `fix/private-attr-preservation`.

**✅ #611 — Pre-rendered WS reconnect drops `_private` attributes, skipping `mount()`** — Shipped (same PR as #627 — shared root cause). The reconnect path in `RequestMixin._restore_session_state()` now restores private attrs from the `_private_state` session key before the view resumes. Branch: `fix/private-attr-preservation`.

**✅ #622 — VDOM patcher calls element methods on text nodes** — Shipped. The patcher now guards all 5 affected patch types (setAttribute, removeAttribute, appendChild, removeChild, replaceChild) with an `isElement()` check, skipping gracefully on text/comment nodes. Branch: `fix/vdom-patcher-text-nodes-autofocus`.

**✅ #683 — `as_live_field()` ignores `widget.attrs` (type, placeholder, pattern)** — Shipped. `BaseAdapter._merge_widget_attrs()` now merges `field.widget.attrs` into the rendered HTML for all field types (input, textarea, select, checkbox, radio), with djust-specific attributes taking precedence. Branch: `fix/as-live-field-widget-attrs-683`.

~~**#628 — `form.cleaned_data` Python types (date, Decimal) serialized to null**~~ ✅ — Fixed: `DjangoJSONEncoder` and `normalize_django_value()` already handled these types; added 10 regression tests to lock in the behavior. Branch: `fix/serialization-hardening`.

~~**#626 — `set()` not JSON-serializable as public LiveView state**~~ ✅ — Fixed: extended both `DjangoJSONEncoder.default()` and `normalize_django_value()` to serialize `set`/`frozenset` as sorted lists. 11 regression tests. Branch: `fix/serialization-hardening`.

~~**#612 — `dict` state attributes deserialized as `list` after Rust state sync**~~ ✅ — Fixed: replaced `#[serde(untagged)]` derived `Deserialize` on `Value` with a custom visitor-based implementation that uses `visit_map`/`visit_seq` to correctly distinguish maps from arrays in MessagePack. 4 Rust + 1 Python regression tests. Branch: `fix/serialization-hardening`.

**✅ #617 — VDOM patcher should handle `autofocus` on inserted elements** — Shipped. The patcher now detects `autofocus` on newly inserted elements after each patch cycle and calls `.focus()` explicitly. Branch: `fix/vdom-patcher-text-nodes-autofocus`.

**✅ #613 — Debug panel SVG attributes double-escaped** — Shipped. The Rust VDOM's `to_html()` was HTML-escaping text inside `<script>`/`<style>` raw text elements, corrupting JS/CSS code on roundtrip. Fix: `_to_html(in_raw_text)` skips escaping for raw text element children. Branch: `fix/debug-svg-escape-613`.

~~**#623 — docs: `data-*` attribute naming convention for event handler params not documented**~~ ✅ — Documented in Events guide: dash-to-underscore rule, type-hint suffixes, `dj-value-*` alternative, quick-reference table. Shipped in `chore/docs-and-checks-cleanup`.

~~**#603 — chore: reduce system check noise — T002, V008, C003**~~ ✅ — Added `suppress_checks` config key to `DJUST_CONFIG`/`LIVEVIEW_CONFIG`. Accepts short (`"T002"`) or qualified (`"djust.T002"`) IDs, case-insensitive. Only Info-level variants are suppressible. 7 new tests. Shipped in `chore/docs-and-checks-cleanup`.

### Milestone: v0.5.0 — Async Loading, Core Components, Streams & Package Consolidation

*Goal:* Ship the async data loading and core component primitives that production apps need. Scope intentionally trimmed — DX features (testing, error overlay, computed state) moved to v0.5.1. Begin the package consolidation work from ADR-007 by folding the two smallest runtime packages into core.

**Package consolidation: fold `djust-auth` and `djust-tenants` into core ([ADR-007](docs/adr/007-package-taxonomy-and-consolidation.md))** — Phase 1 of the three-phase consolidation. Move `djust_auth/` → `djust.auth` and `djust_tenants/` → `djust.tenants`, expose as `djust[auth]` / `djust[auth-oauth]` / `djust[tenants]` / `djust[tenants-redis]` / `djust[tenants-postgres]` extras. Ship final `djust-auth 0.4.0` and `djust-tenants 0.4.0` standalone releases as thin compat shims that re-export from the new locations with a `DeprecationWarning`. Smallest fold (670 + 1,900 LOC total), ~2-3 days of work. Proves out the migration process ahead of the larger `djust-theming` and `djust-components` folds in v0.5.1 and v0.5.2.

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

*Goal:* Make djust a joy to develop with. Ship the testing utilities, error overlay, form patterns, and computed state that transform the daily development experience. These were split from v0.5.0 to ship the core async/component primitives faster.

**Package consolidation: fold `djust-theming` into core ([ADR-007](docs/adr/007-package-taxonomy-and-consolidation.md))** — Phase 2 of the three-phase consolidation. Move `djust_theming/` (~37.6K LOC, 139 Python files) → `djust.theming`, update Django app config from `djust_theming.apps.DjustThemingConfig` → `djust.theming.apps.DjustThemingConfig`, migrate CSS generator / design tokens / component CSS generator / gallery / context processors / template tags. Expose as `djust[theming]` extra. Test migration is the biggest chunk — theming's test suite is substantial and needs careful preservation. Ship `djust-theming 0.5.0` as a compat shim. ~1-2 weeks of focused work. Required for v0.5.2 to cleanly consume theming internals from the `djust.components` fold.

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

### Milestone: v0.5.2 — Package Consolidation: Components

*Goal:* Complete the runtime package consolidation from [ADR-007](docs/adr/007-package-taxonomy-and-consolidation.md) with the largest fold (`djust-components`). Intentionally scoped as a single-feature release so the migration gets focused review bandwidth without competing with feature work. Deliberately placed between v0.5.1 and v0.6.0 so the full v0.5.x cycle has cleanly unified auth, tenants, theming, and components extras by the time generative UIs ship.

**Package consolidation: fold `djust-components` into core ([ADR-007](docs/adr/007-package-taxonomy-and-consolidation.md) Phase 3)** — Largest fold of the three-phase consolidation. Move `djust-components/components/` (~64.2K LOC, 307 Python files) → `djust.components`. Migrate CSS assets, icon libraries, template tag registration, component-showcase assets. The `component_showcase/` demo app is NOT folded — it moves to `examples.djust.org` or stays as a demo repo. `markdown>=3.0` and `nh3>=0.2` become dependencies of the new `djust[components]` extra rather than core. Test migration is the biggest chunk — components' test suite is the largest of the folded packages. Ship `djust-components 0.5.0` as a compat shim that re-exports from `djust.components` with a `DeprecationWarning`. ~2-3 weeks of focused work; scoping this as its own milestone acknowledges the risk and gives it room to land cleanly.

### Milestone: v0.6.0 — Production Hardening, Interactivity & Generative UIs

*Goal:* Make djust production-ready for teams deploying real apps, close the remaining interactivity gap with client-side frameworks, and ship the capture-and-promote generative UI story as the headline feature.

**Package consolidation sunset ([ADR-007](docs/adr/007-package-taxonomy-and-consolidation.md) Phase 4)** — Complete the three-phase consolidation by removing the compatibility shims shipped in v0.5.0 / v0.5.1 / v0.5.2. Archive the standalone `djust-auth`, `djust-tenants`, `djust-theming`, and `djust-components` packages on PyPI (still installable, no new releases). Update their READMEs to point users at `pip install djust[auth]` / `djust[tenants]` / `djust[theming]` / `djust[components]`. Delete the `djust_auth/`, `djust_tenants/`, `djust_theming/`, `djust_components/` shim modules from the standalone repos. One full minor cycle of compatibility (v0.5.x) is enough for users to migrate; removing the shims in v0.6.0 is clean closure, not rush.

**AI-generated UIs with capture-and-promote ([ADR-006](docs/adr/006-ai-generated-uis-with-capture-and-promote.md))** — v0.6.0 headline feature and the natural follow-through from the v0.5.x AI work. Users can chat with an assistant to compose UIs from a vetted component library, iterate through conversation, save drafts, publish them as real routed djust views, and optionally export them to idiomatic Python source for developer customization. Four phased deliverables: (A) `@ai_composable` decorator + `CompositionDocument` schema + `GenerativeMixin` with ephemeral generation; (B) `GeneratedView` model + draft capture lifecycle + drafts panel; (C) publish-and-version flow with URL routing, version history, diff/rollback/fork; (D) Python export generator producing idiomatic LiveView code with zero runtime dependency on the generative layer. The feature is deliberately structured as "LLM composes validated documents" not "LLM writes code" — the composition document is a strict recursive JSON that the framework renders through the same VDOM pipeline as every other djust view. All twelve captured-view threats (prompt injection, data exfiltration, storage quota, cost exploitation, stale bindings, tampering, DoS, accessibility regression, IP ambiguity, cross-tenant leakage, pathological compositions, poisoned component dependencies) have documented mitigations. Eight new A060-A067 system checks. Integrates with `AssistantMixin` from v0.5.x so the generative tool is just another entry in the LLM's tool schema. *~9 weeks total across four subphases; each phase is independently shippable and useful. The "user builds an app with an LLM by prompting for designs and capturing them" use case is the primary user story. Competes with Retool AI / Hex / v0 / Claude Artifacts on DX but beats them structurally on lock-in, state integration, and export.*


**Animations & transitions** — Declarative `dj-transition` attribute for enter/leave CSS transitions with three-phase class application (start → active → end), matching Phoenix's `JS.transition`. `dj-remove` attribute for exit animations before element removal. FLIP technique for list reordering animations. `dj-transition-group` for animating list items entering/leaving (React's `<TransitionGroup>` / Vue's `<transition-group>` equivalent — essential for todo lists, kanban boards, search results). Skeleton/shimmer loading state components. *(View Transitions API integration promoted to v0.5.0.)*

**Sticky LiveViews** — Mark a LiveView as `sticky=True` in `live_render()` to keep it alive across live navigations. Use case: persistent audio/video player, sidebar, notification center. The sticky view doesn't unmount/remount when the user navigates — it stays connected and retains state. Phoenix added this and it's a big win for app-shell patterns.

**`dj-mutation` — DOM mutation events** — Fire a server event when specific DOM attributes or children change via MutationObserver. `<div dj-mutation="handle_change" dj-mutation-attr="class,style">`. Use case: third-party JS libraries (charts, maps, rich text editors) that modify the DOM outside djust's control — the server needs to know about these changes to keep state in sync. Currently requires a custom `dj-hook` for every integration. One declarative attribute replaces boilerplate in every third-party-widget integration. Implementation: MutationObserver config from attributes, debounced event push. ~50 lines of JS.

**`dj-sticky-scroll` — Auto-scroll preservation** — Automatically keep a scrollable container pinned to the bottom when new content is appended (chat messages, logs, terminal output), but stop auto-scrolling if the user scrolls up to read history. Resume auto-scroll when they scroll back to bottom. This is the #1 asked-for behavior in chat and log-viewer apps and currently requires a custom `dj-hook` with scroll position math. `<div dj-sticky-scroll>` handles it declaratively. ~40 lines of JS.

**`dj-track-static` — Static asset change detection (Phoenix `phx-track-static` parity)** — Mark `<link>` and `<script>` tags with `dj-track-static` to record their fingerprinted URLs at mount time. On WebSocket reconnect, the client compares current asset URLs against the stored versions. If any have changed (server deployed new code while the user was disconnected), show a prompt: "App updated — click to reload" or auto-reload based on configuration. Without this, users on long-lived WebSocket connections silently run stale JavaScript after a deploy — the server sends new HTML referencing new JS bundles, but the client still has the old JS loaded. This causes subtle breakage that's impossible to debug. Phoenix has had `phx-track-static` since 0.15 and it's used on every production deploy. Implementation: client stores a hash of `[dj-track-static]` element `src`/`href` values on connect; on reconnect, re-hash and compare. If different, fire a `dj:stale-assets` event (or auto-reload if `dj-track-static="reload"` is set). ~30 lines JS + ~10 lines Python template tag. *This is a production-critical feature that every deployed app needs. Without it, zero-downtime deploys are a myth — you get zero downtime on the server but broken behavior on connected clients.*

**WebSocket per-message compression (permessage-deflate)** — Enable `permessage-deflate` WebSocket extension for automatic compression of all WS messages. VDOM patches are highly compressible (repetitive HTML fragments, JSON structure) — typical compression ratios of 60-80% reduction in wire size. Django Channels/Daphne supports this via configuration; djust needs to: (1) enable the extension in the consumer, (2) ensure the client negotiates it, (3) document the memory tradeoff (each connection holds a zlib context, ~64KB per connection — fine for most deployments, configurable via `DJUST_WS_COMPRESSION = True/False`). Implementation: ~20 lines of consumer configuration + documentation. *This is the single cheapest bandwidth optimization available — just turning it on reduces WebSocket traffic by 60-80% with no code changes. Combined with template fragments (which reduce what's sent), compression reduces the wire format of what remains. Every production deployment should have this; it should be on by default.*

**Runtime layout switching** — Change the base layout template during a LiveView session without a full page reload. `self.set_layout('layouts/fullscreen.html')` in an event handler swaps the surrounding layout (nav, sidebar, footer) while preserving the inner LiveView state. Use cases: toggle between admin layout and public layout, switch to fullscreen mode for a presentation or editor, show a minimal layout during onboarding then switch to the full app layout. Phoenix 1.1 added runtime layout support. Implementation: the layout is rendered server-side and sent as a special WS message; the client replaces everything outside `[data-djust-root]`. ~80 lines Python + ~30 lines JS. *This is how real apps work — layouts aren't static. A document editor that goes fullscreen, a dashboard that hides the sidebar, an onboarding flow that uses a minimal layout then switches to the app layout on completion. Without runtime layout switching, these patterns require a full page reload, losing all state.*

**Advanced service worker features** — VDOM patch caching (cache last rendered DOM per page; diff against fresh response on back-navigation). LiveView state snapshots (serialize on unmount, restore on back-nav). Request batching for multi-component pages.

### Milestone: v0.7.0 — Navigation, Smart Rendering & AI Patterns

*Goal:* Make navigation feel like a SPA and establish djust as the best framework for AI-powered applications.

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

**Django admin LiveView widgets** — Drop-in LiveView-powered widgets for Django's admin interface. `DjustAdminMixin` on any `ModelAdmin` enables real-time dashboards, live search/filter, inline editing, and bulk action progress within the admin. Use cases: real-time order status dashboards, live log viewers, monitoring panels, AI-powered admin actions with streaming output. This is a unique djust differentiator — no other LiveView-style framework integrates with an existing admin like Django's. Implementation: admin template overrides + a `DjustAdminWidget` base class that renders a mini LiveView inside admin change forms/list views. ~300 lines Python. *Django's admin is used by 90%+ of Django projects. Making it reactive with zero config is the single most effective demo of djust's value proposition — "add one mixin and your admin goes live."*

**Prefetch on hover/intent** — Pre-load the next page's data when the user hovers over a link or shows navigation intent (mouse movement toward link, touch start). `<a dj-prefetch href="/dashboard">Dashboard</a>` triggers a lightweight prefetch request on hover, so the page loads instantly on click. Different from existing `22-prefetch.js` (which pre-fetches all visible links) — this is intent-based and targeted. Remix, Next.js, and Astro all use hover-prefetch as their primary strategy for fast navigation. Implementation: `mouseenter` listener with 65ms delay (avoids prefetch on fly-over), prefetch via `<link rel="prefetch">` or fetch API with abort on `mouseleave`. ~50 lines JS. *Combined with View Transitions API, this makes navigation feel literally instant — the page is already loaded before the user clicks.*

**Server functions (RPC-style calls, promoted from post-v0.7.0 consideration)** — Call server-side Python functions from client JS and get structured results back, without defining an event handler or managing state. `const result = await djust.call('search_users', {query: 'john'})` invokes a decorated Python function and returns JSON. Different from event handlers (which trigger re-renders) — server functions are pure request/response, ideal for typeahead suggestions, autocomplete, validation checks, and any pattern where you need data but don't want a full re-render. React Server Actions and tRPC popularized this pattern. API: `@server_function` decorator on view methods, client-side `djust.call()` with promise return. ~100 lines Python + ~30 lines JS.

### Milestone: v0.8.0 — Server Actions, Async Streams & Form Patterns (NEW)

*Goal:* Bridge the gap between Phoenix 1.0's async primitives and React 19's server actions model. Make djust the most ergonomic framework for forms, data mutation, and async data flows.

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
| **Lock (prevent double-fire)** | **Event ack protocol** | — | **Not started** | **v0.4.0** |
| **Auto-recover (custom)** | **`phx-auto-recover`** | — | **Not started** | **v0.4.0** |
| **Cloak (FOUC prevention)** | — | **`v-cloak` (Vue)** | **Not started** | **v0.4.0** |
| **`on_mount` hooks** | **`on_mount/1`** | — | **Not started** | **v0.4.0** |
| **Flash messages** | **`put_flash/3`** | **Toast libraries** | **Not started** | **v0.4.0** |
| ~~Latency simulator~~ | Built-in | — | ✅ **Done** | v0.4.0 |
| ~~Keyboard shortcuts~~ | — | ~~`react-hotkeys-hook`~~ | ✅ **Done** | v0.4.0 |
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
| ~~**Document metadata**~~ | ~~`live_title`~~ | ~~**Native** (React 19)~~ | ✅ **Done** | v0.4.0 |
| **Type-safe template validation** | — | TypeScript | **Not started** | **v0.5.1** |
| **Streaming markdown renderer** | — | — | **Not started** | **v0.7.0** |
| **DB change notifications** | **PubSub + Ecto** | — | **Not started** | **v0.5.0** |
| **Virtual/windowed lists** | — | **`react-window`** | **Not started** | **v0.5.0** |
| **Multi-step wizard** | — | **`react-hook-form`** | **Not started** | **v0.5.1** |
| **Paste event handling** | — | **`onPaste`** | **Not started** | **v0.4.1** |
| ~~**Standalone `{% live_input %}` template tag**~~ | — | — | ✅ **Shipped (#650, PR #668)** | v0.4.1 |
| ~~**WebSocket Origin validation (CSWSH fix)**~~ | ~~`check_origin/2`~~ | — | ✅ **Shipped (#653, PR #658)** | v0.4.1 |
| ~~**Gate `timing`/`performance` on DEBUG**~~ | — | — | ✅ **Shipped (#654, PR #663)** | v0.4.1 |
| ~~**Nonce-based CSP support**~~ | — | ~~React nonce~~ | ✅ **Shipped (#655, PR #664)** | v0.4.1 |
| ~~**`djust_audit` declarative permissions (`--permissions`)**~~ | — | — | ✅ **Shipped (#657, PR #665)** | v0.4.1 |
| ~~**`djust_audit` ASGI stack + config static checks**~~ | — | — | ✅ **Shipped (#659, PR #666)** | v0.4.1 |
| ~~**`djust_audit` AST-based anti-pattern scanner**~~ | — | — | ✅ **Shipped (#660, PR #670)** | v0.4.1 |
| ~~**`djust_audit --live` runtime header probe**~~ | — | — | ✅ **Shipped (#661, PR #667)** | v0.4.1 |
| **Scroll into view** | — | **`scrollIntoView`** | **Not started** | **v0.4.0** |
| **WS compression** | **Built-in (Cowboy)** | — | **Not started** | **v0.6.0** |
| **Runtime layout switching** | **Runtime layouts (1.1)** | — | **Not started** | **v0.6.0** |
| **i18n live switching** | — | — | **Not started** | **v0.7.0** |

---

## Contributing

Want to help? See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

High-impact areas for contributions:

#### Quick Wins (< 1 day, great first contributions)
1. ~~**`dj-value-*` static params**~~ ✅
2. ~~**`_target` param in change events**~~ ✅
3. ~~**`dj-disable-with`**~~ ✅
4. ~~**Connection state CSS classes**~~ ✅
5. ~~**`dj-copy`**~~ ✅
6. ~~**`dj-cloak`**~~ ✅
7. ~~**`live_title`**~~ ✅
8. ~~**`dj-click-away`**~~ ✅
9. ~~**`dj-lock`**~~ ✅
10. ~~**`dj-page-loading`**~~ ✅
11. **Native `<dialog>` integration** — `dj-dialog="open|close"`, ~20 lines JS
12. **`dj-no-submit`** — Prevent enter-key form submission, ~10 lines JS
13. **`page_loading` on `dj.push`** — Trigger loading bar during heavy events, ~15 lines JS
14. ~~**`dj-scroll-into-view`**~~ ✅

#### Medium Effort (1-3 days)
14. **`self.defer(callback)`** — Post-render work scheduling, ~40 lines Python
15. ~~**`dj-shortcut`**~~ ✅
15. ~~**`dj-debounce`/`dj-throttle` HTML attributes**~~ ✅
16. **`on_mount` hooks** — Cross-cutting mount logic, ~100 lines Python
17. **Flash messages** — `FlashMixin` + `{% dj_flash %}` + client JS auto-dismiss
18. **`handle_params` callback** — URL param change handler, ~50 lines Python
19. ~~**`dj-mounted`**~~ ✅
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
