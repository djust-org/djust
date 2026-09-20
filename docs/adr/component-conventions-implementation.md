# ADRs 034–038: implementation sequence

This is an implementation ledger, not acceptance of the complete proposals.
The ADRs remain Proposed until their transport and security gates pass.

## Dependency order

1. ADR-038: typed per-instance state primitives, then separate rendering,
   server persistence, client snapshots and debug projections. Do not expose
   `exposure_policy="explicit"` until every automatic exporter consumes it.
2. ADR-036: shared strict parameter contracts, client collection metadata and
   equivalent validation across all dispatch paths.
3. ADR-035: authorized single-object form lifecycle, consuming the exposure
   contract. Additive public form-construction hooks can land independently.
4. ADR-034: typed component subscriptions, registry-only routing and optional
   client observations; verify two same-type components and stale identities.
5. ADR-037: shared checks and executable documentation throughout these stages;
   finish with catalogue/browser regression coverage and migration guidance.

## Implemented foundation

- Typed `state()` descriptor with instance-owned deep-copied defaults and lazy
  `default_factory`. A consumer mypy regression asserts both accepted reads and
  rejected assignments; nested changes are checked against content fingerprints.
- Public form construction hooks, empty-mapping binding, Django field initial
  values, prefixes and the `_create_form` compatibility bridge. Mount, reset,
  validation, submission and cache reconstruction use the hooks.
- Internal immutable exposure contracts compiled from state descriptors, with
  separate server/client/snapshot/debug projections, bounded JSON primitives,
  detached schema-checked restoration, and explicit inherited exposure grants.
  Defaults and unrelated properties are not evaluated during compilation.
- Internal server-session adapter with sync/async parity, exact supported Django
  storage implementations, required session/user/tenant/route binding, envelope
  expiry, and a total envelope resource budget. Cookie sessions and unreviewed
  custom backends are rejected. The staged HTTP GET/POST path uses this adapter;
  shared-runtime HTTP/WebSocket integration is described below. Sticky-child and
  actor persistence integration remains pending.
- Debug integration now consumes the explicit projection in observability
  assigns, initial/event debug-panel variables and sizes, runtime no-patch
  context diagnostics, and time-travel recording. Time-travel parameters and
  errors are redacted; observational records cannot restore, scrub components,
  or replay handlers. Legacy debug behavior is preserved.
- The staged explicit base-context path now uses deliberate kwargs and the
  component descriptor registry, action state and stream rendering providers;
  it does not discover public/private attributes, configuration, properties or
  state declarations. Native deliberately supplied ORM objects remain renderer
  inputs. Context processors use a copied rendering dictionary, preserve view
  precedence, and reject reserved provider collisions instead of overwriting.
  This branch remains behind the construction guard; all remaining persistence
  paths must stop saving render context before it can be enabled.

These changes **do not enable explicit exposure at runtime**. LiveView rejects
`exposure_policy="explicit"` and nondefault state exposure grants until automatic
exporters enforce the contracts. Legacy exposure and event policies are unchanged.
Managed `self.object`, strict event mode, scoped subscriptions and no-op client
observations remain pending. No generators should use the proposed APIs yet.

### Server persistence adapter boundary

`python/djust/_exposure_sessions.py` reuses Django's opaque session handle and
server storage rather than storing Python state inside the Rust view backend.
Supported concrete stores are Django database, cached database, cache, and file
sessions. The gate checks actual implementation identity, not `SESSION_ENGINE`
text, inheritance, or a self-asserted confidentiality flag. Custom stores require
a reviewed adapter; they currently fail closed. Deployment still determines
whether file/cache storage is shared across workers and durable.

The adapter validates current session identity before and after lazy loading,
requires explicit user/tenant/route identifiers, rejects legacy dictionaries and
changed schemas, and never restores attributes itself. Its digest is not a
signature: storage authenticity comes from the supported server-side session.
Transport integration must derive identifiers from trusted request/routing state,
perform fresh authorization, and remount on rejected restores. Permission checks
and managed-object resolution are **not** implemented by this storage helper.
Envelope expiry is separate from login-session expiry; Django remains responsible
for session cleanup. No client-storage fallback exists on write failure.

