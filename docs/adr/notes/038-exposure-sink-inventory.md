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
| Actor mount/event frames | runtime.py actor branches; WSConsumerTransport.dispatch_actor_mount/dispatch_actor_event | Staged nonlegacy actor mounts now refuse before lifecycle/transport registration, matching the existing event refusal. test_exposure_actor_mount.py checks actual single/batch WebSocket dispatch and surviving sibling mounts. Actor integration still requires implementation or an explicit supported-boundary decision before activation. |
| Debug assign endpoint | observability/views.py: _lenient_assigns/view_assigns | test_exposure_debug.py asserts actual response bytes and redacted defaults under DEBUG. Debug mutation is not ordinary authorized event dispatch. |
| Time-travel history and restore | time_travel.py: record_event_start/end, restore/replay paths | test_exposure_debug.py covers projected records and denied raw restoration. Explicit records are not legacy-restorable snapshots. |
| Bug capture/export | bug_capture.py: encode_view_state | Same debug suite covers historical provenance and reprojection; cannot reuse legacy history after policy changes. |
| Runtime debug frames | runtime.py transport debug projection | Inspect all transport diagnostic hooks, not only the observability endpoint. Existing projection is not exhaustive error/traceback evidence. |
| Exception logs, DEBUG responses and traceback ring | security/error_handling.py: handle_exception; observability/tracebacks.py: record_traceback | test_exposure_mount_diagnostics.py covers runtime initialization, auth, mount, handle_params, initial render and actor-render catches at frames/logs/ring destinations. The shared redacted mode does not inspect exceptions or record traceback data. Instantiation, outer transport catches, foreground events and other lifecycle hooks still require closure; this is not a global diagnostics guarantee. |

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
separately through `WSConsumerTransport.dispatch_actor_mount`, passing context
to the actor. The actor-event refusal did not protect that path. A subsequent
guard now refuses nonlegacy actor mounts before lifecycle hooks or transport
registration, without closing a shared batch socket. Real WebSocket single and
batch tests reproduce the missing guard; legacy actor template tests remain
green. This is a temporary staged refusal, not implemented explicit actor
support or a decision to remove actors from ADR-038's acceptance scope.

The renderer-policy marker is initialized with framework attributes, before
the private-state classification snapshot, so legacy user-private persistence
cannot accidentally carry it. A regression exercises that classification.

## Mount diagnostic finding

The generic exception handler previously recorded full exception messages and
tracebacks in the observability ring and logs in both DEBUG modes, and included
them in DEBUG response frames. Runtime mount failure tests reproduced this at
initialization, authorization, mount, handle_params and initial rendering.

`handle_exception(expose_details=False)` now produces a static log and generic
error response before inspecting an exception, metadata or traceback. Only
exact `True` permits detailed diagnostics, and an inherited restricted scope
still overrides that permission. Mount callers require legacy policy both
before lifecycle work and at the failure boundary. A turn-local ContextVar
carries the entry restriction into nested calls and sync_to_async workers,
so a successful policy change in an earlier lifecycle hook cannot grant later
diagnostics. Scope exit restores the caller's permission; nested scopes cannot
loosen it. Concurrent-request and exception-reset tests cover that isolation.
Legacy behavior remains tested in both DEBUG modes.

An actor-render catch also rechecks policy: the tested actor adapter starts
legacy and changes policy before failing. This tests the catch, not support for
explicit actor mounting. The ordinary explicit actor mount remains refused.
Template-hash fallback logging was another leak on the tested legacy-to-explicit
render path; after such a failure it now propagates to the redacted boundary
instead of logging and continuing into the legacy cache.

These tests cover the named local mount catches. They do not cover arbitrary
exceptions escaping outer WS/SSE handlers, constructor failures, all foreground
event handlers, or every callback invoked during a mount. Those remain E1 work.

## Remaining inventory work

### Event diagnostic evidence

Runtime root handler and full-render failures now inherit an owned diagnostic
scope across foreground, deferred and direct render calls. Both successful and
failing handlers recheck their owner; rendering also rechecks policy before
handling an exception or continuing downstream. A protected handler exception
is not stringified for time-travel metadata. Deferred handler failures retain
their existing no-response/no-traceback-ring behavior, with a static protected
log instead of a traceback.

`test_exposure_event_diagnostics.py` covers 68 cases across DEBUG modes, initial
and final policies, handler/render failure, and exceptions that must not be
stringified. The initial matrix reproduced 24 leaks; independent review added
render-time policy transitions and reproduced six more failures before the fix.
All 68 cases pass. This evidence covers the named runtime catches, not all
component/child callbacks, outer transport catches or constructor failures.

### Callback diagnostic evidence

The callback diagnostic slice extends the runtime evidence to all four waiter
notification callers (root, deferred, component-parent and sticky child), plus
time-travel notifications and deferred draining. The shared waiter helper keeps
source arguments and legacy warning formats intact, without introducing an error
frame or traceback-ring entry. Both root and target policies are checked, including
owner replacement during failure. Native waiter predicates and activity queue
dispatch have their own protected catches: their internally swallowed failures
cannot be covered by an outer runtime catch alone. Owned scopes retain observed
restrictions across each full notification/drain pass and reset afterward.
Runtime-owned slots are watched inside the scope so a nested predicate can see
root policy changes or replacement even though it swallows its own exception.
Unreadable owner slots fail closed; concurrent tasks and worker propagation have
dedicated regression coverage.

The destination regressions in `test_exposure_callback_diagnostics.py` also check
that failed predicates remain pending, later waiters resolve and later queued
events execute. This does not close constructor, outer transport, layout,
persistence or other callback diagnostics; E1 remains open.

### Shared inbound-message diagnostic boundary

`ViewRuntime.dispatch_message` now owns an outer diagnostic scope and handles
protected failures before they escape to transport/framework handlers. Actual
WebSocket receive and SSE HTTP `/message/` and `/event/` tests cover logs,
traceback-ring records, DEBUG error frames and Django technical error pages.
Protected event errors carry the request reference and leave a healthy connection
usable; failure to deliver the generic error attempts a close, with static logs
even if closing fails. Legacy exceptions still follow their original handlers.

An observed restriction survives exceptional unwinding through nested scopes,
including legacy → explicit in a handler followed by explicit → legacy in a
later failing hook. The outermost scope resets on all exits; cancellation is
not swallowed. Successful nested scopes retain their existing local cleanup.

This evidence concerns runtime-owned inbound messages. Direct initial HTTP/SSE
mounts, constructors, navigation replacement, WebSocket-only handlers and
exceptions already swallowed/logged by inner hooks remain distinct open routes.

### Remaining routes

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
