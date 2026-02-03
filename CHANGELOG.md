# Changelog

All notable changes to djust will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.6.0] - 2026-02-03 — "Universal Access" 🌐

A major release expanding djust's reach with TypeScript support, SSE alternative, accessibility features, mobile/touch gestures, and Django Admin integration.

### Added

#### TypeScript Definitions
- **Full `.d.ts` type coverage** — Type definitions for `djust.js`, `client.js`, and all public APIs
- **Exported types** — `DjustLiveView`, `DjustEvent`, `PatchOperation`, hook interfaces
- **VS Code autocomplete** — Full IntelliSense support for djust client-side APIs

#### Server-Sent Events (SSE)
- **`SSEView`** — New view class for server-push-only use cases without WebSocket infrastructure
- **`SSEMixin`** — Add SSE capabilities to existing views
- **`dj-sse` directive** — Subscribe DOM elements to SSE channels
- **`@sse_event` decorator** — Mark methods as SSE event generators
- **No Channels required** — SSE works over standard HTTP, simplifying deployment

#### Accessibility (ARIA)
- **`AccessibilityMixin`** — Auto-inject ARIA attributes for dynamic content
- **`announce(message, priority)`** — Programmatically announce updates to screen readers
- **`focus(selector)`** — Manage focus after DOM updates
- **`dj-aria-live` directive** — Mark regions for automatic screen reader announcements
- **`dj-keyboard` directive** — Declarative keyboard navigation handlers
- **Debug toolbar audit** — Accessibility warnings panel for missing ARIA attributes

#### Mobile/Touch Support
- **`MobileMixin`** — Touch event handling and gesture recognition
- **`dj-tap` directive** — Optimized tap handler (no 300ms delay)
- **`dj-longpress` directive** — Long-press gesture with configurable duration
- **`dj-swipe` directive** — Swipe gesture detection (left/right/up/down)
- **`dj-pinch` directive** — Pinch-to-zoom gesture handler
- **`dj-pull-refresh` directive** — Pull-to-refresh pattern for mobile lists
- **Viewport-aware hints** — Automatic mobile detection and optimization

#### Django Admin Integration
- **`LiveViewAdminMixin`** — Add LiveView interactivity to ModelAdmin classes
- **`@live_action` decorator** — Real-time admin actions without page reloads
- **Live inline editing** — Edit fields in changelist without opening detail view
- **Dashboard widgets** — LiveView-powered admin dashboard components
- **`LiveAdminView`** — Full LiveView pages within Django admin

### Testing

- 39 SSE tests covering connection lifecycle, event streaming, and error handling
- 32 accessibility tests for ARIA injection, announcements, and keyboard navigation
- 37 mobile/touch tests for gesture recognition and touch event handling
- 23 admin integration tests for LiveView-admin interoperability

## [0.5.0] - 2026-02-03 — "Developer Experience" 🛠️

A release focused on making djust easier to adopt with scaffolding tools and comprehensive documentation.

### Added

#### Scaffolding & Templates
- **Django app template** — Use `django-admin startapp --template` with djust's app template for instant LiveView-ready app structure
- **`startliveview` management command** — Scaffold new LiveViews with `python manage.py startliveview MyView` including template, tests, and URL configuration

#### Documentation
- **Comprehensive getting-started guide** — Step-by-step tutorial for new users covering installation, first LiveView, events, and deployment

#### Analysis & Documentation
- **State storage investigation** — In-depth analysis of session/state storage patterns with scaling recommendations for production deployments
- **VDOM performance benchmarks** — Documented benchmark results and optimization guidance

#### Testing
- **11 new VDOM stress tests** — Additional edge case coverage for large lists, rapid mutations, and complex nested structures

## [0.4.0] - 2026-02-03 — "Stable Connections" 🔗

A focused release hardening TurboNav/SPA navigation integration and adding performance analysis tooling.

### Fixed

#### TurboNav Integration
- **Triple-initialization bug** — `startHeartbeat()` now properly stores the interval ID and guards against duplicate heartbeats. Previously, each navigation would create additional heartbeat intervals without clearing old ones.
- **Duplicate WebSocket connections** — Added guards in `connect()` to prevent connecting when already connected or connecting. The `disconnect()` method now handles both CONNECTING and OPEN states.
- **Rapid navigation race conditions** — Added `reinitInProgress` guard to prevent concurrent reinitializations when users rapidly click between pages.
- **Orphaned connections** — `reinitLiveViewForTurboNav()` now explicitly cleans up `window.djust.liveViewInstance` in addition to the local `liveViewWS` reference.
- **Connection timestamp reset** — `disconnect()` now resets `stats.connectedAt` to properly track reconnection statistics.

