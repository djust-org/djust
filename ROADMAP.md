# djust Roadmap

## ✅ v0.3.0 "Phoenix Rising" - Complete

Major feature release introducing PWA and multi-tenant capabilities:

- **Progressive Web App Support** — Complete offline-first implementation with service worker integration, IndexedDB/LocalStorage abstraction, optimistic UI updates, and offline-aware template directives. Includes 8 PWA template tags, PWA mixins, and automatic sync.
- **Multi-Tenant Architecture** — Production-ready SaaS support with flexible tenant resolution (subdomain, path, header, session, custom), automatic data isolation, tenant-aware state backends, and comprehensive template integration.
- **114 New Tests** — Comprehensive test coverage (53 PWA, 61 multi-tenant) with full CI integration.
- **Complete Documentation** — New guides for PWA and multi-tenant development, updated API references.

## 1. Profile & Improve Performance

Identify bottlenecks in the render cycle and optimize.

- Profile the full request path: HTTP render → WebSocket mount → event → VDOM diff → patch
- Use existing benchmarks in `tests/benchmarks/` (serialization, e2e render, tags, templates) as baselines
- Instrument key paths with `performance.py` timing trees and `profiler.py` `@profile` decorator
- Determine whether Rust VDOM diffs or Python serialization/template rendering is the bottleneck
- Profile `state_backend.py` — compression overhead for states >10KB, Redis round-trip latency
- Target: <2ms per patch, <5ms for list updates

## 2. Investigate Session/State Storage

Understand memory pressure and evaluate client-side or external storage.

**Current architecture** (`state_backends/`):

- Pluggable backends: memory (dev) or Redis (prod)
- Server memory holds: RustLiveView instance, user context, cached decorator values, draft state
- States >100KB trigger warnings; >10KB get zstd compressed
- TTL-based expiration with `cleanup_liveview_sessions` management command

**Questions to answer:**

- Can template context be reconstructed from DB rather than stored in memory/Redis?
- Can any state move client-side (signed cookies, JWT)?
- What is the Redis serialization cost vs memory backend? Is there a hybrid approach?
- What is a typical session size and how does it scale with concurrent users?

## 3. TurboNav Integration

Make djust + TurboNav a first-class documented pattern.

**Issues found and fixed:**

- Injected scripts need `data-turbo-track="reload"` for `loadPageScripts` to pick them up
- Inline `<script>` tags inside `<main>` require explicit execution after `innerHTML` swap
- `DOMContentLoaded` doesn't fire on dynamically loaded scripts — must check `document.readyState`

**Remaining work:**

- Document the contract: TurboNav swaps `<main>` innerHTML, `loadPageScripts` handles tracked scripts
- Fix triple-initialization on navigation (console shows 3 rounds of client.js init logs)
- Guard against duplicate WebSocket connections on repeated navigation
- Decide: should TurboNav ship with djust or remain a separate integration concern?
- Write a guide for integrating djust LiveViews into existing Django sites using TurboNav

## 4. Evaluate & Improve Developer Experience

Lower the barrier to getting started and debugging.

