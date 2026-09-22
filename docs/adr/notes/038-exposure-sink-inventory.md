# ADR-038 exposure sink inventory

The inventory that closed E1. `exposure_policy="explicit"` is activated on the
completion branch (#2954); see the ledger's *ADR-038 activation review*.
Entries identify real producers and destinations with their evidence. An
implemented projection is not proof that every caller uses it, which is why
each row names a destination-level test.

## Classified entry points

| Destination | Producer / boundary | Evidence and remaining closure |
| --- | --- | --- |
| Template context | mixins/context.py: ContextMixin._get_explicit_context_data and context processors | test_exposure_context.py covers declared context/provider boundaries. Dynamic/inherited provider closure remains E2. |
| Rust render state and render caches | mixins/rust_bridge.py: _sync_state_to_rust, preloaded_context, cached Rust views | test_exposure_render_cache.py exercises actual Rust rendering and the memory backend. Explicit initialization bypasses the shared legacy cache entirely; context remains instance-owned. Actor render state is unreachable for nonlegacy views; see the actor row. |
| HTTP HTML and embedded configuration | mixins/request.py and mixins/template.py | test_exposure_http.py exercises staged HTTP paths; full provider/renderer matrix is E5. |
| HTTP API responses (ADR-008) | api/dispatch.py: dispatch_api `{"result", "assigns"}`; dispatch_server_function `{"result"}` | `result` is the handler's own explicit response (outside the automatic contract, D6). `assigns` was automatic and unclassified: `_public_assigns_snapshot_diff` returned every changed public attribute with no policy check, so an explicit view's undeclared attribute reached the HTTP client while its declared `client=True` field was missing. For a nonlegacy view it is now the diff of the `client` projection `get_state` uses; an unavailable projection yields no fields. test_exposure_api_dispatch.py, red against the original. |
| Direct client state | live_view.py: get_state | test_exposure_state_apis.py asserts client grants and sentinel absence at returned values, including failed/unknown policies. |
| Raw client snapshot state | live_view.py: _capture_snapshot_state | Same direct API suite; only snapshot grants. Raw private/component export and unverified restore are rejected. |
| Signed browser snapshot | _exposure_snapshots.py: ClientSnapshot.capture/restore; runtime mount/event frames | test_exposure_snapshots.py and test_exposure_sse_navigation.py. Browser storage delivery and cross-worker/failure matrix remain E5. |
| Browser service-worker storage | Writer: 03-websocket.js storeSignedSnapshot (WS :529, SSE 03b-sse.js:196) → 46-state-snapshot.js _serializeCurrentState → 33-sw-registration.js captureState/forgetState → service-worker.js putWithLRU into CacheStorage `djust-state-cache-v1`. Reader: 18-navigation.js lookupStateForUrl → STATE_SNAPSHOT_LOOKUP. | Classified. tests/js/exposure_sw_state_storage.test.js runs the real client into the real worker and asserts the persisted bytes: only the signed token, verbatim, in a fixed {url, view_slug, state_json, ts} envelope; ineligible child/background/error frames cannot reach storage; a null revocation deletes the entry. Each assertion is mutation-checked. At-rest lifetime and logout residue remain open; see *Service-worker state storage finding*. |
| Declared server session state | _exposure_sessions.py: ServerStateSession.save/asave/load/aload | test_exposure_sessions.py covers bound envelopes and backend capability checks. Do not equate this adapter with every state_backend caller. |
| Legacy/Rust state backends | state_backend.py and state_backends/{base,memory,redis}.py. Every `get_backend()` writer: mixins/rust_bridge.py:425, :460, :491 (`backend.set` of the serialized Rust view); components/_interactive.py:414 (`_register_observation`) and :234 (`_claim_observation`); session_utils.py (`cleanup_expired`, `get_stats`); cli.py (operator commands). | The three `backend.set` calls carry render state; explicit initialization bypasses backend resolution/read/write. Observation writes are value-free: key `__observation__:obs_<uuid4 hex>`, value an integer sequence (Redis) or `(sequence, expiry)` (memory) — no application data. session_utils and cli write no payload, and cli is not automatic. Actor integration remains open; this is not cross-worker acceptance. |
| Root WS/SSE foreground frames | runtime.py: dispatch_mount, _dispatch_event, _dispatch_single_event, _render_and_send | Staged runtime/HTTP/SSE tests exist; full destinations and debug/provider matrix still open. |
| Root background frames and logs | runtime.py: _execute_async_task, _render_async_result | Diagnostic sentinel regression added by this inventory. Authorization, persistence, signed-snapshot refresh and render failures remain E3/E1 closure work. |
| Explicit child results/state | _child_async.py and _exposure_child_persistence.py | test_exposure_child_async.py and child lifecycle suites cover selected eager/sticky owners. Parent/mount queued work and unsupported lifecycle combinations remain E3. |
| Actor mount/event frames | Two runtime entrances only: the actor mount call (runtime.py:2883) and the actor event call (:3283). Every other actor call — WSConsumerTransport.dispatch_actor_mount's create_session_actor/actor_handle.mount (:1528, :1548) and dispatch_actor_event's actor_handle.event and deferred flush (:1281, :1204) — is reachable only through those two. SSE raises on all four hooks. | Classified **unsupported, refused at entry**. The mount refusal (:2349) runs before lifecycle/transport registration; test_exposure_actor_mount.py checks actual single/batch WebSocket dispatch and surviving sibling mounts. The event refusal (:3268) re-checks the current policy per event and is reachable only after a late transition from a legacy actor mount; test_actor_event_refuses_after_a_late_policy_transition covers explicit/None/invalid with a legacy control that must reach the actor, and fails on all three when the refusal is removed. Actor render state is therefore unreachable for nonlegacy views. Supporting actors under explicit policy remains an implementation-or-exclusion decision before activation (E6). |
| Debug assign endpoint | observability/views.py: _lenient_assigns/view_assigns | test_exposure_debug.py asserts actual response bytes and redacted defaults under DEBUG. Debug mutation is not ordinary authorized event dispatch. |
| Time-travel history and restore | time_travel.py: record_event_start/end, restore/replay paths | test_exposure_debug.py covers projected records and denied raw restoration. Explicit records are not legacy-restorable snapshots. |
| Bug capture/export | bug_capture.py: encode_view_state; above `bug_capture_inline_limit`, `BugCapture.encode` writes to the configured SnapshotStore (bug_capture.py:180) and emits only a reference | Same debug suite covers historical provenance and reprojection; cannot reuse legacy history after policy changes. test_bug_capture_store_destination_holds_only_the_debug_projection asserts the store path was taken and reads the stored bytes; mutation-checked against a skipped store path and against unprojected bytes reaching the store. |
| Django fragment cache (`{% cache %}` tag) | template_libraries.py: CacheTagHandler.after_body (:1489) → Django `caches[...]` | Developer-invoked, not an automatic exporter: it stores rendered output the template author asked to cache, derived from the template-context projection, so it adds no exposure beyond that row. Key and `vary_on` semantics are Django's; varying on the user where output is per-user remains the author's responsibility, as in Django. |
| Runtime debug frames | runtime.py transport debug projection | Inspect all transport diagnostic hooks, not only the observability endpoint. Existing projection is not exhaustive error/traceback evidence. |
| Exception logs, DEBUG responses and traceback ring | security/error_handling.py: handle_exception; observability/tracebacks.py: record_traceback | test_exposure_mount_diagnostics.py covers runtime initialization, auth, mount, handle_params, initial render and actor-render catches at frames/logs/ring destinations. The shared redacted mode does not inspect exceptions or record traceback data. Foreground events are closed by test_exposure_event_diagnostics.py and runtime-owned inbound messages by test_exposure_transport_diagnostics.py. Still open: entry points no protected scope covers (HTTP GET/aget, the SSE stream GET and navigation replacement, the WebSocket `receive` catch-all, view construction) — completion plan item E1-1. |

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

## Service-worker state storage finding

The destination is **CacheStorage**, which the browser persists to disk across
tab closes and restarts — not the worker's in-memory reconnect buffer. What is
persisted is exactly the server's signed token plus a fixed envelope, and only
from eligible frames; `tests/js/exposure_sw_state_storage.test.js` pins all
three properties at the stored bytes. Signing gives integrity, lifetime and
binding, not confidentiality, so the token's granted fields are readable at
rest. Three findings follow; none is fixed in this slice.

1. **No at-rest lifetime.** `lookupCached(STATE_CACHE, …)` returns an entry
   without checking `ts`; the VDOM cache has a TTL, the state cache has only
   the 50-entry LRU (`STATE_MAX_ENTRIES`). The server rejects an expired token
   on restore, but its readable bytes remain on disk. Belongs to **E5**
   (expired restores) and must be decided before **E6** activation.
2. **No logout or identity-change clearing.** The worker handles
   `DJUST_CLEAR_STATE_CACHE`, but no client or server code sends it. After
   logout, the prior identity's token stays in CacheStorage. Binding stops a
   cross-identity *restore*; nothing stops the next user of the same browser
   profile reading the granted fields. Belongs to **E5** (cross-identity) and
   **E6**.
3. **Keyed by pathname only.** Capture keys on `window.location.pathname`
   (`18-navigation.js:213`) and lookup on `url.pathname` (`:452`), so
   `/orders?page=1` and `/orders?page=2` share one entry and back-navigation
   can offer the wrong query's token. A restoration-correctness question, not
   an exposure one; belongs to **E3**.

## Consumer-local exception logging finding

`handle_exception` refuses to stringify an exception for a nonlegacy owner
because undeclared state can occur in its message. Raw `logger` calls ignore
that: `diagnostics_allowed()` is consulted only by `handle_exception`, and the
`djust` logger's `DjustLogSanitizerFilter` strips control characters, not
values. An AST scan of `websocket.py` finds 44 logging calls that carry
exception data — the exception in the arguments, `logger.exception`, or
`exc_info`. (An earlier single-line regex count was also 44 but a different,
wrong set: it missed multi-line calls such as `db_notify` and `_run_tick`.)
Every site is classified below by function; line numbers drift, names do not.

The classification below is executable: `python/djust/tests/test_log_exposure_pin.py`
scans `websocket.py` with the same AST rule and requires every site to
appear in exactly one of its `HELPER`, `LEGACY_GATED`, `FRAMEWORK_ONLY` or
`KNOWN_OPEN` tables, keyed by function and message literal. A new site
fails until classified; a fixed open site fails until its entry is deleted.

**Fixed — routed through `_log_view_hook_failure`.** The helper takes the
call site's own `msg`/`args` and `traceback=True` for `logger.exception`, so
legacy output is unchanged by construction; it checks the hook's view and the
current owner at the logging boundary. Each reproduced site fails before the
fix for explicit/None/invalid and a legacy control proves the hook ran.

- Reproduced: `handle_presence_heartbeat`, `handle_cursor_move`, `server_push`
  (via `apush_to_view`), `_run_tick` (`handle_tick`), `db_notify`'s
  `handle_info` catch (via the NOTIFY channel group), and `disconnect`'s
  `untrack_presence` cleanup.
- Both deferred-callback twins: `ViewRuntime._flush_deferred` and the consumer's
  `_flush_deferred` (via `server_push` with `_skip_render`). Besides the
  exception, their `repr(callback)` fallback logged a `functools.partial`'s bound
  arguments; the value-free line drops both.
- The consumer's `_flush_pending_layout` twin (via `server_push` with
  `_skip_render`). Only `legacy` and `explicit` can reach a layout render:
  `None` and invalid policies fail closed in `get_context_data` first, so the
  test asserts the render ran instead of trusting a value-free line.
