# djust Roadmap

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

**Current problem:** Even with the Redis backend, the `RustLiveView` stays in Rust heap memory for the full WebSocket connection lifetime. Redis is only used for persistence across reconnects — active connections still hold 200-400KB in Rust memory (template source + serialized state + full VDOM tree). At 1000 concurrent users that's 200-400MB of server memory.

**Where memory lives per connection:**
| Component | Location | Size | Persistent? |
|-----------|----------|------|-------------|
| Python `LiveView` attributes | Python heap | 1-5KB | Yes (connection lifetime) |
| PyO3 wrapper (`_rust_view`) | Python heap | ~100B pointer | Yes |
| `RustLiveViewBackend.template_source` | Rust heap | 10KB-1MB | Yes |
| `RustLiveViewBackend.state` | Rust heap | 10-500KB | Yes |
| `RustLiveViewBackend.last_vdom` | Rust heap | 50-500KB | Yes |
| ASGI scope + request | Python heap | 5-50KB | Yes |

**Proposed: configurable hybrid state offloading**

Add a `STATE_OFFLOAD` config option that controls whether the `RustLiveView` is kept in memory or deserialized on demand:

| Mode | Behavior | Per-connection memory | Event latency |
|------|----------|----------------------|---------------|
| `"none"` (default) | Current behavior — Rust object lives in memory | 200-400KB | Fastest |
| `"between_events"` | Serialize to Redis/DB after each event, drop Rust object, deserialize on next event | 1-5KB | +1-3ms (Redis) / +3-8ms (DB) |
| `"adaptive"` | Keep hot sessions in memory (LRU), offload idle sessions after configurable timeout | 1-5KB idle, 200-400KB hot | 0ms hot / +1-8ms cold |

The `"adaptive"` mode with an LRU cache would give the best of both worlds: fast responses for active users, minimal memory for idle connections waiting for the next interaction.

**Implementation steps:**
1. Add a `DatabaseStateBackend` using Django ORM (`BinaryField` for msgpack bytes)
2. Add `STATE_OFFLOAD` config option to control offload behavior
3. Implement `"between_events"` mode: serialize/drop after `_send_update`, deserialize in `receive_json`
4. Implement `"adaptive"` mode: LRU cache with configurable max size and idle timeout
5. Benchmark serialize/deserialize round-trip cost for realistic view sizes

**Questions to answer:**
- Can template context be reconstructed from DB rather than stored in memory/Redis?
- Can any state move client-side (signed cookies, JWT)?
- What is the Redis serialization cost vs memory backend?
- What is a typical session size and how does it scale with concurrent users?
- What is the msgpack serialize/deserialize overhead for 100KB-500KB views?
- Can the VDOM be reconstructed from a fresh render instead of stored? (trade CPU for memory)

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

| File | Status | PRs |
|------|--------|-----|
| `debug-panel.js` | ✅ Split into source modules | #125 |
| `client.js` | ✅ Split into source modules | #124 |
| `live_view.py` | ✅ Extracted serialization, session utils, mixins | #126, #127, #130 |
| `websocket.py` | ✅ Extracted websocket_utils | #129 |
| `state_backend.py` | ✅ Split into state_backends package | #123 |
| `template_backend.py` | ✅ Split into template package | #128 |

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
- ✅ Handler discovery matches runtime `event_security` policy — only `@event_handler` and `_allowed_events` (#193, #197)
- ✅ Auto-instantiation from `DJUST_DEBUG_INFO` bootstrap data (#194, #197)
- ✅ Handlers tab supports dict format from server (#195, #197)
- ✅ Live debug updates via WebSocket `_debug` payload — variables, handlers, patches, performance (#196, #197)
- ✅ Network tab — WebSocket message inspection with directional color coding, expandable payloads, copy-to-clipboard, connection stats (#196, #197)
- ✅ Event filtering by handler name and success/error status (#197)
- ✅ Event replay — resend previous events with same parameters, inline status feedback (#197)
- ✅ Per-view state persistence via localStorage (#197)

**Missing:**
- Event filtering by time range
- Performance warnings tab — slow patches (>5ms), state size alerts, memory tracking
- State size visualization — session contents and size breakdown
- Panel state persistence across TurboNav navigation (per-view UI prefs persist, but event/patch histories clear on navigation)

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