The initial codec only accepts bounded, exact JSON primitives. Additional codecs,
framework provider manifests, authenticated client snapshots, migration inventory,
and all runtime exporter integrations remain required before ADR-038 acceptance.

### Debug integration boundary

Explicit debug output reads only client-permitted descriptors; other declared
fields are redacted without evaluating their values or reporting their sizes.
It never falls back to attribute walks, rendering context, or object `repr` on
codec/default-factory failure. Such failures produce an unavailable-state marker
without logging exception text (which itself may contain private values).

`test_exposure_debug.py` checks actual observability JSON, consumer debug/time-
travel payloads, and the runtime diagnostic signal. Its explicit-view fixtures
deliberately bypass construction: **normal explicit mounts are still rejected**.
This does not prove full transport or browser coverage. Handler metadata is
withheld in explicit debug mode pending the typed metadata contract, and
debug restore/replay requires a future separate authorized restoration contract.
Observability reset/eval endpoints now reject explicit or unknown policies with
a static 409 before clearing state, replaying mount, parsing eval parameters or
invoking handlers. These direct mutation endpoints do not supply current runtime
identity, object authorization and event locking; redacting their output would
not authorize their side effects. Explicit mutations must use the live runtime.
Legacy reset/eval behavior is unchanged.

Bug capture rejects historical legacy records when the current view is explicit.
Explicit observational records are projected again through current debug-field
permissions before encoding, preserving historical permitted values rather than
substituting current live state. Undeclared keys are dropped and non-client fields
redacted. Tests assert the decoded shareable capture, not just a helper result.
Caller-supplied patches and custom scrub callbacks remain deliberate application
inputs; this is not protection against application code intentionally exporting
data. Error tooling and other raw exporters still need a complete audit before
activation. Display redaction alone is not acceptance of all debugging surfaces.

### Explicit rendering context boundary

The base context does not automatically expose `state()` fields. Applications
must deliberately add their rendering inputs through `get_context_data()`.
Component rendering uses the existing descriptor registry and rejects stale
entries replaced by properties. Instance-assigned components are not discovered;
their future migration/provider contract still needs completion. Actions and
streams contribute rendering namespaces only. A raw `view` context key is
rejected; no compatibility facade is provided yet. Form/upload providers and
nested provider inheritance remain acceptance work.

`test_exposure_context.py` covers no-reflection sentinels, kwargs/cache isolation,
reserved-name collisions, context-processor precedence, per-view component
binding, stale registries and deliberate native-model rendering in Django and
Rust template backends. These are direct context/renderer tests with deliberately
uninitialized gated views, not full HTTP/WS explicit-policy coverage. The separate
HTTP and shared-runtime integration tests below use real initialized views;
full transport coverage is still required before removing the guard.

### HTTP persistence integration

Explicit HTTP GET/POST saves only descriptor-selected `persist="server"` values
through the server-session adapter, not render context, tracked private attrs,
or legacy component snapshots. Trusted identity derives from the session handle,
middleware user and tenant, and request path. Missing authentication middleware,
missing configured tenant resolution, and unsupported identity types fail closed.
TenantInfo and Django model tenants use stable identifiers, not object strings.

On explicit HTTP POST, request-level authorization and mount hooks run first.
`mount()` reconstructs transient server dependencies on the new HTTP view;
validated stored fields overlay its defaults. Rejected schema/identity/expiry or
legacy dictionaries leave the fresh mount state in place. Existing object-level
authorization runs before handler dispatch/render. This reconstruction lifecycle
is explicit-policy-only; legacy restore still skips mount as before. Managed
object/form lifecycle ordering still requires ADR-035, and no claim is made that
mount side effects are transactional or exactly once.

`test_exposure_http.py` bypasses only the construction guard and exercises actual
GET/POST, rendering, authentication and database session storage. It checks
sentinels at HTML/JSON and persisted destinations, declared-counter restoration,
cross-user/tenant and schema rejection, legacy-state isolation, denied requests,
and cookie-backend rejection. This does not prove complete WS/actor/sticky-child
parity or enable the policy. Server state currently uses the adapter's one-hour
lifetime; configurable schema-version/codec and provider persistence work remains.

### Shared-runtime persistence integration