### Added

#### Performance Tooling
- **Performance Analysis Report** — New `docs/PERFORMANCE_ANALYSIS.md` with comprehensive benchmark results:
  - 21/21 benchmarks passing under 2ms p95 target
  - Template rendering: 0.001ms (simple) to 1.2ms (500 items)
  - VDOM diffing: 0.004ms (single change) to 1.2ms (toggle 100 items)
  - Full update cycle: 0.007ms (counter) to 1.3ms (list append)
  - Bottleneck analysis and optimization recommendations

#### Documentation
- **TurboNav Integration Guide** — New comprehensive guide at `docs/guides/turbonav-integration.md` covering:
  - The contract between TurboNav and djust
  - Setup instructions for base templates
  - Common pitfalls and their solutions
  - Best practices for combining TurboNav with LiveViews
  - Troubleshooting guide and FAQ

## [0.3.0] - 2026-02-03 — "Phoenix Rising" 🔥

The biggest djust release yet — 13 major features bringing djust to full parity with Phoenix LiveView.

### Added

#### Navigation & URL State
- **`live_patch()`** — Update browser URL without remounting the view. Ideal for filters, pagination, and tabs.
- **`live_redirect()`** — Navigate to a different LiveView over the same WebSocket connection.
- **`handle_params()`** — Hook called when URL params change (browser back/forward or live_patch).
- **`dj-patch`** — Template directive for declarative URL updates: `<a dj-patch="?page=2">`.
- **`dj-navigate`** — Template directive for LiveView navigation: `<a dj-navigate="/items/42/">`.
- **`live_session()`** — Group related views to share WebSocket connections.

#### LiveForm Validation
- **`LiveForm`** — Standalone declarative form validation without Django Forms dependency.
- **Built-in validators** — `required`, `min_length`, `max_length`, `pattern`, `email`, `url`, `min`, `max`, `choices`.
- **Custom validators** — Pass callable validators for complex validation logic.
- **`live_form_from_model()`** — Auto-generate LiveForm from Django model field definitions.
- **Real-time validation** — Validate fields on `dj-change` for instant feedback.

#### File Uploads
- **`UploadMixin`** — Chunked file uploads over WebSocket with progress tracking.
- **`allow_upload()`** — Configure upload slots with size limits, accepted types, and max entries.
- **`consume_uploaded_entries()`** — Process completed uploads in event handlers.
- **`dj-upload`** — Template directive for drag-and-drop upload zones.
- **Magic byte validation** — Server validates file content, not just extensions.

#### Presence Tracking
- **`PresenceMixin`** — Track who's viewing a page in real-time.
- **`track_presence()`** / **`untrack_presence()`** — Start/stop tracking with custom metadata.
- **`list_presences()`** / **`presence_count()`** — Query current viewers.
- **`handle_presence_join()`** / **`handle_presence_leave()`** — Hooks for join/leave events.
- **`LiveCursorMixin`** — Share live cursor positions for collaborative features.
- **`broadcast_to_presence()`** — Send events to all users in a presence group.

#### Streaming
- **`stream_to()`** — Push partial DOM updates during async handler execution.
- **`stream_text()`** — Stream raw text content (append/prepend/replace modes).
- **`stream_insert()`** — Insert HTML at start or end of a container.
- **`stream_error()`** — Show error state while preserving partial content.
- **`stream_start()`** / **`stream_done()`** — Signal stream lifecycle to client.
- **`dj-stream`** — Template attribute to mark streaming targets.
- **~60fps batching** — Automatic rate limiting for smooth updates.

#### JavaScript Hooks
- **`dj-hook`** — Integrate third-party JS libraries (charts, maps, editors).
- **Hook lifecycle** — `mounted()`, `updated()`, `destroyed()`, `disconnected()`, `reconnected()`.
- **`pushEvent()`** — Send events from JS hooks to server handlers.
- **`handleEvent()`** — Listen for server-pushed events in JS hooks.
- **`window.DjustHooks`** — Register hooks Phoenix LiveView-style.

