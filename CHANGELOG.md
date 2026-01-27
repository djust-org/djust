# Changelog

All notable changes to djust will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-01-26

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

[Unreleased]: https://github.com/djust-org/djust/compare/v0.2.0a1...HEAD
[0.2.0-alpha.1]: https://github.com/djust-org/djust/compare/v0.1.8...v0.2.0a1
[0.1.8]: https://github.com/djust-org/djust/compare/v0.1.7...v0.1.8
[0.1.7]: https://github.com/djust-org/djust/compare/v0.1.6...v0.1.7
[0.1.6]: https://github.com/djust-org/djust/compare/v0.1.5...v0.1.6
[0.1.5]: https://github.com/djust-org/djust/compare/v0.1.4...v0.1.5
[0.1.4]: https://github.com/djust-org/djust/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/djust-org/djust/releases/tag/v0.1.3