- Converted in the same function, **not independently reproduced**: the
  `db_notify` outer catch and its deferred-activity flush catch.
- `_mount_one` (`mount_batch`) leaked to the **client** as well as the log:
  under DEBUG it put `str(exc)` in the batch's `failed[]` entry. The failed view
  may never have become `view_instance`, so the owner is the class the batch
  entry names, resolved by the shared allowlist-first `resolve_view_class`; an
  unresolvable class fails closed. Its trigger is synthetic (`handle_mount`
  stubbed to raise), since the catch is the batch's last line of defense for
  whatever escapes rather than for one known path.

**Legacy-gated — verified by reading the guard:** sticky `_on_sticky_unmount`
in `disconnect` and `handle_live_redirect_mount` (nonlegacy children go to
`dispose_child_subtree` first; the staging block's outer catch sees only
framework exceptions); `render_embedded_child_html` (a nonlegacy child raises
a value-free `ExposureError … from None` before logging); time-travel jump,
component jump and forward replay (`restore_snapshot`,
`restore_component_snapshot` and `replay_event` return `False` for a nonlegacy
view before any re-render); `disconnect`'s upload cleanup (runs only when
the view was not disposed as nonlegacy).

**Fixed with #2946:** `_run_async_work` (`start_async` callback and
`handle_async_result`), once the dropped-`start_async` defect was fixed and made
it reachable from the NOTIFY drain. No pinned site is open; the pin's
`KNOWN_OPEN` tables are the authoritative list.