The staged explicit runtime path reconstructs transient dependencies with mount,
then overlays validated server fields before existing object authorization and
rendering. Event saves use the server projection independently of the legacy
client-snapshot opt-in. Explicit views neither consume nor emit legacy signed
snapshots; the separate explicit client codec is described below.

`test_exposure_runtime.py` exercises real runtime dispatch with a recording
transport and database sessions: three fresh runtimes restore successive values,
invalid schemas/extra keys/legacy dictionaries remount, denied reconnects do not
restore or write, and internal sentinels stay out of emitted frames. This is not
an actual WebSocket connection or complete SSE endpoint test.

The existing event-save timeout and best-effort error handling are retained.
The constructor guard remains in place; this integration does not enable
explicit-mode apps.

### Fresh event authorization

Explicit runtime events now require a fresh trusted transport request and compare
its session/user/tenant/route against an immutable mount binding before dispatch.
View permission checks run regardless of legacy `reauth_on_event` settings;
identity changes, missing requests and provider exceptions discard the view and
close with a static denial, without exception text. TenantMixin resolvers are
rerun, not read from their mount cache. Existing object/handler authorization
still runs downstream. Legacy views retain their optional reauthorization path.

The socket adapter reloads a concrete supported server session and uses Django's
`get_user` rather than the scope's cached principal. SSE consumes the current
owner-checked POST request once, preserving the mounted route for persistence.
The runtime captures each request before waiting on its explicit-event lock;
authorization, handler/render and save are serialized, and temporary save-request
state is cleared afterwards. Saves use the authorized event request and recheck
its identity, not the mount request. Actor events are explicitly refused while
their separate integration remains unfinished.

Tests cover denied/throwing auth hooks, changed user/tenant, missing request,
logout, inactive users, changed passwords, the SSE route/session boundary and
concurrent SSE request isolation. Channels WebSocket connection tests prove
save/reconnect restoration and rejection after session deletion, both without
tenancy and with a configured session tenant resolver and TenantMixin.

### Tenant-bound request and query context

In explicit mode, TenantMixin stamps its resolved tenant onto the request and
scopes HTTP dispatch/GET/POST query context around that tenant. Runtime mount
resolves the explicit view's tenant before permission hooks, matching HTTP's
ordering. Runtime mount restores its caller's tenant context on all exits
(including legacy mounts); this prevents a resolved tenant from escaping the
mount operation. Legacy auth sequencing and HTTP TenantMixin behavior remain
unchanged.

Tests assert matching tenant identities in permission hooks, persistence and
rendering, context restoration after success and missing-required-tenant errors,
rejection of an old runtime after a session tenant switch, and HTTP remount
instead of cross-tenant state hydration. Session-resolver coverage does not prove
all tenant middleware/custom/header/path resolver combinations. Live browser,
complete SSE endpoint, actor/sticky-child/component persistence, explicit client
snapshot and remaining provider tests are still activation gates.

### Explicit signed client snapshots

The staged runtime mount path now emits a separately salted TimestampSigner
envelope containing only fields with `persist="client"` (which also requires
`client=True`). Raw client permission alone does not grant snapshot permission.
The envelope binds session/user/tenant/route identity, schema, destination and
creation time. Its total signed UTF-8 length, including signature overhead, is
bounded; nested values use the shared exact-JSON byte/node/depth constraints.
There is no compression, arbitrary encoder or repr fallback.

Restore verifies the signature, lifetime (including future timestamps), complete
field set, schema and identity before returning detached values. Invalid input
leaves fresh mount defaults intact. Client fields and independently validated
server fields overlay the reconstructed view before existing object authorization.
The global snapshot switch and application restore veto apply. An explicit
restore sends fresh HTML rather than assuming cached markup matches the combined
state. Codec failures invalidate the cached snapshot with an explicit null,
without exception values or a legacy fallback. Signing does not conceal permitted
client values.

Tests cover codec rejection cases and runtime mount/restore frames, including
master switch, veto, codec failure, and exclusion of server/render-only values.
Successful authorized top-level events refresh this token on patch, HTML and
no-render acknowledgement frames. Identity is rechecked before capture; failures,
missing client declarations or the disabled master switch emit null rather than
leave an older token cached. Denied/failed events do not publish refreshed state.
Explicit no-render acknowledgements precede queued navigation, so its capture
sees the updated token; legacy side-effect ordering is unchanged.