#### Client-Side Features
- **`dj-model`** — Two-way data binding between inputs and server state.
- **`dj-model.lazy`** — Update on blur instead of every keystroke.
- **`dj-model.debounce-N`** — Debounce model updates by N milliseconds.
- **`dj-confirm`** — Show browser confirm dialog before sending events.
- **`dj-loading`** — Show/hide elements, add classes, or disable during events.
- **`dj-transition`** — CSS enter/leave animations when elements mount/unmount.
- **`dj-debounce`** — Debounce any event type (not just inputs).
- **`dj-throttle`** — Throttle events to fire at most once per interval.
- **`dj-target`** — Scope DOM updates to specific elements.
- **`dj-optimistic`** — Apply client-side state changes immediately with auto-rollback.

#### Push Events
- **`push_event()`** — Push events to client-side JS hooks from handlers.
- **`push_event_to_view()`** — Push events from background tasks (Celery, management commands).

#### Testing
- **`LiveViewTestClient`** — Test LiveViews without browser or WebSocket.
- **`ComponentTestClient`** — Test components in isolation.
- **`MockUploadFile`** — Mock file uploads for testing.
- **`SnapshotTestMixin`** — Compare rendered output against stored snapshots.
- **`@performance_test`** — Assert handler time and query count limits.
- **pytest fixtures** — `live_view_client`, `component_client`, `mock_upload`.

#### Error Handling
- **Dev error overlay** — Rich overlay with syntax-highlighted tracebacks.
- **`djust:error` event** — Client-side event for custom error handling.
- **Debug mode** — Automatic when `DEBUG=True` or `data-debug` attribute.

#### Server-Push API
- **`push_to_view()`** — Push state updates from background tasks to connected clients.
- **`handle_tick()`** — Periodic handler for self-updating views.

### Fixed

