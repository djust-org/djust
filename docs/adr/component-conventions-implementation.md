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

The SSE slice's full Python run across all three roots completed with 29,758
passed, 952 skipped, and two failures: an obsolete SSE no-op-lock expectation
and a source pin predating the explicit-policy mount-HTML rule. The lock test now
asserts actual serialization, and the mount pin preserves the legacy-only rule;
the affected 23-test set passes. The full suite must be rerun after these changes.
All 1,952 JavaScript tests pass. These counts do not establish ADR acceptance.

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