WebSocket and SSE clients share opaque-token storage, scoped to the primary view
for event responses. Null clears the old entry; unrelated, background and error
frames cannot replace it. Bundle tests cover byte-for-byte navigation capture;
runtime tests cover event-to-remount restoration and acknowledgement ordering.

The navigation cache now evicts the source URL when no current token exists,
rather than retaining an older service-worker entry. State writes, per-URL
eviction, lookup and whole-state-cache clearing run in receipt order, with
service-worker lifetime extension. Redirect capture records the source before
history changes; Back navigation captures/invalidates the page being left before
destination lookup. Regression tests include a deliberately delayed cache write.
A real-browser harness verifies the bundled WebSocket/SSE clients through actual
service-worker messages and CacheStorage (eight checks); its transport frames are
synthetic, not proof of Django authorization or complete page navigation.

SSE endpoint navigation now resolves the destination through URLconf, reconstructs
a page request from the current authenticated POST, and mounts through the shared
runtime. POST turns and render/result application are serialized. A discarded
runtime is detached before replacement, preventing its background results from
being applied to the next page. Lazy Django authentication is resolved off the
event loop on stream creation and both POST endpoints.

The client retains its snapshot-routing identity after EventSource open, accepts
navigation links without requiring a WebSocket, and replaces old HTML on navigation
even when incoming markup already has `dj-id` values. Failed replacements fall
back to HTTP. After a stream disconnect, route-changing navigation reconnects to
the current page, not the original EventSource URL.

A real Django/ASGI browser fixture verified an event changing `initial` to
`latest`, navigation to a second view, Back restoring `latest`, and Forward
restoring the second page. Server frames and request logs prove these used the
same SSE session without a destination-page HTTP load. A separate real stream
shutdown verified reconnect creates a stream for the current second-page route.
The fixture bypasses the explicit-policy constructor guard only in its own process.
An earlier empty `dj-navigate` fixture performed a full load and was rejected as
evidence; the corrected fixture uses explicit destination attributes.

Complete snapshot restoration across reconnect, background state changes,
remaining direct state APIs and provider/child contracts remain activation gates.
SSE happy-path browser evidence does not establish WebSocket, cross-worker,
sticky-child, or all failure-path browser parity. Explicit policy is still
constructor-gated and ADRs 034–038 remain Proposed.

## Direct state API boundaries

The staged explicit policy now routes direct state reads through the same
declaration compiler and bounded JSON projection as the transport adapters:

| API | Explicit-policy behavior |
| --- | --- |
| `get_state()` | Detached values for `client=True` declarations only |
| `_capture_snapshot_state(strict=...)` | Detached values for `persist="client"` declarations only; strict validation regardless of the legacy `strict` flag |
| `_get_private_state()` | Rejects inferred private persistence; use the bound server adapter |
| `_capture_components_snapshot()` | Rejects reflective component export; a descriptor is not a state-exposure grant |
| `_restore_snapshot()` / `_restore_private_state()` | Rejects raw dictionaries before inspection or assignment; use validated bound adapters |

Unknown or unreadable policies cannot select legacy reflection. Invalid values,
resource limits, inherited grants and factory errors fail closed without leaking
exception values or retrying with context, `__dict__`, or a fallback encoder.
Policy lookup distinguishes a genuinely absent declaration from a descriptor
raising `AttributeError`; the latter cannot select the legacy default, including
in debug projections.
Declaring a field for rendering does not make it raw client state or a snapshot.

The legacy sticky adapter also rejects explicit children before reading context
or writing a session, and rejects explicit/mixed-policy restoration before any
public assignments. This is a **temporary unsupported boundary**, not completion
of sticky-child support. Its dedicated bound provider adapter, mixed-policy
semantics and restoration tests remain required before explicit mode can ship.
The production constructor guard is unchanged. New direct-API tests use
uninitialized synthetic views only; existing transport and sticky regression
tests cover legacy compatibility.

The audio mixin's private-restore override rejects nonlegacy policy before
filtering the payload; calling the guarded base method afterwards is too late
because Python evaluates the filtered argument first. A hostile-payload
regression failed before this entry guard and passes with it.

