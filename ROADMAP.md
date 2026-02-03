# djust Roadmap

## 1. Profile & Improve Performance

Identify bottlenecks in the render cycle and optimize.

- Profile the full request path: HTTP render → WebSocket mount → event → VDOM diff → patch
- Use existing benchmarks in `tests/benchmarks/` (serialization, e2e render, tags, templates) as baselines
- Instrument key paths with `performance.py` timing trees and `profiler.py` `@profile` decorator
- Determine whether Rust VDOM diffs or Python serialization/template rendering is the bottleneck
- Profile `state_backend.py` — compression overhead for states >10KB, Redis round-trip latency
- Target: <2ms per patch, <5ms for list updates

## 2. Investigate Session/State Storage — ✅ Complete

Understand memory pressure and evaluate client-side or external storage.

**Current architecture** (`state_backends/`):
- Pluggable backends: memory (dev) or Redis (prod)
- Server memory holds: RustLiveView instance, user context, cached decorator values, draft state
- States >100KB trigger warnings; >10KB get zstd compressed
- TTL-based expiration with `cleanup_liveview_sessions` management command

**Investigation complete:**
- Documented scaling recommendations for production deployments
- Analyzed Redis vs memory backend tradeoffs
- Provided guidance on state reconstruction patterns
- See `docs/STATE_STORAGE.md` for full analysis

## 3. TurboNav Integration — ✅ Complete

Make djust + TurboNav a first-class documented pattern.

**Issues found and fixed:**
- ✅ Injected scripts need `data-turbo-track="reload"` for `loadPageScripts` to pick them up
- ✅ Inline `<script>` tags inside `<main>` require explicit execution after `innerHTML` swap
- ✅ `DOMContentLoaded` doesn't fire on dynamically loaded scripts — must check `document.readyState`
- ✅ Triple-initialization bug: `startHeartbeat()` now stores interval ID and has guard against duplicates
- ✅ Duplicate WebSocket connections: Added guards in `connect()` and `disconnect()` methods
- ✅ Rapid navigation: Added `reinitInProgress` guard to prevent concurrent reinitializations
- ✅ Orphaned connections: `reinitLiveViewForTurboNav()` now cleans up `window.djust.liveViewInstance`

**Decision:** TurboNav remains a separate integration concern. djust provides hooks (`turbo:load` handler, `reinitLiveViewForTurboNav()`) but doesn't bundle TurboNav, allowing flexibility to use Turbo Drive, other SPA navigation libraries, or custom solutions.

**Documentation:** See `docs/guides/turbonav-integration.md` for:
- The contract between TurboNav and djust
- Setup instructions
- Common pitfalls and solutions
- Best practices and troubleshooting

## 4. Evaluate & Improve Developer Experience — ✅ Complete

Lower the barrier to getting started and debugging.

- ~~Docs are extensive (40+ files) but scattered — consolidate the getting-started path~~ ✅ Comprehensive getting-started guide added
- CLI (`cli.py`) has `stats`, `health`, `profile`, `analyze`, `clear` — evaluate discoverability
- ~~Error messages from event security now surface in the debug toolbar — verify clarity and usefulness~~ ✅ Done (#112)
- Identify common first-time stumbling blocks: missing `@event_handler` decorator? WebSocket/Channels config? Template syntax?
- ~~Document `@event` → `@event_handler` migration path~~ ✅ Done (#122, #141)
- ~~Consider a `django-admin startapp` template with djust boilerplate~~ ✅ Django app template added
- ~~Consider better error pages in DEBUG mode with actionable suggestions~~ ✅ `startliveview` management command added

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

## 8. VDOM Correctness — ✅ Complete

Ongoing effort to harden the VDOM diff and patch pipeline.

**Resolved:**
- ✅ Keyed diff insert ordering (#152, #154)
- ✅ MoveChild resolution via djust_id (#150)
- ✅ Duplicate key detection warning (#145, #149)
- ✅ data-djust-replace child removal (#142, #143, #144)
- ✅ Unkeyed list reorder documentation (#148, #151)
- ✅ Proptest/fuzzing for diff algorithm (#146, #153)
- ✅ JIT serialization fixes for M2M, nested dicts, @property (#140)
- ✅ Edge cases from proptest fuzzing investigated
- ✅ Large list performance documented with benchmarks
- ✅ 11 new VDOM stress tests added for edge cases
