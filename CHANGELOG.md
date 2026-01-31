# Changelog

All notable changes to djust will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.2rc2] - 2026-01-31

### Fixed

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

[Unreleased]: https://github.com/djust-org/djust/compare/v0.2.2rc2...HEAD
[0.2.2rc2]: https://github.com/djust-org/djust/compare/v0.2.2rc1...v0.2.2rc2
[0.2.2rc1]: https://github.com/djust-org/djust/compare/v0.2.1...v0.2.2rc1
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
