# ADR-038 exposure sink inventory

Working inventory, not activation approval. The constructor guard remains
closed. Entries identify real producers/destinations and existing evidence;
an implemented projection is not proof that every caller uses it.

## Classified entry points

| Destination | Producer / boundary | Evidence and remaining closure |
| --- | --- | --- |
| Template context | mixins/context.py: ContextMixin._get_explicit_context_data and context processors | test_exposure_context.py covers declared context/provider boundaries. Dynamic/inherited provider closure remains E2. |
| Rust render state and render caches | mixins/rust_bridge.py: _sync_state_to_rust, preloaded_context, cached Rust views | test_exposure_render_cache.py exercises actual Rust rendering and the memory backend. Explicit initialization bypasses the shared legacy cache entirely; context remains instance-owned. Actor render state is a separate open route. |
| HTTP HTML and embedded configuration | mixins/request.py and mixins/template.py | test_exposure_http.py exercises staged HTTP paths; full provider/renderer matrix is E5. |
| Direct client state | live_view.py: get_state | test_exposure_state_apis.py asserts client grants and sentinel absence at returned values, including failed/unknown policies. |
| Raw client snapshot state | live_view.py: _capture_snapshot_state | Same direct API suite; only snapshot grants. Raw private/component export and unverified restore are rejected. |
| Signed browser snapshot | _exposure_snapshots.py: ClientSnapshot.capture/restore; runtime mount/event frames | test_exposure_snapshots.py and test_exposure_sse_navigation.py. Browser storage delivery and cross-worker/failure matrix remain E5. |
| Browser service-worker storage | static/djust/src/46-state-snapshot.js | Carries the signed server blob. Must audit all writer/reader paths and actual stored bytes, not merely token generation. |
| Declared server session state | _exposure_sessions.py: ServerStateSession.save/asave/load/aload | test_exposure_sessions.py covers bound envelopes and backend capability checks. Do not equate this adapter with every state_backend caller. |
| Legacy/Rust state backends | state_backend.py and state_backends/{base,memory,redis}.py | The three automatic Python backend.set calls are in RustBridgeMixin._initialize_rust_view. Explicit initialization bypasses backend resolution/read/write. Actor integration remains open; this is not cross-worker acceptance. |
| Root WS/SSE foreground frames | runtime.py: dispatch_mount, _dispatch_event, _dispatch_single_event, _render_and_send | Staged runtime/HTTP/SSE tests exist; full destinations and debug/provider matrix still open. |
| Root background frames and logs | runtime.py: _execute_async_task, _render_async_result | Diagnostic sentinel regression added by this inventory. Authorization, persistence, signed-snapshot refresh and render failures remain E3/E1 closure work. |
| Explicit child results/state | _child_async.py and _exposure_child_persistence.py | test_exposure_child_async.py and child lifecycle suites cover selected eager/sticky owners. Parent/mount queued work and unsupported lifecycle combinations remain E3. |
| Actor event frames | runtime.py actor branch; WebSocketTransport.dispatch_actor_event | Explicit actor events currently fail closed. Actor mount/backend integration must be implemented or receive an explicit supported-boundary decision before activation. |
| Debug assign endpoint | observability/views.py: _lenient_assigns/view_assigns | test_exposure_debug.py asserts actual response bytes and redacted defaults under DEBUG. Debug mutation is not ordinary authorized event dispatch. |
| Time-travel history and restore | time_travel.py: record_event_start/end, restore/replay paths | test_exposure_debug.py covers projected records and denied raw restoration. Explicit records are not legacy-restorable snapshots. |
| Bug capture/export | bug_capture.py: encode_view_state | Same debug suite covers historical provenance and reprojection; cannot reuse legacy history after policy changes. |
| Runtime debug frames | runtime.py transport debug projection | Inspect all transport diagnostic hooks, not only the observability endpoint. Existing projection is not exhaustive error/traceback evidence. |

## Diagnostic finding addressed first

Root background execution logged exception tracebacks and task names even for
explicit/invalid policies. test_exposure_root_async_diagnostics.py reproduces
the leak using callback, result-handler and stale-task sentinels. The fix logs
static messages for nonlegacy work, without exception payloads/tracebacks.
Detailed legacy logging requires legacy policy both before work and at the log
site, so a transition to explicit policy cannot expose late failures.

This does not fix root background authorization or persistence. Application
result handlers still receive their own exception object; deliberate application
logging/rendering is outside the automatic-export guarantee.

## Render-cache persistence finding

`RustBridgeMixin._initialize_rust_view` had three automatic legacy backend
writes: HTTP cache-hit refresh, WebSocket cache-hit refresh, and cache-miss
creation. The memory backend retains the mutable Rust view itself, so later
render-context updates enter retained state without any additional save call.
Redis serializes the renderer through `serialize_msgpack`; the Rust payload
includes template source, the full state map, VDOM, version and timestamp.
Neither representation is the declared server-state projection.

The regression tests first reproduced both transient render-state retention
and restoration of a legacy renderer by an explicit view with the same
session/page/template key, for HTTP and WebSocket initialization. Explicit
initialization now bypasses legacy backend resolution entirely and keeps its
render baseline on the view instance. Declared state persistence continues
through `ServerStateSession`; reconnect must rebuild authorized render context.
Legacy cache entries remain intact for legacy callers. This intentionally
forgoes shared render-baseline reuse for explicit views; measure the cost in E6.

This proof concerns the Python render-cache entry point. Actor mounts dispatch
separately through `WebSocketTransport.dispatch_actor_mount`, passing context
to the actor, and are **not** protected by the explicit actor-event refusal.
Actor mount/backend handling remains an activation blocker.

## Remaining inventory work

1. Trace every legacy/Rust backend writer from actor, render cache, mount,
   navigation, teardown and reconnect; inspect the actual stored payload.
2. Trace browser snapshot writers/readers and debug/error transport hooks,
   including WebSocket-only mount background work and failure paths.
3. Add destination-level sentinel tests for each uncovered route before calling
   that row closed. Keep malformed/unknown policies fail-closed.
4. Reconcile provider-owned state (forms/actions/streams/uploads) with E2 and
   managed objects in ADR-035; do not add independent safety-name lists.
5. Cross-check the resulting caller inventory against ADR-038 D6 and E5's
   actual HTTP/WS/SSE, renderer and cross-worker matrix.

Explicit application push messages/API responses are outside the automatic
export contract, as specified in ADR-038 D6. All other unclassified automatic
sinks remain activation blockers; this initial inventory is not exhaustive.
