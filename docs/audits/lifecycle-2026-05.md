# djust Lifecycle Coverage Audit — 2026-05

**Status**: Snapshot in time. Synthesized from a state-type × hook matrix walk through `python/djust/websocket.py` (5,300+ LoC), `mixins/async_work.py`, `mixins/rust_bridge.py`, `decorators.py`, and the just-shipped `runtime.py` transport abstraction.

**Companion audit**: `decorator-contract-2026-05.md` (sibling audit covering the orthogonal decorator/tag-name surface).

**Scope**: Every (state-type, lifecycle-hook) cell in the matrix below. Two transports: WebSocket (`websocket.py`) and SSE (`sse.py`/`runtime.py`). What does the framework promise re: re-render, serialization, and reconnect-restoration for each cell?

---

## 1. Architecture at a glance

Three concentric layers run on each user event:

```
JS event binding (09-event-binding.js)
        ↓
Transport frame (WS or SSE)
        ↓
LiveViewConsumer.handle_event / handle_mount   (websocket.py)
        ↓
_validate_event_security — getattr(view, event_name, None)   (websocket_utils.py:173)
        ↓
Pre-handler snapshot via _snapshot_assigns                    (websocket.py:163)
        ↓
Handler invocation (decorators.py wraps for @event_handler / @action / @background)
        ↓
Post-handler snapshot + _compute_changed_keys                 (websocket.py:222)
        ↓
_sync_state_to_rust → render_with_diff                         (rust_bridge.py)
        ↓
_send_update(patches) → _flush_push_events → _dispatch_async_work
```

### State-type catalog

| State type | Where stored | Visible to template? | Persisted across reconnect? |
|---|---|---|---|
| Public state (`self.x`) | `view.__dict__`, non-`_` | ✓ | ✓ |
| Private state (`self._x`) | `view.__dict__`, `_`-prefixed | ✓ via `get_context_data()` | ✓ if in `_user_private_keys` |
| `AsyncResult` (from `assign_async`) | view attr (a `frozen` dataclass) | ✓ via attr access | ✗ not JSON-serializable |
| `_action_state` dict (from `@action`) | `view._action_state[name]` | ✓ template injection | ✗ runtime-only |
| `_pending_push_events` | view list | N/A (out-of-band) | N/A |
| `_async_tasks` | view dict | N/A | N/A (regenerated) |
| `_changed_keys` | view set | N/A (Rust-only) | N/A |

### Lifecycle hooks

| Hook | Code site | What it does |
|---|---|---|
| `mount(request, **kwargs)` | `websocket.py:1616` (handle_mount) → user code at line 2100 | Initial setup; class attrs initialized |
| `@event_handler` runtime path | `websocket.py:2529` (handle_event) | Event dispatch → handler → re-render |
| `@action` runtime path | `decorators.py:340-393` (action wrapper) | Wraps event_handler; records `_action_state[name]` |
| `start_async()` callback completion | `websocket.py:827` (`_run_async_work`) | Background work drains; view re-renders |
| Navigation / `live_redirect` | `websocket.py:3974+` | New mount; state reset |
| `push_to_view` / `push_event` | broadcasts | Out-of-band frames |
| WS reconnect | `websocket.py:1954-2001` (state restore path) | Session-state restored before mount |

---

## 2. State-type × hook matrix

Verdicts: ✓ tested + works, ? unverified, ✗ known broken.

| State type | `mount()` | `@event_handler` | `@action` | `start_async` | `push_event` | URL change | WS reconnect |
|---|---|---|---|---|---|---|---|
| Public state | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ reset | ✓ restored |
| Private state | ✓ | **✗ #1281** | ✗ same | ✗ same | ? | ✓ reset | ✓ if in `_user_private_keys` |
| `AsyncResult` | **✗ #1280** | ✓ | ✓ | ✓ | ? | ✓ reset | **✗ Gap-D** |
| `_action_state` | ? | ✓ | ✓ | N/A | ? | ✗ reset | **✗ Gap-C** |
| `_pending_push_events` | **✗ Gap-E** | ✓ flushed | ✓ flushed | ✓ flushed | ✓ self | N/A | N/A |
| `_async_tasks` | **✗ #1280** | ✓ drained | ✓ drained | N/A | ? | ✓ reset | ✓ |
| `_changed_keys` | N/A | ✓ pre/post | ✓ pre/post | ? snapshot only | N/A | ✓ reset | ? |

