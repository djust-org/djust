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

**Current architecture** (`state_backend.py`):
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
- Error messages from event security now surface in the debug toolbar — verify clarity and usefulness
- Identify common first-time stumbling blocks: missing `@event` decorator? WebSocket/Channels config? Template syntax?
- Consider a `django-admin startapp` template with djust boilerplate
- Consider better error pages in DEBUG mode with actionable suggestions

## 5. Break Up Large Files

Separate concerns and reduce merge conflicts. Priority by file size:

| File | Lines | Split candidates |
|------|-------|-----------------|
| `debug-panel.js` | 3,475 | UI rendering, event logging, VDOM patches, variable inspection |
| `client.js` | 3,193 | WebSocket connection, VDOM patching, event binding, loading states, init |
| `live_view.py` | 2,924 | Core class, request handling, state management, JSON encoding, script injection |
| `websocket.py` | 1,224 | Consumer, event dispatch, validation, rate limiting |
| `state_backend.py` | 1,043 | Backend interface, memory impl, Redis impl, compression, cleanup |
| `template_backend.py` | 978 | Django integration, variable extraction, context processing |

Start with `live_view.py` and `client.js` — they change most frequently and cause the most merge conflicts.

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

**Missing:**
- Event filtering by handler name, success/error status, time range
- Event replay — resend previous events with same parameters
- Network tab — raw WebSocket message inspection, connection status monitoring
- Performance warnings tab — slow patches (>5ms), state size alerts, memory tracking
- State size visualization — session contents and size breakdown
- Panel state persistence across TurboNav navigation