- **Client-side SetText mis-targets text nodes after keyed MoveChild** — MoveChild patches now include `child_d` (the child's `djust_id`) so the client resolves the child to move by `data-dj-id` instead of stale index.
- **VDOM diff/patch round-trip failure on keyed child reorder** — `apply_patches` now processes patches level-by-level (shallowest parent first).
- **apply_patches djust_id-based resolution** — Rewrote to resolve parent nodes by `djust_id` instead of path-based traversal.
- **Diff engine keyed+unkeyed interleaving** — Emits `MoveChild` patches for unkeyed element children when position changes due to keyed sibling moves.
- **Text node targeting after keyed moves** — `SetText` patches now carry `djust_id` when available.

### Changed

- **Removed `@event` decorator alias** — Use `@event_handler` instead (deprecated in v0.2.2).
- **Auto-build client.js** — Pre-commit hook builds from `src/` modules automatically.
- **Keyed-mutation fuzz testing** — Proptest cases increased to 1000 with smarter tree mutation.

## [0.2.2] - 2026-02-01

### Fixed

- **Stale Closure Args on VDOM-Patched Elements** — After deleting a todo, the remaining button's click handler sent the wrong `_args` (stale closure from bind time) because `SetAttribute` patches updated the `dj-click` DOM attribute but not the listener closure. Event listeners now re-parse `dj-*` attributes from the DOM at event time. Also sets `dj-*` as DOM attributes in `createNodeFromVNode` and marks elements as bound to prevent duplicate listeners. ([#205](https://github.com/djust-org/djust/pull/205))
- **VDOM: Non-breaking Space Text Nodes Stripped** — Rust parser stripped `&nbsp;`-only text nodes (used in syntax highlighting) because `char::is_whitespace()` includes U+00A0. Now preserves `\u00A0` text nodes in parser, `to_html()`, and client-side path traversal. Also adds `sync_ids()` to prevent ID drift between server VDOM and client DOM after diffing, and 4-phase patch ordering matching Rust's `apply_patches()`. ([#199](https://github.com/djust-org/djust/pull/199))
- **CSRF Token Lookup on Formless Pages** — Pages without a `<form>` element failed to send CSRF tokens with WebSocket events. Token lookup now falls back to the `csrftoken` cookie. ([#210](https://github.com/djust-org/djust/pull/210))
- **Codegen Crash on Numeric Index Paths** — Template expressions like `{{ posts.0.url }}` produced paths starting with a numeric index (`0.url`), generating invalid Python (`obj.0`). Codegen now skips numeric-leading paths since list items are serialized individually.
- **JIT Serialization Pipeline** — Fixed multiple issues in JIT auto-serialization: ([#140](https://github.com/djust-org/djust/pull/140))
  - M2M `.all()` traversal now generates correct iteration code in codegen serializers
  - `@property` attributes are now serialized via Rust→Python codegen fallback when Rust can't access them
  - `list[Model]` context values (not just QuerySets) now receive full JIT optimization with `select_related`/`prefetch_related`
  - Nested dicts containing Model/QuerySet values are now deep-serialized recursively
  - `_djust_annotations` model class attribute for declaring computed annotations (e.g., `Count`) applied during query optimization
  - `{% include %}` templates are now inlined for variable extraction, so included template variables get JIT optimization
  - Rust template parser now correctly prefixes loop variable paths (e.g., `item.field` inside `{% for item in items %}`)
- **`{% include %}` After Cache Restore** — `template_dirs` was not included in msgpack serialization of `RustLiveView`. After a cache hit, the restored view had empty search paths, causing `{% include %}` tags to fail with "Template not found". Now calls `set_template_dirs()` on both WebSocket and HTTP cache-hit paths.
- **VDOM Replace Sibling Grouping** — Fixed `data-djust-replace` inserting children into wrong parent when the replace container has siblings. `groupPatchesByParent()` now uses the full path for child-operation patches, and `groupConsecutiveInserts()` checks parent identity before batching. ([#144](https://github.com/djust-org/djust/pull/144))
- **VDOM Replace Child Removal** — Fixed `data-djust-replace` not removing old children before inserting new ones, causing duplicate content on re-render. ([#142](https://github.com/djust-org/djust/pull/142), [#143](https://github.com/djust-org/djust/pull/143))
- **Context Processor Precedence** — View context now takes precedence over context processors. Previously, context processors could overwrite view-defined variables (e.g., Django's messages processor overwriting a view's `messages` variable).
- **VDOM Keyed Diff Insert Ordering** — Fixed `apply_patches` for keyed diff insert ordering where items were inserted in the wrong position. ([#154](https://github.com/djust-org/djust/pull/154))
- **VDOM MoveChild Resolution** — Fixed `MoveChild` in `apply_patch` by resolving children via `djust_id` instead of index. ([#150](https://github.com/djust-org/djust/pull/150))
- **Debug Toolbar: Received WebSocket Messages Not Captured** — Network tab now captures both sent and received WebSocket messages by intercepting the `onmessage` property setter (not just `addEventListener`). ([#188](https://github.com/djust-org/djust/pull/188))
- **Debug Toolbar: Events Tab Always Empty** — Events tab now populates by extracting event data from sent WebSocket messages and matching responses, replacing the broken `window.liveView` hook. ([#188](https://github.com/djust-org/djust/pull/188))
- **Debug Panel: Handler Discovery, Auto-loading, Tab Crashes** — Handler discovery now finds all public methods; `debug-panel.js` auto-loads; handler dict normalized to array; retroactive WebSocket hooking for late-loading panels. ([#191](https://github.com/djust-org/djust/pull/191), [#197](https://github.com/djust-org/djust/pull/197))

### Added

- **Debug Panel: Live Debug Payload** — When `DEBUG=True`, WebSocket event responses now include a `_debug` field with updated variables, handlers, patches, and performance metrics. ([#191](https://github.com/djust-org/djust/pull/191))
- **Debug Toolbar: Event Filtering** — Events tab filter controls to search by event/handler name and filter by status. ([#180](https://github.com/djust-org/djust/pull/180))
- **Debug Toolbar: Event Replay** — Replay button (⟳) that re-sends events through the WebSocket with original params. ([#181](https://github.com/djust-org/djust/pull/181))
- **Debug Toolbar: Scoped State Persistence** — Panel UI state scoped per view class via localStorage. ([#182](https://github.com/djust-org/djust/pull/182))
- **Debug Toolbar: Network Message Inspection** — Directional color coding and copy-to-clipboard for expanded payloads. ([#183](https://github.com/djust-org/djust/pull/183))
- **Debug Toolbar: Test Harness** — Integration tests against the actual `DjustDebugPanel` class. ([#185](https://github.com/djust-org/djust/pull/185))
- **VDOM Proptest/Fuzzing** — Property-based testing for the VDOM diff algorithm with `proptest`. ([#153](https://github.com/djust-org/djust/pull/153))
- **Duplicate Key Detection** — VDOM keyed diff now warns on duplicate keys. ([#149](https://github.com/djust-org/djust/pull/149))
- **Branding Assets** — Official logo variants (dark, light, icon, wordmark, transparent). ([#208](https://github.com/djust-org/djust/pull/208), [#213](https://github.com/djust-org/djust/pull/213))

### Deprecated

- **`@event` decorator alias** — The `@event` shorthand is deprecated in favor of `@event_handler`. `@event` will be removed in v0.3.0. A deprecation warning is emitted at import time. ([#141](https://github.com/djust-org/djust/pull/141))

### Changed

- **Internal: LiveView Mixin Extraction** — Refactored monolithic `live_view.py` into focused mixins: `RequestMixin`, `ContextMixin`, `JITMixin`, `TemplateMixin`, `RustBridgeMixin`, `ComponentMixin`, `LifecycleMixin`. No public API changes. ([#130](https://github.com/djust-org/djust/pull/130))
- **Internal: Module Splits** — Split `client.js` into source modules with concat build, extracted `websocket_utils.py`, `session_utils.py`, `serialization.py`, split `state_backend.py` into `state_backends` package, split `template_backend.py` into `template` package. ([#124](https://github.com/djust-org/djust/pull/124), [#125](https://github.com/djust-org/djust/pull/125), [#126](https://github.com/djust-org/djust/pull/126), [#128](https://github.com/djust-org/djust/pull/128), [#129](https://github.com/djust-org/djust/pull/129))
- **Dependencies** — Upgraded uuid 1.19→1.20, thiserror 1→2, bincode 1→2, happy-dom 20.3.7→20.4.0, actions/setup-python 5→6, actions/upload-artifact 4→6, actions/checkout 4→6, softprops/action-gh-release 1→2

## [0.2.1] - 2026-01-29

### Security

- **WebSocket Event Security Hardening** - Three-layer defense for WebSocket event dispatch: ([#104](https://github.com/djust-org/djust/pull/104))
  - **Event name guard** — regex pattern filter (`^[a-z][a-z0-9_]*$`) blocks private methods, dunders, and malformed names before `getattr()`
  - **`@event_handler` decorator allowlist** — only methods decorated with `@event_handler` (or listed in `_allowed_events`) are callable via WebSocket. Configurable via `event_security` setting (`"strict"` default, `"warn"`, `"open"`)
  - **Server-side rate limiting** — per-connection token bucket algorithm with configurable rate/burst. Per-handler `@rate_limit` decorator for expensive operations. Automatic disconnect after repeated violations (close code 4429)
  - **Per-IP connection limit** — process-level `IPConnectionTracker` enforces a maximum number of concurrent WebSocket connections per IP (default: 10) and a reconnection cooldown after rate-limit disconnects (default: 5 seconds). Configurable via `max_connections_per_ip` and `reconnect_cooldown` in `rate_limit` settings. Supports `X-Forwarded-For` header for proxied deployments. ([#108](https://github.com/djust-org/djust/issues/108), [#121](https://github.com/djust-org/djust/pull/121))
  - **Message size limit** — 64KB default (`max_message_size` setting)

### Documentation

- Added migration guide for `@event_handler` decorator requirement and strict mode upgrade path ([#105](https://github.com/djust-org/djust/issues/105), [#122](https://github.com/djust-org/djust/pull/122))
- Added `@event_handler` decorator to all example demo view handler methods

### Added

- `is_event_handler(func)` — check if a function is decorated with `@event_handler`
- `@rate_limit(rate, burst)` — per-handler server-side rate limiting decorator
- `_allowed_events` class attribute — escape hatch for bulk allowlisting without decorating each method
- `LIVEVIEW_CONFIG` settings: `event_security`, `rate_limit` (including `max_connections_per_ip`, `reconnect_cooldown`), `max_message_size`

## [0.2.0] - 2026-01-28

### Added

- **Template `and`/`or`/`in` Operators** - `{% if %}` conditions now support `and`, `or`, and `in` boolean/membership operators with correct precedence and chaining. ([#103](https://github.com/djust-org/djust/pull/103))

### Fixed

- **Pre-rendered DOM Whitespace Preservation** - WebSocket mount no longer replaces `innerHTML` when content was pre-rendered via HTTP GET. Instead, `data-dj-id` attributes are stamped onto existing DOM elements, preserving whitespace in code blocks and syntax-highlighted content. ([#99](https://github.com/djust-org/djust/pull/99))

- **VDOM Keyed Diffing** - Unkeyed children in keyed diffing contexts are now matched by relative position among unkeyed siblings, eliminating spurious insert+remove patch pairs when keyed children reorder. ([#95](https://github.com/djust-org/djust/pull/95), [#97](https://github.com/djust-org/djust/pull/97))

- **Event Handler Attributes Preserved** - `dj-*` event handler attributes are no longer removed during VDOM patching. ([#100](https://github.com/djust-org/djust/pull/100))

- **Model List Serialization** - Lists of Django Model instances are now properly serialized on GET requests. ([#103](https://github.com/djust-org/djust/pull/103))

- **Mount URL Path** - WebSocket mount requests now use the actual page URL instead of a hardcoded path. ([#95](https://github.com/djust-org/djust/pull/95))

### Changed

- **Dependencies** - Upgraded html5ever 0.27→0.36, markup5ever_rcdom 0.3→0.36, vitest 2.x→4.x, actions/download-artifact 4→7. ([#101](https://github.com/djust-org/djust/pull/101), [#102](https://github.com/djust-org/djust/pull/102), [#43](https://github.com/djust-org/djust/pull/43))

### Developer Experience

- **VDOM Debug Tracing** - `debug_vdom` Django config is now bridged to Rust VDOM tracing. Mixed keyed/unkeyed children emit developer warnings. ([#97](https://github.com/djust-org/djust/pull/97))

## [0.2.0a2] - 2026-01-27

### Changed

- **Internal: DRY Refactoring** - Reduced ~275 lines of duplicate code across the codebase through helper function extraction. These are internal improvements that don't affect the public API. ([#93](https://github.com/djust-org/djust/pull/93), [#94](https://github.com/djust-org/djust/pull/94))
  - `getComponentId()` - DOM traversal for component ID lookup (client.js)
  - `buildFormEventParams()` - Form event parameter building (client.js)
  - `send_error()` - WebSocket error response helper (websocket.py)
  - `_send_update()` - WebSocket patch/HTML response helper (websocket.py)
  - `_create_rust_instance()` - Rust component instantiation (base.py)
  - `_render_template_with_fallback()` - Template rendering with Rust→Django fallback (base.py)
  - `_make_metadata_decorator()` - Decorator factory for metadata-only decorators (decorators.py)

## [0.2.0a1] - 2026-01-26

### Changed

- **BREAKING: Event Binding Syntax** - Standardized all event bindings to use `dj-` prefix instead of `@` prefix. This affects all event attributes: `@click` → `dj-click`, `@input` → `dj-input`, `@change` → `dj-change`, `@submit` → `dj-submit`, `@blur` → `dj-blur`, `@focus` → `dj-focus`, `@keydown` → `dj-keydown`, `@keyup` → `dj-keyup`, `@loading.*` → `dj-loading.*`. Benefits: namespaced attributes, no conflicts with Vue/Alpine, no CSS selector escaping required. ([#68](https://github.com/djust-org/djust/issues/68))

- **BREAKING: Component Consolidation** - Removed legacy `python/djust/component.py`. Use `djust.Component` which now imports from `components/base.py`. ([#89](https://github.com/djust-org/djust/pull/89))

- **BREAKING: Method Rename** - `LiveComponent.get_context()` → `get_context_data()` for Django consistency. ([#89](https://github.com/djust-org/djust/pull/89))

- **BREAKING: Decorator Attributes Removed** - Deprecated decorator attributes removed: `_is_event_handler`, `_event_name`, `_debounce_seconds`, `_debounce_ms`, `_throttle_seconds`, `_throttle_ms`. Use `_djust_decorators` dict instead. ([#89](https://github.com/djust-org/djust/pull/89))

- **BREAKING: Data Attributes Renamed** - Standardized data attribute naming for consistency:
  - `data-liveview-root` → `data-djust-root`
  - `data-live-view` → `data-djust-view`
  - `data-live-lazy` → `data-djust-lazy`
  - `data-dj` → `data-dj-id`
  ([#89](https://github.com/djust-org/djust/pull/89))

- **BREAKING: WebSocket Message Types** - Renamed message types for consistency:
  - `connected` → `connect`
  - `mounted` → `mount`
  - `hotreload.message` → `hotreload`
  ([#89](https://github.com/djust-org/djust/pull/89))

### Added

- **LiveComponent Methods** - Added missing methods to `LiveComponent`: `_set_parent_callback()`, `send_parent()`, `unmount()`. ([#89](https://github.com/djust-org/djust/pull/89))

- **Inline Template Support** - `LiveComponent` now supports inline `template` attribute for template strings, in addition to `template_name` for file-based templates. ([#89](https://github.com/djust-org/djust/pull/89))

- **Form Components Export** - `ForeignKeySelect` and `ManyToManySelect` are now exported from `djust.components`. ([#89](https://github.com/djust-org/djust/pull/89))

### Fixed

- **`{% elif %}` Tag Support**: Template parser now correctly handles `{% elif %}` conditionals. Previously, elif branches fell through to the unknown tag handler and rendered all branches instead of just the matching one. ([#80](https://github.com/djust-org/djust/pull/80))

- **Template Include Fallback** - Component `render()` methods now fall back to Django templates when Rust template engine fails (e.g., for `{% include %}` tags). ([#89](https://github.com/djust-org/djust/pull/89))

## [0.1.8] - 2026-01-25

### Fixed

- **Nested Block Inheritance**: Fixed template inheritance for nested blocks. When a child template overrides a block that is nested inside another block in the parent (e.g., `content` inside `body`), the override is now correctly applied. ([#71](https://github.com/djust-org/djust/pull/71))

## [0.1.7] - 2026-01-25

### Added

- **Tag Handler Registry**: Extensible system for custom Django template tags in Rust. Register Python callbacks for tags like `{% url %}` and `{% static %}` with ~100-500ns overhead per call. Built-in tags (if, for, block) remain zero-overhead native Rust. Includes ADR documenting architecture decisions. ([#65](https://github.com/djust-org/djust/pull/65))
- **Comparison Operators**: Template conditions now support `>`, `<`, `>=`, `<=` operators in addition to `==` and `!=`. ([#65](https://github.com/djust-org/djust/pull/65))
- **Enhanced `{% include %}` Tag**: Full support for `with` clause (pass variables) and `only` keyword (isolate context). ([#65](https://github.com/djust-org/djust/pull/65))
- **Performance Testing Infrastructure**: Comprehensive benchmarking with Criterion (Rust) and pytest-benchmark (Python). New Makefile commands: `make benchmark`, `make benchmark-quick`, `make benchmark-e2e`. Enables tracking performance across releases and detecting regressions. ([#69](https://github.com/djust-org/djust/pull/69))
- **Inline Handler Arguments**: Event handlers now support function-call syntax with arguments directly in the template attribute. Use `dj-click="handler('arg')"` instead of `dj-click="handler" data-value="arg"`. Supports strings, numbers, booleans, null, and multiple arguments. ([#67](https://github.com/djust-org/djust/pull/67))

### Fixed

- **Async Event Handlers**: WebSocket consumer now properly supports `async def` event handlers. Previously only synchronous handlers worked correctly. ([#63](https://github.com/djust-org/djust/pull/63))

### Performance

- Dashboard render: ~37µs (27,000 renders/sec)
- Tag handler overhead: ~100-500ns per call
- Template variable substitution: ~970ns
- 50-row data table: ~188µs

## [0.1.6] - 2026-01-24

### Added

- **`{% url %}` Tag Support**: Django's `{% url %}` template tag is now fully supported with automatic Python-side URL resolution. Supports named URLs, namespaced URLs, and positional/keyword arguments. ([#55](https://github.com/djust-org/djust/pull/55))
- **`{% include %}` Tag Support**: Fixed template include functionality by passing template directories to the Rust engine. Included templates are now correctly resolved from configured template paths. ([#55](https://github.com/djust-org/djust/pull/55))
- **`urlencode` Filter**: Added the `urlencode` filter for URL-safe encoding of strings. Supports encoding all characters or preserving safe characters. ([#55](https://github.com/djust-org/djust/pull/55))
- **Comparison Operators in `{% if %}` Tags**: Added support for `>`, `<`, `>=`, `<=` comparison operators in conditional expressions. ([#55](https://github.com/djust-org/djust/pull/55))
- **Auto-serialization for Django Types**: Context variables with Django types (datetime, date, time, Decimal, UUID, FieldFile) are now automatically serialized for Rust rendering. No manual JSON conversion required. ([#55](https://github.com/djust-org/djust/pull/55))
- **Lazy Hydration**: LiveView elements can now defer WebSocket connections until they enter the viewport or receive user interaction. Use `data-djust-lazy` attribute with modes: `viewport` (default), `click`, `hover`, or `idle`. Reduces memory usage by 20-40% per page for below-fold content. ([#54](https://github.com/djust-org/djust/pull/54))
- **TurboNav Integration**: LiveView now works seamlessly with Turbo-style client-side navigation. WebSocket connections are properly disconnected on navigation and reinitialized when returning to a page. ([#54](https://github.com/djust-org/djust/pull/54))

### Changed

- **AST Optimization**: Template parser now merges adjacent Text nodes during AST optimization, reducing allocations and improving render time by 5-15%. Comment nodes are also removed during optimization as they produce no output. ([#54](https://github.com/djust-org/djust/pull/54))

### Fixed

- **Nested Block Inheritance**: Fixed template inheritance for nested blocks (e.g., `docs_content` inside `content`). Block overrides are now recursively applied to merged content, ensuring deeply nested blocks are correctly resolved. ([#57](https://github.com/djust-org/djust/pull/57))
- **Form Validation First-Click Issue**: Added `parse_html_continue()` function to maintain ID counter continuity across parsing operations. Prevents ID collisions when inserting dynamically generated elements (like validation error messages) that caused first-click validation issues. ([#54](https://github.com/djust-org/djust/pull/54))
- **Whitespace Preservation**: Whitespace is now preserved inside `<pre>`, `<code>`, `<textarea>`, `<script>`, and `<style>` elements during both Rust parsing and client-side DOM patching. ([#54](https://github.com/djust-org/djust/pull/54))

### Security

- **pyo3 Upgrade**: Upgraded pyo3 from 0.22 to 0.24 to address RUSTSEC-2025-0020 (buffer overflow vulnerability in `PyString::from_object`). ([#55](https://github.com/djust-org/djust/pull/55))

## [0.1.5] - 2026-01-23

### Added

- **Context Processor Support**: LiveView now automatically applies Django context processors configured in `DjustTemplateBackend`. Variables like `GOOGLE_ANALYTICS_ID`, `user`, `messages`, etc. are now available in LiveView templates without manual passing. ([#26](https://github.com/djust-org/djust/pull/26))

### Fixed

- **VDOM Cache Key Path Awareness**: Cache keys now include URL path and query string hash, preventing render corruption when navigating between views with different template structures (e.g., `/emails/` vs `/emails/?sender=1`). ([#24](https://github.com/djust-org/djust/pull/24))

## [0.1.4] - 2026-01-22

### Added

- Initial public release
- LiveView reactive server-side rendering
- Rust-powered VDOM engine (10-100x faster than Django templates)
- WebSocket support for real-time updates
- 40+ UI components (Bootstrap 5 and Tailwind CSS)
- State management decorators (`@state`, `@computed`, `@debounce`, `@optimistic`)
- Form handling with real-time validation
- Testing utilities (`LiveViewTestClient`, snapshot testing)

## [0.1.3] - 2026-01-22

### Fixed

- Bug fixes and stability improvements

[Unreleased]: https://github.com/djust-org/djust/compare/v0.6.0...HEAD
[0.6.0]: https://github.com/djust-org/djust/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/djust-org/djust/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/djust-org/djust/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/djust-org/djust/compare/v0.2.2...v0.3.0
[0.2.2]: https://github.com/djust-org/djust/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/djust-org/djust/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/djust-org/djust/compare/v0.2.0a2...v0.2.0
[0.2.0a2]: https://github.com/djust-org/djust/compare/v0.2.0a1...v0.2.0a2
[0.2.0a1]: https://github.com/djust-org/djust/compare/v0.1.8...v0.2.0a1
[0.1.8]: https://github.com/djust-org/djust/compare/v0.1.7...v0.1.8
[0.1.7]: https://github.com/djust-org/djust/compare/v0.1.6...v0.1.7
[0.1.6]: https://github.com/djust-org/djust/compare/v0.1.5...v0.1.6
[0.1.5]: https://github.com/djust-org/djust/compare/v0.1.4...v0.1.5
[0.1.4]: https://github.com/djust-org/djust/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/djust-org/djust/releases/tag/v0.1.3