**5 ✗ cells** drive the ranked weaknesses below.

---

## 3. Current weaknesses, ranked

🔴 = production-deploy-blocker class. 🟡 = should-fix. Effort: S/M/L.

| # | Weakness | Cite | Impact | Effort | Issue |
|---|---|---|---|---|---|
| 1 | `mount()` doesn't drain `_async_tasks`. `assign_async()`/`start_async()` called from `mount()` is queued but never spawned. View shows pending state forever until user triggers any event. | `websocket.py:2352` (handle_mount end has no `_dispatch_async_work` call); compare `websocket.py:921, 1218, 1287` (drain calls in event/deferred paths) | 🔴 | S | [#1280](https://github.com/djust-org/djust/issues/1280) |
| 2 | Private state changes (`self._x = ...`) don't trigger re-render. `_snapshot_assigns` filters all `_*` attrs from change-detection (`if k.startswith("_"): continue`). Changes survive in `__dict__` and persist via `_user_private_keys` but template stays stale until next event. | `websocket.py:163-219` (`_snapshot_assigns`), `websocket.py:222-233` (`_compute_changed_keys` operates on already-filtered snapshots) | 🔴 | M | [#1281](https://github.com/djust-org/djust/issues/1281) |
| 3 | `_pending_push_events` queued during `mount()` is never flushed pre-response. Notifications/alerts emitted by `mount()` or `on_mount` hooks lose the mount-window delivery. | `websocket.py:2352` (no `_flush_push_events` before `send_json(response)`); compare `websocket.py:921` (drained in `_run_async_work`), `websocket.py:1082, 1110, 1211` (drained in event/deferred paths) | 🟡 | S | (file as new issue) |
| 4 | `_action_state` not persisted across reconnect. After `@action` completes successfully or with error, the dict survives in-memory but is discarded on disconnect. On reconnect, template reading `{{ create_todo.error }}` sees fresh empty state — UI silently desyncs from prior outcome. | `decorators.py:349-378` (`_action_state` stamped at runtime), `live_view.py:_get_private_state` (saves only `_user_private_keys` attrs) | 🟡 | M | (file as new issue) |
| 5 | `AsyncResult` not session-serializable. `frozen` dataclass with no `to_dict`. `_get_private_state` JSON-encodes each value; `AsyncResult` gets coerced to `str()` and silently dropped. On reconnect, attribute is `None` or missing entirely → templates raise `VariableDoesNotExist`. | `async_result.py` (frozen dataclass), `live_view.py:_get_private_state` (`json.dumps(...)` fallback) | 🟡 | M | [#1274](https://github.com/djust-org/djust/issues/1274) (sibling — same root: serializer allowlist) |
| 6 | `_snapshot_assigns` content fingerprint truncates at 100 list items + 50 dict keys. Mutations inside a list of 101+ are missed; "stale-after-grow" class. | `websocket.py:188-212` (the `len(v) < 100` / `len(v) < 50` guards) | 🟡 | S | (file as new issue) |
| 7 | `mount()` lacks pre/post snapshot. Differs from `@event_handler` which captures both. Means a mount-time mutation has no `_changed_keys` and forces full HTML render. (Acceptable for first render; not for replays/snapshot-restore.) | `websocket.py:2100, 2168, 2208` (mount path: no `_capture_dirty_baseline` paired with `_compute_changed_keys`) | 🟡 | M | (file as new issue) |
| 8 | Dual change-detection paths with divergent semantics. Explicit `_changed_keys` (set via `set_changed_keys()`) → partial Rust render. Implicit (snapshot diff) → only public attrs. The two never meet — explicit path can include `_x`, snapshot can't. | `rust_bridge.py:707` (Rust receives `_changed_keys`); `websocket.py:222` (snapshot diff filters `_x`) | 🟡 | M | (file as new issue) |

---

## 4. Test gaps

| Area | Gap | Reproducer outline |
|---|---|---|
| `mount()` + `start_async()` | No test asserts the queued task actually completes post-mount | Mount a view that calls `self.start_async(self._loader)`; assert `self._loader` ran and sent a patch frame |
| `mount()` + `push_event()` | No test asserts mount-time push events reach client | Mount a view that calls `self.push_event("hello", {})`; assert client received frame |
| Private state re-render | No test asserts `self._x = ...` in handler triggers patch | Handler mutates `self._cache`; assert `_cache`-backed template fragment patches |
| `_action_state` reconnect | No test asserts `{{ action.error }}` survives WS disconnect → reconnect | `@action` raises; disconnect; reconnect; assert error visible |
| `AsyncResult` reconnect | No test asserts `AsyncResult.pending()` survives session round-trip | `assign_async()` in mount; disconnect mid-fetch; reconnect; assert state |
| Snapshot truncation | No test for list-with-100+ in-place mutations | Make `self.items` of length 101; mutate `items[50]['name']`; assert detected |

---

## 5. Improvement roadmap

### Phase 1 — Quick wins (~3 PRs)

| # | Fix | Files | Effort | Closes |
|---|---|---|---|---|
| 1 | Add `await self._dispatch_async_work()` and `await self._flush_push_events()` before final `send_json(response)` in `handle_mount` | `websocket.py:2352` | S | Weaknesses #1, #3 → #1280 |
| 2 | Add 2 regression tests: mount-time `start_async` completes + mount-time `push_event` delivered | `tests/test_lifecycle_mount_async.py` (new) | S | Test gap |

### Phase 2 — Correctness hardening (split-foundation per Action #163)

| Initiative | Effort | Closes |
|---|---|---|
| **Foundation**: extend `_snapshot_assigns` to optionally include `_user_private_keys` attrs in change detection. Gate behind opt-in flag for backwards compatibility, then flip default in v0.10. | M | #1281 (foundation) |
| **Capability**: flip default; add migration note in CHANGELOG; update `docs/STATE_MANAGEMENT_API.md` to document private-state re-render semantics | M | #1281 (capability) |
| `AsyncResult` serializable envelope: implement `to_dict`/`from_dict`; register in `serialization.py:normalize_django_value` | M | Weakness #5 (sibling to Audit F #1274) |
| `_action_state` session persistence: extend `_get_private_state`/`_restore_private_state` to include action state with serializable result/error | M | Weakness #4 |
| Mount-time pre-snapshot: capture `_capture_dirty_baseline()` before user `mount()` to enable partial re-render on snapshot-restore path | M | Weakness #7 |
| Unify change-detection: single source of truth for `_changed_keys` whether it came from explicit `set_changed_keys()` or snapshot diff | M | Weakness #8 |

### Phase 3 — Architectural (multi-PR)

| Initiative | Effort | Impact |
|---|---|---|
| Move change-detection into Rust — declared changed keys at render time instead of pre/post snapshot in Python | L | Eliminates snapshot overhead; closes truncation gaps (#6); enables partial-dict patching for lists |
| Unify state serialization across public/private/action/async — single `_capture_persisted_state()` method on LiveView | M | Reduces drift; one canonical contract for "what survives reconnect" |
| Add `on_state_changed(self, keys)` observability hook for time-travel/debug-overlay/external-state-store integrations | S-M | Pays back on debug tooling and time-travel features |

### Phase 4 — Documentation

- The lifecycle matrix above belongs in `docs/STATE_MANAGEMENT_API.md` as the canonical contract reference, not buried in this audit.
- The mount path's "queue-without-drain" semantics need a user-facing note in `docs/website/guides/loading-states.md` (complete after #1280 lands).
- The `_user_private_keys` opt-in mechanism needs first-class documentation; currently surfaced only in code comments.

---

## 6. Strategic observations

1. **The mount path is the under-tested critical path.** Mount is the first-impression hook (initial HTML, async loading state setup). Phase 1 alone adds the load-bearing tests. Every other lifecycle hook (event, push, async-completion) calls `_dispatch_async_work` and `_flush_push_events`; mount is the lone outlier. This asymmetry should be removed before more hooks are added.

2. **Change-detection has two sources of truth that disagree on private state.** Explicit `set_changed_keys({"_x"})` would mark `_x` as changed and pass to Rust — but the snapshot-diff path filters `_x` before computing diffs. Users who write `self.set_changed_keys({"_x"})` get correct behavior; users who rely on auto-detection don't. The fix is structural (pick one path), not a new flag.

3. **Reconnect-restoration is asymmetric across state types.** Public state is auto-persisted/restored. Private state is conditionally persisted (must be in `_user_private_keys`). `_action_state` and `AsyncResult` are unconditionally lost. This is a contract gap, not a bug per se — but it means `@action` and `assign_async` look like they "just work" until a reconnect. Phase 2 establishes the contract; users get a single sentence: "X survives reconnect; Y does not."

4. **The `_snapshot_assigns` truncation guards are pragmatic but invisible.** A list of 101 items mutating an inner field silently breaks change-detection. No warning is emitted; the patch frame just doesn't include the change. The fix is either (a) raise the threshold, (b) emit a `vdom_trace!()`-style warning when the threshold trips, or (c) document the threshold in the public API. Phase 1 should add option (b).

5. **Process payoff aligned with VDOM audit findings**: like the VDOM audit, the central recurring class here is "scope of change-detection vs. scope of template-context dependency." The VDOM audit's #1205 (`list[Model]` `__eq__` pk-only) and this audit's weakness #2 (`_x` filtered) are the same shape: change-detection sees less than the template depends on. A unified test pattern — "mutation-after-render assertion across all state-types" — would catch both classes.

---

## 7. Sequencing

- **v0.9.2-5 drain bucket** (~1-2 days, 1-2 PRs): Phase 1 quick wins — drain `_async_tasks` + flush `_pending_push_events` in `handle_mount` (closes #1280, #1283). Add 2 regression tests. **Blocks v0.9.2 stable** because #1280 is 🔴 production-deploy-blocker class; shipping 0.9.2 with a known broken `mount() + assign_async()` would re-burn the same downstream consumers who reported it.
- **v0.9.3** (~5 days, 4 PRs): Phase 2 split-foundation — private state re-render gate (foundation PR + capability PR per Action Tracker #163; closes #1281, #1286), AsyncResult envelope (#1274), `_action_state` persistence (#1284), snapshot-truncation warning (#1285). Mount-time pre-snapshot (Weakness #7) lands in the same window. These five are independent but should land in the same milestone for a coherent release note.
- **v0.10 planning**: Phase 3 architectural work. Move change-detection into Rust is ADR-class; benefits from a planning cycle.
- **Continuous**: Phase 4 documentation. Pick up alongside Phase 1/2 PRs.

---

## 8. Companion canon update

When this audit lands, add to `CLAUDE.md` under "Process canonicalizations":

> **Lifecycle-matrix maintenance** (Audit A — 2026-05). When adding any new
> state-type, lifecycle-hook, or transport, update the matrix in
> `docs/audits/lifecycle-2026-05.md` AND add a regression test for each new
> cell. The matrix is the contract; the tests lock it in. Skipping this step
> reproduces the failure mode that produced #1280, #1281, and the four
> Phase-2 weaknesses cataloged here.