The setattr structural net now matches the exact developer-returned dictionary
application in its lexical scope instead of pinning source line numbers.
Canaries permit blank-line shifts but reject a client-payload substitution,
an extra adjacent assignment and the same block in a different scope.

## Bound child storage foundation

`python/djust/_exposure_children.py` now supplies an internal child-slot adapter
over the existing server-session envelope. It binds the current session, user,
tenant and route, every parent contract/schema in the slot ancestry, and bounded
JSON mount inputs. The child contract supplies its own class/schema binding and
selects only `persist="server"` fields. Same-type siblings and nested slots have
distinct storage keys. Parent/class/schema/input changes encounter and reject
the previous envelope in the same logical slot, avoiding one orphan per version
or object. Only the mount-input digest is retained; arbitrary objects and encoder
fallbacks are rejected.

This adapter returns validated values, not hydrated views, and does not grant
authorization. Its inputs must come from current server rendering/registration,
not client event parameters. It reuses session rotation, expiry, exact backend
allowlists, bounded envelopes and synchronous/asynchronous persistence from the
parent adapter. It neither reads rendering context nor creates client snapshots.

**Integration is partial and still gated.** The legacy sticky guards remain in
place; fresh eager explicit mounts use the separate adapter described below.
The lifecycle work includes:

1. Derive ancestry, stable slots and mount inputs from the trusted render/child
   registry. Fresh eager nested ownership is implemented; repeat-instance and
   changed-identity reuse remain to be completed.
2. Run fresh request authorization and `mount()` to reconstruct dependencies,
   overlay the validated server projection, then enforce current object
   permission before rendering or dispatch. Legacy skip-mount behavior must not
   be reused for explicit children. This sequence is implemented for fresh eager
   sticky children; event and preserved-instance integration remains pending.
3. Recheck preserved/live-instance identity before reuse. Current-request view
   and object authorization on both render reuse paths is now implemented as
   described below; this is not yet complete identity/lifecycle integration.
4. Connect HTTP and shared-runtime save/restore, with fresh binding and event
   serialization; explicit server grants must not depend on the legacy
   browser-snapshot opt-in.
5. Prune removed provider slots, define mixed-policy subtree behavior and prove
   failure/remount behavior without partially applying state.

`test_exposure_children.py` exercises real server sessions, copied-envelope
attacks (not only missing keys), independent sibling/nested state, ancestor and
mount-input changes, schema changes, parent-envelope substitution, rotation,
expiry, invalid payloads, readable-backend refusal and storage failures.
These adapter tests do not prove actual sticky lifecycle or browser support.

### Existing-child authorization prerequisite

The real `live_render` registered-instance and preserved-child branches used to
return before the fresh-child authorization checks. Both now update the child's
request, check current view authorization and enforce current object permission
before rendering or registering a preserved child for client reattachment.
Unexpected predicate failures deny reuse with a static error. Allowed reuse keeps
the same mounted instance and re-fetches its authorized object.

`test_exposure_child_reuse.py` exercises actual template rendering, registry
lookup/registration and authorization. Its ten initial legacy tests failed before
the fix. The expanded suite covers legacy and construction-gate-bypassed explicit
fixtures, logout, revoked view/object permissions, broken predicates, current
request use and allowed preservation without re-mounting. These are server-render
tests, not browser reattachment or per-event transport proof.

This prerequisite is a correction to existing legacy behavior as well as staged
explicit behavior. It does not wire `ChildStateSession` into the child mount/save
paths, resolve changed slot/class/mount identity, or remove the explicit gate.

### Fresh eager child lifecycle

The staged explicit `live_render sticky=True` fresh-child path now compiles its
scope from actual registered ancestry and detached server-render mount inputs.
An invalid/cyclic/unregistered ancestry cannot claim another child's scope.
Server-persisted children currently require all-explicit ancestry; transient
children do not require a server session or upgrade their legacy ancestors.

After current view authorization, the path prepares the adapter, calls `mount()`
to reconstruct transient dependencies, and rechecks schema and ownership against
the pre-mount binding before applying any saved values. Rejected envelope schema,
identity or expiry leaves fresh defaults intact. Object authorization runs on the
restored state before registration or initial persistence. The initial save uses
only declared server fields, independently of legacy browser-snapshot opt-in.
The legacy skip-mount restore path is unchanged.