The pin does not cover client error frames that interpolate an exception
(`send_error("…%s" % exc)`); `handle_bug_capture_share` did exactly that and is
fixed. The pin now covers direct interpolation in client frames too: four
sites in `websocket.py`, all legacy-gated; `runtime.py` has none. Indirect
flows (`detail = str(exc)` passed later) are not visible to it.

**Framework-only** (message not derived from application values):
`_find_sticky_slot_ids`, `_clear_live_handles` (teardown step names — confirm
no application hook runs there), `_flush_accessibility` (two sites),
`_attach_debug_payload`, the actor shutdown in `disconnect`,
`_handle_upload_resume` (two sites), `_send_frame`, `_clear_template_caches`,
`disconnect`'s db_notify group leave, waiter cancellation (scheduling only)
and child unregistration (nonlegacy children disposed, legacy hooks caught
inside `_unregister_child`),
`handle_live_redirect_mount`'s upload cleanup, and hot reload (three sites,
dev-only and file-derived).

**Separate defect found while reproducing `_run_tick`** (not an exposure
issue, not fixed here): the tick task is created during mount
(`ViewRuntime`'s `on_view_mounted`) but the consumer's `view_instance` is
assigned after `dispatch_mount` returns, and `_run_tick` stops on its first
wake-up if the view is not set yet. A view whose `tick_interval` is shorter
than its mount time never ticks — 20 ms reproduces it; 300 ms does not.

## Runtime and mixin exception logging

The consumer finding's rule applies wherever a raw `logger` call carries
exception data, and a runtime turn's diagnostic scope does not change that —
only `handle_exception` consults `diagnostics_allowed()`. Scanned counts:
`runtime.py` 26, `time_travel.py` 10, `mixins/request.py` 5,
`mixins/async_work.py` 4, `mixins/sticky.py` 3, `live_view.py` 2, and one each
in `sse.py`, `mixins/rust_bridge.py`, `mixins/activity.py` and
`mixins/waiters.py`. Pinned now: `websocket.py`, `runtime.py`, `live_view.py`,
`sse.py`, and the `sticky`, `rust_bridge`, `activity`, `waiters` and
`async_work` and `request` mixins, and `time_travel.py`. Package-wide there
are 286 exception-carrying log calls in 79 modules; the 214 in the other 70
modules are unclassified and frozen in `tests/fixtures/log_exposure_unreviewed.json`
under a ratchet (new sites fail; the baseline only shrinks). As of this
writing the baseline is **empty**: every exception-carrying log call in the
package is pinned, fixed, or turn-gated. The last five, in the owner-less
template backend and PWA sync endpoint, were closed by #2951; see the ledger.
The two consumer `_run_async_work` sites held `KNOWN_OPEN` behind #2946 are
converted now that the fix makes them reachable; no pinned site is open. The PWA offline cache was checked and is in-process server
memory, not a browser-storage sink.

`runtime.py`'s 26 sites (two identical sticky-unmount messages in
`on_mount_render_ready` are keyed separately): **10 legacy-gated**, each by a
guard read in place — `if diagnostics_allowed()` for the deferred drain,
deferred dispatch, waiters and time-travel hook; `if legacy_diagnostics and
uses_legacy_exposure(view)` for both async-task catches; `uses_legacy_exposure(child)`
for both sticky hooks; the actor refusal for the actor flush; and
`opt_in and legacy_exposure` for snapshot restore. **7 framework-only**:
NOTIFY group join, the DJE-053 diagnostic, debug decoration, the observability
registry, the `sticky_hold` send and two accessibility flushes. **9 open** at pinning, since reduced:
`get_presence_key` at presence setup (fixed: reproduced at mount, explicit
from mount, now `log_failure` at WARNING); the `full_html_update` Django signal
(fixed: receivers' exceptions arrive via `send`; `log_failure` at DEBUG); the WS and SSE event
re-auth checks (reclassified legacy-gated: only legacy views are re-checked); `state_snapshot_signed` emission (reclassified legacy-gated: a
nonlegacy view reaches only the explicit branch, wrapped in its own catch); both
post-event state saves (fixed: storage exceptions can carry `persist="server"`
data; `log_failure`); scoped component render (fixed, unit-level evidence; E2
gates an end-to-end explicit reproduction).

Fixed: `ViewRuntime._flush_pending_layout` now logs through
`_exposure_diagnostics.log_failure` (reproduced, explicit from mount). Already
known to be value-free: `mixins/activity.py`'s drain logs "Protected deferred
activity event failed" for restricted owners.

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
