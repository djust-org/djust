# Changelog

All notable changes to djust will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.0rc5] - 2026-02-11

### Added

- **Automatic change tracking** — Phoenix-style render optimization. The framework automatically detects which context values changed between renders and only sends those to Rust's `update_state()`. Replaces the manual `static_assigns` API. Two-layer detection: snapshot comparison for instance attributes, `id()` reference comparison for computed values (e.g., `@lru_cache` results). Immutable types (`str`, `int`, `float`, `bool`, `None`, `bytes`, `tuple`, `frozenset`) skip `deepcopy` in snapshots.

### Fixed

- **Developer Tools Events tab not capturing events** — The debug panel loaded after the WebSocket connected, so its prototype hooks missed the existing connection's `onmessage` handler. Fixed `_hookExistingWebSocket()` to wrap the existing handler using the original property descriptor. Also added `html_update` to event response type matching.

### Removed

- **`static_assigns` class attribute** — Replaced by automatic change tracking. The framework now detects unchanged values automatically — no manual annotation needed.

## [0.3.0rc4] - 2026-02-11

### Added

- **All 57 Django template filters** — The Rust template engine now supports the complete set of Django built-in filters. Added 24 filters across two batches: `default_if_none`, `wordcount`, `wordwrap`, `striptags`, `addslashes`, `ljust`, `rjust`, `center`, `make_list`, `json_script`, `force_escape`, `escapejs`, `linenumbers`, `get_digit`, `iriencode`, `urlize`, `urlizetrunc`, `truncatechars_html`, `truncatewords_html`, `safeseq`, `escapeseq`, `unordered_list`, `phone2numeric`, `pprint`. ([#246](https://github.com/djust-org/djust/issues/246), [#254](https://github.com/djust-org/djust/issues/254))
- **Authentication & Authorization** — Opinionated, framework-enforced auth for LiveViews. View-level `login_required` and `permission_required` class attributes (plus `LoginRequiredMixin`/`PermissionRequiredMixin` for Django-familiar patterns). Custom auth logic via `check_permissions()` hook. Handler-level `@permission_required()` decorator for protecting individual event handlers. Auth checks run server-side before `mount()` and before handler dispatch — no client-side bypass possible. Integrates with `djust_audit` command (shows auth posture per view) and Django system checks (`djust.S005` warns on unprotected views with exposed state).
- **Navigation & URL State** — `live_patch()` updates URL query params without remount, `live_redirect()` navigates to a different view over the same WebSocket. Includes `handle_params()` callback, `live_session()` URL routing helper, and client-side `dj-patch`/`dj-navigate` directives with popstate handling. ([#236](https://github.com/djust-org/djust/pull/236))
- **Presence Tracking** — Real-time user presence with `PresenceMixin` and `PresenceManager`. Pluggable backends (in-memory and Redis). Includes `LiveCursorMixin` and `CursorTracker` for collaborative live cursor features. ([#236](https://github.com/djust-org/djust/pull/236))
- **Streaming** — `StreamingMixin` for real-time partial DOM updates (e.g., LLM token-by-token streaming). Provides `stream_to()`, `stream_insert()`, `stream_text()`, `stream_error()`, `stream_start()`/`stream_done()`, and `push_state()`. Batched at ~60fps to prevent flooding. ([#236](https://github.com/djust-org/djust/pull/236))
- **File Uploads** — `UploadMixin` with binary WebSocket frame protocol for chunked file uploads. Includes progress tracking, magic bytes validation, file size/extension/MIME checking, and client-side `dj-upload`/`dj-upload-drop` directives. ([#236](https://github.com/djust-org/djust/pull/236))
- **JS Hooks** — `dj-hook` attribute for client-side JavaScript lifecycle hooks (mounted, updated, destroyed, disconnected, reconnected). ([#236](https://github.com/djust-org/djust/pull/236))
- **Model Binding** — `dj-model` two-way data binding with `.lazy` and `.debounce-N` modifiers. Server-side `ModelBindingMixin` with security field blocklist and type coercion. ([#236](https://github.com/djust-org/djust/pull/236))
- **Client Directives** — `dj-confirm` confirmation dialogs, `dj-target` scoped updates, embedded view routing in event handlers. ([#236](https://github.com/djust-org/djust/pull/236))
- **Server-Push API** — Background tasks (Celery, management commands, cron jobs) can now push state updates to connected LiveView clients via `push_to_view()`. Includes per-view channel groups (auto-joined on mount), a sync/async public API (`push_to_view` / `apush_to_view`), and periodic `handle_tick()` for self-updating views. ([#230](https://github.com/djust-org/djust/issues/230))
- **Progressive Web App (PWA) Support** — Complete offline-first PWA implementation with service worker integration, IndexedDB/LocalStorage abstraction, optimistic UI updates, and offline-aware template directives. Includes comprehensive template tags (`{% djust_pwa_head %}`, `{% djust_pwa_manifest %}`), PWA mixins (`PWAMixin`, `OfflineMixin`, `SyncMixin`), and automatic synchronization when online. ([#235](https://github.com/djust-org/djust/pull/235))
- **Multi-Tenant SaaS Support** — Production-ready multi-tenant architecture with flexible tenant resolution strategies (subdomain, path, header, session, custom, chained), automatic data isolation, tenant-aware state backends, and comprehensive template context injection. Includes `TenantMixin` and `TenantScopedMixin` for views. ([#235](https://github.com/djust-org/djust/pull/235))
- **`dj-poll` attribute** — Declarative polling for LiveView elements. Add `dj-poll="handler_name"` to any element to trigger the handler at regular intervals. Configurable via `dj-poll-interval` (default: 5000ms). Automatically pauses when the page is hidden and resumes on visibility change. ([#269](https://github.com/djust-org/djust/issues/269))
- **`DjustMiddlewareStack`** — New ASGI middleware for apps that don't use `django.contrib.auth`. Wraps WebSocket routes with session middleware only (no auth required). Updated `C005` system check to recognize both `AuthMiddlewareStack` and `DjustMiddlewareStack`. ([#265](https://github.com/djust-org/djust/issues/265))
- **System check `C006`** — Warns when `daphne` is in `INSTALLED_APPS` but `whitenoise` middleware is missing. ([#259](https://github.com/djust-org/djust/issues/259))
- **`startproject` / `startapp` / `new` CLI commands** — `python -m djust new myapp` creates a full project with optional features (`--with-auth`, `--with-db`, `--with-presence`, `--with-streaming`, `--from-schema`). Legacy `startproject` and `startapp` commands also available. ([#266](https://github.com/djust-org/djust/issues/266))
- **`djust mcp install` CLI command** — Automates MCP server setup for Claude Code, Cursor, and Windsurf. Tries `claude mcp add` first (canonical for Claude Code), falls back to writing `.mcp.json` directly. Merges with existing config, backs up malformed files, idempotent.
- **Simplified root element** — `data-djust-view` is now the only required attribute on LiveView container elements. The client auto-stamps `data-djust-root` and `data-liveview-root` at init time. Old three-attribute format still works. ([#258](https://github.com/djust-org/djust/issues/258))
- **Model `.pk` in templates** — `{{ model.pk }}` now works in Rust-rendered templates. Model serialization includes a `pk` key with the native primary key value. ([#262](https://github.com/djust-org/djust/issues/262))
- **Better Error Messages** — Improved error messages for common LiveView event handler mistakes (missing `@event_handler`, wrong method signature). ([#248](https://github.com/djust-org/djust/issues/248))
- **`LiveViewSmokeTest` mixin** — Automated smoke and fuzz testing for LiveView classes. ([#251](https://github.com/djust-org/djust/pull/251))
- **MCP server** — `python manage.py djust_mcp` starts a Model Context Protocol server for AI assistant integration. Provides framework introspection, system checks, scaffolding, and validation tools. Used by `djust mcp install` to configure Claude Code, Cursor, and Windsurf.
- **`djust_audit` management command** — Security audit showing auth posture, exposed state, and handler signatures per view.
- **`djust_check` management command** — Django system checks for project validation. Gains `--fix` flag for safe auto-fixes and `--format json` for enhanced output with fix hints.
- **`djust_schema` management command** — Extract and generate Django models from JSON schema files.
- **`djust_ai_context` management command** — Generate AI-focused context files for LLM integrations.
- **AI documentation** — `docs/ai/` with focused guides for events, forms, JIT, lifecycle, security, and templates. `docs/llms.txt` and `docs/llms-full.txt` for LLM context.
- **Auto-build client.js from src/ modules** — Pre-commit hook runs `build-client.sh` when `src/` files change. ([#211](https://github.com/djust-org/djust/issues/211))
- **Keyed-mutation fuzz test generator** — New proptest generator produces tree B by mutating tree A, exercising keyed diff paths more effectively. Proptest cases bumped from 500 to 1000. ([#216](https://github.com/djust-org/djust/issues/216), [#217](https://github.com/djust-org/djust/issues/217))

### Changed

- **BREAKING: `data-dj-*` prefix stripping** — Client-side `extractTypedParams()` now strips the `dj_` prefix from `data-dj-*` attributes. `data-dj-preset="dark"` sends `{preset: "dark"}` instead of `{dj_preset: "dark"}`. Update handler parameter names accordingly: `dj_foo` → `foo`.
- **State Backends** — Enhanced with tenant-aware isolation support (`TenantAwareRedisBackend`, `TenantAwareMemoryBackend`).

### Performance

- **Batched `sync_to_async` calls** — Event handler processing now uses 2 thread hops instead of 4, saving ~1-4ms per event. ([#277](https://github.com/djust-org/djust/issues/277))
- **Eliminated JSON encode/decode roundtrip** — Direct `normalize_django_value()` Python-to-Python type normalization replaces 17 `json.loads(json.dumps(...))` patterns. Saves 2-5ms per event for views with database objects. ([#279](https://github.com/djust-org/djust/issues/279))
- **Cached template variable extraction** — Rust `extract_template_variables()` results cached by content hash (SHA-256). Size-capped at 256 entries with automatic eviction. ([#280](https://github.com/djust-org/djust/issues/280))
- **Cached context processor resolution** — `resolve_context_processors()` results cached per settings configuration. Invalidated on `setting_changed` signal. ([#281](https://github.com/djust-org/djust/issues/281))
- **JIT short-circuit for non-DB views** — Views without QuerySets or Models in context skip the entire JIT serialization pipeline. Saves ~0.5ms per event for simple views. ([#278](https://github.com/djust-org/djust/issues/278))
- **Slimmer debug payload** — Event responses send only state variables; handler metadata moved to initial mount as static data. ~68% smaller debug payloads (~25KB → ~8KB per event).

### Fixed

- **Inline args on form events** — `dj-change`, `dj-input`, `dj-blur`, `dj-focus` now parse inline arguments (e.g., `dj-change="toggle(3)"`) before sending to server. Also fixed state change detection to use deep copy comparison, catching in-place mutations.
- **Error overlay on intentional disconnect** — Suppress "WebSocket Connection Failed" overlay during TurboNav navigation via `_intentionalDisconnect` flag.
- **VDOM patch failure recovery** — When VDOM patches fail, the client requests recovery HTML on demand instead of reloading the page. Uses DOM morphing to preserve event listeners and form state. ([#259](https://github.com/djust-org/djust/issues/259))
- **HTTP Fallback Protocol** — `post()` now accepts the HTTP fallback format where the event name is in the `X-Djust-Event` header and params are flat in the body JSON. ([#255](https://github.com/djust-org/djust/issues/255))
- **Debug panel HTTP-only mode** — POST responses include `_debug` payload when `DEBUG=True`, enabling the debug panel in HTTP-only mode. ([#267](https://github.com/djust-org/djust/issues/267))
- **Silent LiveView config failures** — Client JS now shows helpful `console.error` when no LiveView containers are found. Added system check `V005` for modules not in `LIVEVIEW_ALLOWED_MODULES`. ([#257](https://github.com/djust-org/djust/issues/257))
- **HTTP-only mode session state on GET** — `get()` now saves view state to the session immediately when `use_websocket: False`. ([#264](https://github.com/djust-org/djust/issues/264))
- **`use_websocket: False` client-side enforcement** — Setting now actually prevents WebSocket connections. ([#260](https://github.com/djust-org/djust/issues/260))
- **DOM morphing preserves event listeners** — `html_update` now uses morphdom-style DOM diffing instead of `innerHTML`. ([#236](https://github.com/djust-org/djust/pull/236))
- **Textarea newlines preserved** — Template whitespace stripping no longer collapses newlines inside `<textarea>` elements. ([#236](https://github.com/djust-org/djust/pull/236))
- **PresenceMixin crash without auth** — `track_presence()` now checks for `request.user` before accessing it. ([#236](https://github.com/djust-org/djust/pull/236))
- **`_skip_render` support in server_push** — `server_push()` now checks `_skip_render`, preventing phantom renders and VDOM version mismatches. ([#236](https://github.com/djust-org/djust/pull/236))
- **Client-side SetText mis-targets after keyed MoveChild** — MoveChild patches now include `child_d` for `data-dj-id` resolution. ([#225](https://github.com/djust-org/djust/issues/225))
- **VDOM diff/patch round-trip on keyed child reorder** — Patches now processed level-by-level (shallowest parent first). ([#212](https://github.com/djust-org/djust/issues/212))
- **apply_patches djust_id-based resolution** — Resolves parent nodes by `djust_id` instead of path-based traversal. ([#216](https://github.com/djust-org/djust/issues/216))
- **Diff engine keyed+unkeyed interleaving** — Emits `MoveChild` patches for unkeyed element children in keyed contexts. ([#219](https://github.com/djust-org/djust/issues/219))
- **Text node targeting after keyed moves** — `SetText` patches carry `djust_id` when available; `sync_ids` propagates IDs to text nodes. ([#221](https://github.com/djust-org/djust/issues/221))
- **Tag registry test pollution** — `clear_tag_handlers()` now restores built-in handlers in teardown. ([#261](https://github.com/djust-org/djust/issues/261))

### Security

- **HTTP POST handler dispatch gating** — `post()` now enforces the same security model as the WebSocket path: only `@event_handler`-decorated methods can be invoked. Validates event names with `is_safe_event_name()` to block dunders and private methods.
- **Auto-escaping in Rust template engine** — `SafeString` values propagated to Rust for proper auto-escaping.
- **HTML-escaped `urlize` and `unordered_list` filters** — Both filters now escape their output to prevent XSS. ([#254](https://github.com/djust-org/djust/issues/254))
- **Template tag XSS prevention** — All PWA template tags now use `format_html()` and `escape()` instead of `mark_safe()` with f-string interpolation.
- **Sync endpoint hardening** — Removed `@csrf_exempt` from `sync_endpoint_view`. Added authentication requirement, payload validation, and safe field extraction.
- **Silent exception elimination** — All `except: pass` patterns replaced with appropriate logging calls.
- **Production JS hardened** — All `console.log` calls guarded behind `djustDebug` flag.

### Removed

- **`_allowed_events` class attribute** — The backwards-compatibility escape hatch that allowed undecorated methods to be called via WebSocket or HTTP POST has been removed. All event handlers must now use the `@event_handler` decorator.

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

[Unreleased]: https://github.com/djust-org/djust/compare/v0.3.0rc4...HEAD
[0.3.0rc4]: https://github.com/djust-org/djust/compare/v0.2.2...v0.3.0rc4
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