Explicit sticky rendering no longer inserts a raw `view` object into context.
Nested rendering obtains its parent through the framework's scoped active-parent
context. Explicit context errors fail with a static error rather than logging
private exception values and rendering an empty fallback context.

`test_exposure_child_mount.py` covers actual Django template rendering, a real
HTTP GET through the normal parent view path, real database sessions, nested
registry ownership, initial saves, mount-before-restore, declaration replacement
during mount, view denial before mount and object denial after restore. The raw
view sentinel, declaration-replacement and context-failure regressions each
failed before their fix; disabling the load changes the actual rendered count.
The first test setup initially lacked configured tenant middleware state; that
fixture error is not evidence of a product failure.

The Decimal restore-site inventory recognizes only the exact explicit child
assignment block as a JSON-primitives codec boundary; it does not exempt the
whole module. A canary detects extra assignments, and an actual child restore
proves legacy Decimal-tag-shaped dictionaries stay ordinary JSON. Applying the
legacy decoder on this path would silently change those values.

The fresh-mount slice does **not** complete child-event saving, changed-identity reuse, lazy child
mounts, non-sticky provider scopes, pruning, mixed-policy persistence, cross-worker
restoration or browser reconnect coverage. Those remain activation gates, and
the explicit constructor guard is still unchanged.

### Shared-runtime child events

The staged explicit routed-child event path now uses the root runtime's freshly
authorized event request and existing explicit event lock. Before invoking a
handler it verifies live registry ancestry, the mount-time declaration schema and
bound child identity, refreshes the child's request, and checks current child view
permissions. Existing handler/object authorization then runs against that request.

After the handler it revalidates root/child binding, declarations, view and object
permissions, and saves only the routed child's declared server fields through
the bound adapter. This is independent of legacy browser-snapshot opt-in. The
storage await retains the 150ms deadline; timeout cancellation or persistence
failure sends a static error and no successful embedded update. Handler and
explicit render errors likewise do not echo or log their exception values.
Embedded rendering uses the active-child context and rejects raw-view context.

These are not transaction/rollback guarantees: handler side effects have already
occurred when a post-handler check or save fails. A render failure can occur after
the state was saved. The response asks for recovery rather than reporting a
successful update; no automatic handler retry is introduced.

`test_exposure_child_events.py` exercises real shared-runtime mount/event
dispatch, database sessions, emitted frames and restoration in a fresh runtime.
Handler-entry counters make stale-scope and revoked-permission tests fail if a
handler is entered, even if it later raises. The initial five checks failed
before wiring, and the render-error sentinel failed before the explicit error
boundary was added. Tests also cover post-handler scope/view/object denial,
storage failure, error redaction and bounded timeout cancellation.

The timeout inventory pins the exact three runtime save methods, not just a
substring count. The explicit child storage cancellation test exercises a
stalled backend; legacy best-effort timeout behavior remains separately tested.

This is not complete child persistence: parent-driven mutations/HTTP sweeps,
lazy/non-sticky children, pruning, mixed-policy scopes,
real-browser reconnect and cross-worker evidence remain required. The production
construction gate remains closed.

### Explicit child reuse identity

Both eager registered reuse and navigation-preserved reattachment now compare
a private mount-time identity before retaining an explicit child. It includes
the exact Python child/ancestor classes, declaration schemas, registered ancestor
slots and their recorded inputs, the child's detached mount kwargs, and current
user, tenant, route path and session identity (including backend type). A digest is only an equality token,
not authorization: fresh view/object permission checks still run, followed by a
second identity check before reuse. Fresh mounts also recheck after mount and
object authorization. Routed child events use this identity even when no fields
have server-persistence grants.

Transient reuse does not create, read or save session data. Without an established
session key, state can be reused within its current root instance, not transferred
to another root. Mixed-policy transient ancestry is likewise root-instance-bound
because legacy mount inputs do not carry an explicit provider contract. Cookie
session identifiers can identify transient reuse; they do not grant server
persistence. Only the identifier's hash enters the private identity metadata.

