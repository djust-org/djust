# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.4] - 2026-01-22

### Fixed
- **Multi-level template inheritance** - Templates extending a parent that also extends another parent now resolve blocks correctly. Previously, blocks in deeply nested templates were not being populated properly. (PR #23)

### Changed
- Moved regex pattern compilation to class-level constants for better performance
- Improved test assertion specificity with regex word boundaries

## [0.1.3] - 2026-01-22

### Added
- **ID-based VDOM patch resolution** - Elements now receive unique `data-dj` attributes for reliable patch targeting. Fixes issues where conditional content changes caused patch failures. Uses compact base62 encoding to minimize HTML overhead (~3-4%).
- **Automatic type coercion for event handler parameters** - Template `data-*` attributes (always strings) are now automatically converted to expected types based on handler type hints. Supports `int`, `float`, `bool`, `Decimal`, `UUID`, `list`, `List[T]`, and `Optional[T]`.
- New `coerce_types` parameter for `@event_handler()` decorator to disable coercion when raw strings are needed
- Enhanced error messages with hints when type coercion fails
- **Testing utilities** (`djust.testing`) for testing LiveViews without browser/WebSocket:
  - `LiveViewTestClient` - Mount views, send events, assert state changes
  - `SnapshotTestMixin` - Compare rendered HTML against stored snapshots
  - `@performance_test` decorator - Fail tests if handlers exceed time/query thresholds
  - `MockRequest` and `create_test_view` helpers
- **Debug Panel - State Timeline** - View history of state changes with before/after diffs, timeline scrubbing, and state restoration
- `VNode.to_html()` method for serializing VDOM back to HTML with data-dj attributes
- URL query parameters now passed to server mount for filter support

### Fixed
- Debug panel visibility now restored correctly on page load
- VDOM patch path traversal failures on complex page structures with conditional content

### Changed
- Patches now include `d` field (djust_id) for O(1) client-side element lookup via `querySelector`
- Client uses ID-based resolution first, falls back to index-based path traversal
- Improved log sanitization for defense-in-depth security

## [0.1.2] - 2026-01-16

### Fixed
- Fix manylinux wheel builds using PyO3/maturin-action with proper containers

## [0.1.1] - 2026-01-16

### Fixed
- Fix `@loading.disable` attribute not disabling buttons during async operations
- Implement complete client-side caching for `@cache` decorator
- Fix Linux wheel platform tags for PyPI compatibility (manylinux2017)

### Added
- Cache invalidation API: `window.djust.clearCache()` and `window.djust.invalidateCache(pattern)`
- LRU cache eviction with configurable size limit (100 entries)
- Namespace all globals under `window.djust` (with backward compatibility)
- `@loading.show` now supports custom display values (e.g., `@loading.show="flex"`)

### Changed
- Optimize `scanAndRegister()` with targeted CSS selectors for better performance
- Gate debug console.log behind `djustDebug` flag

## [0.1.0] - 2025-12-10

Initial public release of djust — Phoenix LiveView-style reactive server-side rendering for Django, powered by Rust.

### Highlights

#### Core Framework
- **Rust-Powered Performance**: 10-100x faster template rendering via native Rust engine
- **LiveView Base Class**: Reactive server-side components with automatic client updates
- **Virtual DOM Diffing**: Sub-millisecond (<100μs) diff algorithm for minimal DOM patches
- **WebSocket Transport**: Real-time updates over WebSocket with automatic reconnection

#### Template Engine
- **Django Template Compatibility**: Full support for common tags and 37+ filters
- **Template Inheritance**: `{% extends %}`, `{% block %}` with multi-level inheritance
- **Essential Tags**: `{% csrf_token %}`, `{% static %}`, `{% with %}`, `{% comment %}`, `{% verbatim %}`, `{% load %}`
- **Rich Filters**: date/time formatting, string manipulation, list operations, numeric formatting

#### Component System
- **Two-Tier Architecture**: Stateless `Component` and stateful `LiveComponent`
- **Full Lifecycle**: `mount()`, `update()`, `unmount()` for stateful components
- **Parent-Child Communication**: Props down, events up pattern with `send_parent()`
- **Automatic Registration**: Components auto-register when added to context

#### State Management Decorators
- `@debounce(wait)` — Delay execution until user stops typing
- `@throttle(interval)` — Rate-limit rapid events
- `@optimistic` — Instant UI feedback with automatic rollback on errors
- `@cache(ttl, key_params)` — Client-side response caching
- `@client_state(keys)` — Cross-component state sharing
- `DraftModeMixin` — Auto-save form drafts to localStorage

#### Memory Optimization
- **temporary_assigns**: Clear large collections from state after each render
- **Streams API**: Memory-efficient list management (`stream()`, `stream_insert()`, `stream_delete()`)
- **dj-update directive**: Client-side `append`, `prepend`, `ignore` modes for efficient list updates
- **State Fingerprinting**: Track changes efficiently for incremental state sync
- **Profiler**: Built-in performance profiling with operation timing metrics

#### Developer Experience
- **Debug Panel**: Interactive inspector with event history, VDOM patches, and variable inspection (Ctrl+Shift+D)
- **CLI Tools**: `djust stats`, `djust health`, `djust profile`, `djust analyze`, `djust clear`
- **Hot Reload**: Automatic browser refresh on file changes (development mode)
- **@event_handler Decorator**: Auto-discovery, validation, and debug panel integration

#### State Backend
- **InMemory Backend**: Thread-safe with RLock, optimized for development
- **Redis Backend**: Production-ready horizontal scaling with optional zstd compression
- **Memory Stats**: Track session sizes and identify optimization opportunities
- **Automatic Cleanup**: TTL-based session expiration

#### Django Integration
- **Django Forms**: Real-time validation with `FormMixin`
- **CSS Framework Support**: Bootstrap 5, Tailwind CSS, or custom styling
- **CSRF Protection**: Automatic via `{% csrf_token %}` tag
- **Session Authentication**: WebSocket auth via Django sessions

### Performance

| Metric | Value |
|--------|-------|
| Template rendering | <1ms (vs 2-50ms Django) |
| VDOM diffing | <100μs |
| Client bundle | ~7KB (vs Phoenix ~30KB, Livewire ~50KB) |
| State serialization | 5-10x faster with Rust MessagePack |
| Optimistic updates | <1ms latency |

### Requirements

- Python 3.8+
- Django 3.2+
- Rust 1.70+ (for building from source)

### Installation

```bash
pip install djust
```

Or install with optional dependencies:

```bash
pip install djust[redis]        # Redis state backend
pip install djust[compression]  # zstd compression
pip install djust[performance]  # orjson + zstd
```

### Links

- **Documentation**: https://djust.org/docs
- **Repository**: https://github.com/johnrtipton/djust
- **Issues**: https://github.com/johnrtipton/djust/issues

---

[0.1.3]: https://github.com/djust-org/djust/releases/tag/v0.1.3
[0.1.2]: https://github.com/djust-org/djust/releases/tag/v0.1.2
[0.1.1]: https://github.com/djust-org/djust/releases/tag/v0.1.1
[0.1.0]: https://github.com/djust-org/djust/releases/tag/v0.1.0