- Docs are extensive (40+ files) but scattered — consolidate the getting-started path
- CLI (`cli.py`) has `stats`, `health`, `profile`, `analyze`, `clear` — evaluate discoverability
- ~~Error messages from event security now surface in the debug toolbar — verify clarity and usefulness~~ ✅ Done (#112)
- Identify common first-time stumbling blocks: missing `@event_handler` decorator? WebSocket/Channels config? Template syntax?
- ~~Document `@event` → `@event_handler` migration path~~ ✅ Done (#122, #141)
- Consider a `django-admin startapp` template with djust boilerplate
- Consider better error pages in DEBUG mode with actionable suggestions

## 5. Break Up Large Files — ✅ Complete

All major files have been split into focused modules:

| File                  | Status                                           | PRs              |
| --------------------- | ------------------------------------------------ | ---------------- |
| `debug-panel.js`      | ✅ Split into source modules                      | #125             |
| `client.js`           | ✅ Split into source modules                      | #124             |
| `live_view.py`        | ✅ Extracted serialization, session utils, mixins | #126, #127, #130 |
| `websocket.py`        | ✅ Extracted websocket_utils                      | #129             |
| `state_backend.py`    | ✅ Split into state_backends package              | #123             |
| `template_backend.py` | ✅ Split into template package                    | #128             |

## 6. Finish Debug Toolbar

Complete the development tools suite.

**Existing (working):**

- Event handlers tab with parameter/decorator inspection
- Event history (last 50 events) with timing and error details
- VDOM patches tab with operation logging and timing
- Variables tab with current values and types
- Keyboard shortcut (Cmd+Shift+D / Ctrl+Shift+D)
- Toast notifications and error overlay
- Hot reload with template change detection
- Performance warnings for patches >16ms
- Server error display in toolbar (#112)

**Missing:**

- Event filtering by handler name, success/error status, time range
- Event replay — resend previous events with same parameters
- Network tab — raw WebSocket message inspection, connection status monitoring
- Performance warnings tab — slow patches (>5ms), state size alerts, memory tracking
- State size visualization — session contents and size breakdown
- Panel state persistence across TurboNav navigation

## 7. WebSocket Security Hardening — ✅ Complete

All critical and high-priority WebSocket security issues have been resolved:

- ✅ Rust actor path bypass (#106, #118, #120)
- ✅ Rate limiting for mount/ping messages (#107)
- ✅ Per-IP connection limit and reconnection throttle (#108, #121)
- ✅ Error disclosure leak prevention (#109)
- ✅ Config validation on startup (#110)
- ✅ Message size byte-count check (#111)
- ✅ Shared `_validate_event_security` helper (#120)

## 8. VDOM Correctness

Ongoing effort to harden the VDOM diff and patch pipeline.

**Resolved:**

- ✅ Keyed diff insert ordering (#152, #154)
- ✅ MoveChild resolution via djust_id (#150)
- ✅ Duplicate key detection warning (#145, #149)
- ✅ data-djust-replace child removal (#142, #143, #144)
- ✅ Unkeyed list reorder documentation (#148, #151)
- ✅ Proptest/fuzzing for diff algorithm (#146, #153)
- ✅ JIT serialization fixes for M2M, nested dicts, @property (#140)

**Remaining:**

- Investigate edge cases surfaced by proptest fuzzing
- Performance optimization for large list diffs (>1000 items)

## 9. Service Worker Enhancements

Leverage the service worker beyond offline support to improve perceived performance, resilience, and navigation speed for djust LiveView applications.

See [docs/guides/sw-enhancements.md](docs/guides/sw-enhancements.md) for full architecture and implementation details.

**Phase 1 — Quick Wins (v0.4.0):**

- **Prefetch on Hover** — Prefetch internal pages on `pointerenter`, serving them from cache on click for near-instant navigation
- **Smart Static Asset Caching** — Pre-cache djust JS/CSS/icons at SW install; extend `generate_sw` to auto-populate asset lists from `collectstatic`

**Phase 2 — Core Improvements (v0.4.x):**

- **Instant Page Shell** — Cache the page shell (head, nav, footer) and serve instantly on navigation; swap `<main>` content when server responds
- **WebSocket Reconnection Bridge** — Buffer LiveView events in the SW during WebSocket disconnection; replay in order on reconnect

**Phase 3 — Advanced Features (v0.5.0):**

- **VDOM Patch Caching** — Cache last rendered DOM state per page; on back-navigation, serve cached state and diff against fresh server response
- **LiveView State Snapshots** — Serialize LiveView state on unmount; restore on back-navigation for instant state recovery
- **Request Batching** — Batch parallel HTTP requests from multiple components into a single server round-trip

## 10. Framework Portability (Flask/FastAPI Support)

Explore making djust's core available beyond Django.

**Already framework-agnostic (Rust crates):**

- `djust_vdom` — VDOM diffing, HTML parsing, patch generation (zero Django coupling)
- `djust_templates` — Template rendering with abstract `TemplateLoader` trait
- `djust_core` — Value types, context management, serialization

**Django-coupled (Python layer):**

- `websocket.py` — inherits `channels.AsyncWebsocketConsumer`
- `routing.py` — uses `django.urls.path`
- `live_view.py` — inherits `django.views.View`
- `mixins/template.py` — uses `django.template.loader`

**What a per-framework adapter would need (~800-1500 lines each):**

- WebSocket handler (Starlette `WebSocket` for FastAPI, Quart for Flask)
- Route registration adapter
- View base class (plain Python, no Django inheritance)
- Template loader (Jinja2 via `TemplateLoader` trait, or use Rust engine directly)
- Session/state bridge (the `StateBackend` ABC is already framework-agnostic)

**Status**: Not started. Gauging community interest via [GitHub Discussions](https://github.com/johnrtipton/djust/discussions). The Rust crates are architecturally ready to be published as standalone PyPI packages.