A valid but changed identity remounts in the same slot. The replaced instance is
removed from its old registry and the navigation preservation map before its
sticky unmount hook runs. The ordinary registered replacement also invokes its
unregister cleanup hook. Hook failure cannot resurrect the child and is logged
without private exception text. Invalid identity inputs fail closed instead of
falling back to legacy reflection. Existing legacy reuse is unchanged.

The eager tag now rejects combined sticky/lazy options before reuse, closing a
preservation shortcut around the existing incompatibility rule. Tests exercise
native Django rendering, real database session identifiers, matching and changed
identities, scope compilation for nested ancestry, sessionless reuse, cleanup
failures, authorization-induced identity changes and the real post-render
preservation scan. That reuse slice did not establish browser reattachment, recursive subtree
teardown, repeated-instance routing, provider pruning, or cross-worker parity.
Those remain required before activation; the production constructor guard stays
closed.

The post-render scan only accepts explicit survivors already validated and
registered by the tag. Bare `dj-sticky-slot` markup cannot substitute for the
declared class/inputs check; a regression reproduced that bypass before the scan
was restricted. Legacy bare-slot preservation keeps its existing behavior.

### Owned subtree disposal and async cancellation

The gated explicit lifecycle now detaches registered descendants before running
descendant-first cleanup. It removes forward/reverse ownership references, drops
queued/deferred work, cancels waiters on their owning event loop, and runs upload,
unregister and sticky-unmount hooks as appropriate. An idempotent disposal marker
prevents reentrant hooks, repeated teardown and re-registration of disposed
instances. A malformed alias to a child owned by another parent is unlinked, not
followed into that other subtree. Cycles are traversed iteratively.

This path is wired into identity replacement, unregister, denied preservation,
post-render discard, WebSocket disconnect/redirect disposal and SSE
replacement/shutdown for explicit roots. Legacy lifecycle routing remains in
place; its sticky-unmount hook now calls the implemented cancellation method.
Unregistering an explicit child under a legacy root still uses explicit disposal.
Cleanup is best effort: a broken application hook cannot skip siblings or
descendants, and the helper logs static errors rather than private exception
values. This is not session-envelope pruning or an application rollback.

Inspection corrected an earlier overstatement: the previous sticky-unmount
hook looked for `cancel_async_all()`, but that method did not exist. Invoking the
hook alone did not prove cancellation. The method now exists, and both shared
runtime and WebSocket background dispatch track per-view task handles and release
them on completion. Cancellation clears queued work, requests cancellation on the
owning loop, and increments a generation checked by their shared callback runner.
Even a coroutine that swallows cancellation cannot deliver a stale result/error.
Running synchronous code cannot be preempted; its application side effects may
continue even though its completion handler/render is suppressed. Independently
created application tasks remain application-owned.

Tests exercise actual task dispatch/cancellation in both runners, worker-thread
teardown of loop-owned waiters, native tag replacement, WebSocket disconnect,
post-render discard, and real SSE message navigation/shutdown. They do not prove
browser or cross-worker behavior. Parent-driven persistence, removed-slot/session
pruning and scoped child background dispatch remain open: the routed-child event
path currently calls the root async dispatcher, not a child-specific dispatcher.
The remaining providers and ADR034–037 work are still part of the objective.
Explicit exposure remains unavailable to applications.

## Readiness audit

The milestone-audit checklist was applied to the two foundation boundaries.
There are no database schema changes, new Celery tasks, or auditlog registrations
in this slice. Existing state decorators, private field storage, context
descriptor discovery, form caches and legacy overrides were identified before
implementation. No new third-party runtime dependencies are introduced.

The remaining exposure work must inventory actual sinks, not just callers:
HTTP sessions, WebSocket/actor persistence, signed snapshots, reconnect/live
navigation, embedded state, time-travel, debug output and component providers.
Server-only data cannot be placed in readable signed cookies. Registration and
restore must fail closed without turning an unavailable provider into a legacy
reflection fallback. These are implementation gates, not completed guarantees.

## Verification boundaries

The subtree-lifecycle slice completed 29,986 Python tests with 952 skipped across
all three roots (four workers), 29 focused lifecycle tests, and a 260-test async/
dispatch regression set. Full-package mypy passed 1,013 source files.
Unregister and deterministic cross-thread waiter tests failed before their fixes.
The initial full run stopped on lifecycle-unaware mock fixtures and the old SSE
expectation that abandoned work returned normally; corrected tests still assert
successful recovery frames or cancellation with no stale delivery, respectively.
The final full run verifies the frozen implementation. This is native server/
transport evidence, not browser or cross-worker verification.

The identity-aware reuse slice completed 29,957 Python tests with 952 skipped
across all three roots (four workers). Focused child identity/event/mount/reuse
coverage passed 114 tests, and full-package mypy passed 1,011 source files.
The changed-input tests and bare-slot reattachment test failed before their fixes.
An initial full run had two new fixture errors (monkeypatching an absent optional
method); after correction, the final run above verified the frozen implementation.
Native templates, shared-runtime events and the post-render transport hook were
exercised, not a live browser or cross-worker deployment.

The shared-runtime child-event slice completed 29,896 Python tests with 952
skipped across all three roots (four workers), 12 focused event tests and 21
combined legacy/explicit timeout tests. Full-package mypy passed 1,009 source
files. The prior full run's single failure was the old two-site timeout inventory,
now replaced by the exact method inventory described above. These tests use real
runtime dispatch and database sessions with a recording transport, not a browser
connection or a cross-worker deployment.

The fresh eager-child slice completed 29,884 Python tests with 952 skipped
across all three roots (four workers). Focused mount/Decimal-inventory coverage
passed 119 tests, the earlier mount/reuse/adapter/legacy-restore set passed 81,
and full-package mypy passed 1,008 source files. An initial full run's single
failure identified the codec inventory assumption corrected above. The actual
HTTP GET and native template tests do not establish browser or cross-worker
coverage, and the production explicit gate remains closed.

The child-reuse authorization slice completed 29,866 Python tests with 952
skipped across all three roots (four workers), 24 focused reuse tests and
full-package mypy across 1,007 source files. This verifies the stated native
server-render paths, not browser reattachment or completed explicit persistence.

The bound-child adapter slice completed 29,842 Python tests with 952 skipped
across all three roots (four workers), 81 focused child/session checks and
full-package mypy across 1,006 source files. No lifecycle or browser activation
is inferred from those adapter-level results.

The direct-state API slice completed the full Python run across all three roots:
29,809 passed and 952 skipped (four workers; benchmarks disabled under xdist).
The focused direct-state/structural tests passed 62 cases, and full-package mypy
passed all 1,004 source files. The preceding full run's only two failures were
obsolete line-number whitelist entries, replaced by the exact AST matcher and
mutation canaries above. Audio's pre-filter rejection also has failing-before,
passing-after evidence. No JavaScript or Rust implementation changed in this
slice; these Python results do not establish browser or Rust-suite coverage.

The SSE slice's full Python run at `e5230a5b0` across all three roots completed
with 29,761 passed and 952 skipped (four workers; benchmarks disabled under xdist).
An earlier run exposed an obsolete SSE no-op-lock expectation and a source pin
predating the explicit-policy mount-HTML rule. The lock test now asserts actual
serialization, and the mount pin preserves the legacy-only rule. All 1,952
JavaScript tests pass, as do full-package mypy and pre-commit checks. These counts
do not establish ADR acceptance, performance guarantees, or Rust-suite coverage.

The earlier DB-session import-ban correction remains narrowly scoped: the imported
class is used only in the explicit adapter's concrete implementation allowlist,
not instantiated. A cache-only explicit save/restore test forbids database access.

Tests are in `python/djust/tests/test_state_descriptor_contract.py`,
`test_state_descriptor_typing.py`, `test_form_hooks_adr035.py`,
`test_exposure_contract.py`, `test_exposure_policy_guard.py`, and
`test_exposure_sessions.py`, `test_exposure_debug.py`, and
`test_exposure_context.py`, and `test_exposure_http.py`. Run these with
the existing form, decorator, state and WebSocket regression suites. This slice
now also includes `test_exposure_sse_navigation.py` and bundled JavaScript SSE
navigation tests. The browser evidence above covers only the stated sequences;
it does not establish cross-worker or complete explicit-exposure parity.
