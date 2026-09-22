# ADRs 034–038: implementation sequence

This is an implementation ledger, not acceptance of the complete proposals.
The ADRs remain Proposed until their transport and security gates pass.

## Consumer hook diagnostics — E1 slice

`LiveViewConsumer.receive` dispatches `presence_heartbeat` and `cursor_move`
straight to view methods an application may override, and their catches
logged the stringified exception with no policy check — the leak
`handle_exception` exists to prevent. Reproduced over the real consumer: for
explicit, `None` and invalid policies the hook's sentinel reached the log
(6 of 8 cases failed; the legacy controls passed, proving the hooks ran).
Both now log through `_log_view_hook_failure`, which checks the hook's view
and the current owner at the logging boundary and emits the value-free line
for nonlegacy owners. All 8 cases pass; the 82 existing presence, cursor and
heartbeat tests are unchanged; mypy is clean.

The same pattern is wider. The consumer has 44 exception-logging sites that
bypass `handle_exception`; about twenty wrap application code — embedded child
and layout renders, deferred and async callbacks, sticky unmount hooks,
`mount_batch`, time travel, bug-capture share, `server_push`. The inventory's
*Consumer-local exception logging finding* lists them by line with a
framework-only classification for the rest. They are the next E1 slice and
each needs its own reproduction. E1 remains open.

## Actor caller inventory — E1 slice

The actor system has exactly two runtime entrances — the actor mount call
(`runtime.py:2883`) and the actor event call (`:3283`) — and every other actor
call is reachable only through them. Both are refused for nonlegacy views, so
actor render state is unreachable under explicit policy; the inventory now
classifies actors as **unsupported, refused at entry**.

The mount refusal already had a test. The event refusal (`:3268`) had none,
because it cannot be reached by mounting a nonlegacy view: only a late policy
transition after a legacy actor mount gets there.
`test_actor_event_refuses_after_a_late_policy_transition` does exactly that
over the real WebSocket consumer, for `explicit`, `None` and `invalid`, with a
`legacy` control that must reach the actor so the refusal cases cannot pass by
bypassing the actor branch. Removing the refusal fails all three nonlegacy
cases and leaves the control green.

With this, the three caller inventories the earlier E1 slices named — browser
storage, backends and actors — are classified. E1 itself stays open: the
runtime debug hooks, the outer transport and foreground-event diagnostic
catches, and the foreground frame matrix are still listed as open in the
inventory, and remaining routes 2-5 are not closed.

## Backend and store writers — E1 slice

The inventory's backend row claimed the three `backend.set` calls in
`RustBridgeMixin._initialize_rust_view` were the automatic Python writers.
Enumerating every `get_backend()` caller found more writers to the same
backend: the ADR-034 C2 observation claims and registrations
(`components/_interactive.py:234`, `:414`), plus `session_utils` and the CLI.
The observation writes are value-free — a `uuid4` lifetime key and an integer
sequence in both the memory and Redis backends — so the claim about
*application* data holds, and the row now names every writer.

Two destinations outside the state backend are now classified. The bug-capture
snapshot store (`bug_capture.py:180`), used above the inline limit, was never
exercised by an exposure test; `test_bug_capture_store_destination_holds_only_the_debug_projection`
forces the store path, asserts it was taken, and reads the stored bytes. It was
mutation-checked against a skipped store path (which would otherwise pass
vacuously) and against unprojected bytes reaching the store. Django's
`{% cache %}` fragment cache (`template_libraries.py:1489`) is developer-invoked
and stores rendered output derived from the template-context projection, so it
adds no separate exposure.

E1 remains open. The actor caller inventory was then the remaining named E1 task.

## Service-worker state storage — E1 slice

Closes the browser-storage row of [the sink inventory](notes/038-exposure-sink-inventory.md),
which the previous E1 slice named as next. The writer and reader chains are
cited there. The destination is CacheStorage, persisted to disk.

`tests/js/exposure_sw_state_storage.test.js` joins the real client bundle to the
real service worker through the actual `postMessage` bridge and asserts on the
bytes CacheStorage holds, with sentinels in every frame field outside the
token: only the signed token persists, verbatim, in a fixed four-key envelope;
child-view, background and error frames cannot reach storage; a `null`
revocation deletes the entry. Each of the three tests was mutation-checked —
persisting the whole frame, removing the eligibility gate, and disabling the
worker's forget each fail exactly their own test.

Three findings are recorded, not fixed: no at-rest TTL on state entries and no
logout/identity clearing (both **E5**, decided before **E6**), and pathname-only
keys that collapse query strings (**E3**). E1 remains open; the actor and
legacy/Rust backend caller inventories were then the remaining E1 closure tasks.

## Root and deferred background acknowledgements — E4 slice

Root event dispatch and deferred redispatch now capture named and legacy
background work before acknowledging the event. Noop and rendered responses
advertise an opaque batch token; completion is sent only after every captured
task settles. Dispatch verifies the captured root is still the runtime owner.
This reuses the child batch contract without treating intermediate task renders
as completion of the originating request.

The real SSE endpoint regression failed before the fix because named tasks had
no async acknowledgement metadata. Coverage now includes rendered and noop
responses plus direct deferred redispatch through the real session runtime.
The focused suite passed 30 tests. The full Python suite passed 30,044 tests
with 952 skipped before the final two deferred test cases were added; mypy
passed all 1,021 source files. Native browser clicks against a temporary local
server passed over both WebSocket and SSE: loading stayed active through two
task updates and cleared on batch completion. That browser fixture exercises
legacy root dispatch, not explicit-root authorization or cross-worker restore.

E4 remains open: separate component-dispatch and mount callers still use the
legacy queue drain. Root async authorization/persistence remains in E1/E3.
Queues created after batch capture, failure ordering and the remaining lifecycle
matrix require further coverage. No ADR is accepted by this slice and the
explicit-exposure construction guard remains in place.

## Component-route background acknowledgements — E4 slice

The separate component dispatch route now advertises the same captured batch
for noop, subtree patch and full-page responses. All three dispatch that exact
batch after the acknowledgement. Background queue/cancellation bookkeeping is
classified as framework-internal for change detection, preserving noop and
subtree rendering. Capturing an empty batch no longer creates an uninitialized
task queue (which could otherwise break later unnamed task scheduling).

The focused runtime, child-routing, SSE and batch suite passed 72 tests. Native
browser clicks against a temporary server passed over WebSocket and SSE: a
descriptor component's foreground subtree patch and both background patches
kept the button disabled; batch completion released it. No synthetic frames
were supplied by the browser harness. This verifies the legacy-root descriptor
component route, not explicit-root authorization or cross-worker restoration.

The full Python run passed 30,049 tests with 952 skipped; mypy passed 1,021
source files. A subsequently added unnamed-task regression passed with all
five batch tests; that extra test is not included in the full-run count.

Mount callers and legacy child dispatch remain on the lifecycle checklist;
this slice does not close E4 or activate explicit exposure.

## Legacy child-owned background dispatch — E3/E4 slice

Legacy embedded events now capture and acknowledge the selected child's task
batch rather than draining the root queue. Callbacks and result handlers run on
that child; successful results render an async embedded update and use the
existing sticky-child persistence policy. Registry identity and disposal/
generation checks before dispatch and after asynchronous boundaries suppress
late result handling and delivery for removed or replaced owners. Completion
still releases the acknowledged batch even when delivery is suppressed.

The new runtime tests verify child results, untouched parent queues, removed
owners and completion. Temporarily restoring the old parent dispatch caused
both tests to fail; the corrected tests pass. The affected runtime, async,
explicit-child and sticky-child regression set passed 455 tests, and mypy
passed 1,021 source files. This slice has no new live-browser or cross-worker
evidence and does not claim the legacy best-effort persistence policy is the
explicit exposure contract. The strict explicit-child path remains separate.

Mount-time work has no originating event request: do not attach its completion
to a later foreground request. Its ownership/failure matrix still needs review
before E4 closes, along with the remaining client fallbacks.

Additional bundled-client tests verify that mount-time async patches with no
event name cannot acknowledge a later foreground request, and that legacy
no-ref replies are accepted only when one request is outstanding. Both rules
pass for WS and SSE. The full JavaScript suite passed 2,015 tests in 188 files.
These are client frame tests, not live mount/reconnect deployment evidence.

## HTTP/cache correlation — E4 slice

HTTP fallback and cache-hit operations now participate in the shared request
registry. Their awaited operation owns completion locally; no server-supplied
ref is trusted to settle another request. Cleanup runs in finally, so an HTTP
failure or cache application failure releases only that operation's loading.
Teardown keepalive requests remain detached and never allocate foreground refs.

Both HTTP overlap tests failed before the fix: the first response cleared the
other request's loading, for success as well as failure. They now pass, as does
a real cache-hit path interleaved with pending HTTP work in the bundled client.
The focused HTTP/socket tests passed 49 cases before the cache case was added;
all three new HTTP/cache cases pass. These tests drive the actual bundle with
controlled fetch promises; they are not live Django/browser HTTP evidence.
The full rebuilt-client suite passed 2,018 tests in 189 files; bundle ESLint
passed with zero warnings. No Python implementation changed in this slice.

Remaining E4 audit items include page replacement while HTTP work is pending,
transport ownership of buffered unsolicited patches, and failure/recovery
ordering. Keep those separate from mount-time frames, which do not own a
foreground request. This slice does not close E4.

## Outgoing HTTP response ownership — E4 slice

HTTP fallback captures the root DOM owner, URL and navigation generation before
sending. Responses are discarded before application if those change while
waiting for headers or parsing the body. The navigation generation also covers
same-URL navigation that reuses the root node. Teardown remains detached and
the existing finally cleanup releases each local request.

Two replacement tests failed before the guard, demonstrating that stale page
metadata reached the new page from either await boundary. The final fixture
also covers the native before-navigate signal without changing the DOM or URL.
These controlled-fetch bundled-client tests do not establish live browser
navigation or cancellation of a fetch that never settles. Buffered socket
update ownership and navigation/disconnect cancellation remain E4 audit items.
The final rebuilt-client suite passed 2,021 tests in 189 files and bundle
ESLint passed without warnings. The earlier six fragment-test failures were a
window stub missing addEventListener, corrected without changing their asserts.

## Navigation cancellation of ordinary HTTP requests — E4 slice

Ordinary HTTP fallback now passes an AbortController signal to fetch and
tracks it until the operation exits. Native djust/Turbo navigation signals
and pagehide abort the outstanding ordinary requests. Their finally blocks
release request/loading ownership, and intentional aborts are not reported as
HTTP failures. Teardown keepalive sends are never registered for cancellation.

Three new tests failed before the signal was added. They exercise navigation,
Turbo navigation and page exit, asserting that the pending handler settles,
loading/ref state clears and a subsequent request has an un-aborted signal.
The tests use abort-aware fetch doubles, not a real browser network transfer.
The full rebuilt-client suite passed 2,024 tests in 189 files, including the
existing keepalive teardown regressions; bundle ESLint passed with no warnings.
Buffered socket-update ownership remains open; no ADR is accepted by this slice.

## Buffered socket-update ownership — E4 slice

Buffered server updates now carry client-only transport ownership in a WeakMap,
not a forgeable frame property. Disconnect/error cleanup and root/noop/embedded
reply drains consume only that transport's entries. Buffering/draining consults
its pending requests rather than blocking on requests from another connection
or HTTP operation. Existing contiguous-version consumption and deferred-frame
recovery markers are unchanged. Unknown error references cannot discard work.

Three new tests failed before the change: old disconnect/error erased the new
connection's buffer, and old pending requests prevented its drain. Additional
tests cover embedded acknowledgements and unknown error references. Tests drive
the actual bundle with controlled transports; this is not live reconnect or
cross-worker evidence. E4 still needs error/recovery ordering review and its
final evidence audit; the full ADR acceptance checklist remains authoritative.
The final rebuilt-client suite passed 2,029 tests in 189 files, including the
existing deferred-version/recovery tests; bundle ESLint passed without warnings.

## Same-connection error ordering — E4 slice

A valid event error now settles only its request and retains earlier buffered
server updates while another owned request is pending. When the final request
settles, including via an error, those updates drain through the existing
response/version checks. Disconnect still discards the disconnected owner's
buffer. This avoids losing state whose contiguous version was already consumed.

Two bundled-client regressions failed before the fix and now cover error then
noop and error then error, asserting both retained buffer state and eventual
visible metadata application. These are controlled frame tests, not live
transport/browser evidence. The E4 final evidence audit remains open.
The full rebuilt-client suite passed 2,031 tests in 189 files; bundle ESLint
passed without warnings. No Python implementation changed in this slice.

## Disconnect during buffered delivery — E4 audit finding

The acceptance audit reproduced stale delivery after disconnect inside the
first buffered update: root/noop/error/embedded drains had detached their entire
batch before application awaited. A shared drain now takes one frame at a time,
leaving later frames queued and owned so disconnect can discard them. It also
rechecks outstanding owned requests before taking the next frame.

Three failing-before tests cover noop, error and embedded acknowledgements,
disconnect from the first update's metadata callback, and assert that the
second update is never applied. This is actual bundled-client callback behavior
with controlled frames, not a live reconnect test. E4 remains open for the final
evidence audit and refreshed live backend/browser checks.
The rebuilt-client suite passed 2,034 tests in 189 files; bundle ESLint passed
without warnings. No Python implementation changed in this slice.

## Legacy async compatibility — E4 audit finding

Actual bundled-client tests exposed stuck scoped loading for old servers that
send async_pending without an opaque batch token. The client now retains those
legacy origins in transport-owned records and releases them on the matching
async event result, including deferred and embedded results. Modern tokenized
batches remain independent. Disconnect clears both types through shared cleanup.
The legacy protocol identifies completion by event name, not individual task;
it cannot provide modern per-task identity when names overlap.

Both WS/SSE regressions failed before the fix. The final rebuilt-client suite
passed 2,036 tests in 189 files and bundle ESLint passed without warnings.
The refreshed server regression set passed 455 tests at 8515822e2; subsequent
changes are client-only. Native browser overlap checks at that revision passed
for both WS and SSE: ref 1's patch retained loading and ref 2's noop released it.
That browser fixture bypasses the explicit construction guard only in its
temporary process; it is not evidence for production exposure activation.

## Exposure sink inventory and root diagnostics — E1 slice

[The sink inventory](notes/038-exposure-sink-inventory.md) maps current context,
rendering, persistence, snapshot/storage, actor and diagnostic destinations to
their producers, existing tests and remaining closure work. It is deliberately
not marked exhaustive; backend callers and full destination sentinels are open.

Its first reproduced defect was root background logging: nine cases leaked
callback/result-handler/task-name sentinels under explicit or invalid policies.
The fix emits value-free diagnostics for nonlegacy work and checks policy again
at the logging boundary. Added transition cases verify legacy-to-explicit
changes cannot reveal late failures. The exposure/async regression set passed
569 tests, and mypy passed 1,022 source files. These are staged sink tests,
not production explicit construction or root-background authorization evidence.

E1 remains open. Root async authorization/persistence remains E3; actor/backend
and browser-storage caller inventories are the next E1 closure tasks.

### Render-cache boundary

The backend trace reproduced transient render context retained in the memory
backend and legacy context restored into an explicit view with the same key,
for both HTTP and WebSocket initialization. Explicit renderers now remain
instance-owned and do not resolve/read/write the legacy backend. A policy
transition also replaces an existing legacy renderer rather than continuing
to mutate its cached object. Declared server persistence remains separate.

The three-root Python run passed 30,073 tests with 952 skipped before the
additional policy-transition regression. The final focused cache/context/HTTP
set passed 35 tests, including the transition's failing-before/passing-after
case. This is not actor, cross-worker, or browser acceptance. Actor mount remains
an uncovered route distinct from the already-refused actor event path; E1 stays
open. The loss of shared render-baseline reuse must be measured under E6.

### Staged actor mount refusal

The actor mount route is now refused for nonlegacy policies before lifecycle
hooks or transport registration, matching the existing actor-event refusal.
Six real-WebSocket single/batch cases first failed because lifecycle work ran;
the explicit-policy cases also entered actor dispatch. They now assert static
refusal, no lifecycle/actor call, surviving batch siblings, and a responsive
shared socket. The actor rendering/auth/mount/runtime regression set passed 65
tests. Actor support itself is still an acceptance blocker, not completed by
this safety gate. A separate cache-marker regression ensures the renderer's
policy flag is framework state rather than persisted application-private state.

After both changes, the full three-root Python suite passed 30,081 tests with
952 skipped (four workers), and full-package mypy passed 1,024 source files.
No JavaScript changed or browser acceptance was performed in these two slices.

## Mount diagnostic destinations — additional E1 evidence

`test_exposure_mount_diagnostics.py` first reproduced 30 leaks across five
mount failure stages, two DEBUG modes, and policy transitions, while ten legacy
cases preserved existing behavior. The shared error handler now has a redacted
mode that emits only a static log and generic frame without inspecting the
exception or writing the traceback ring. The runtime mount catches require
legacy policy at both entry and failure, including authorization and actor
render failure. Template-hash fallback logging also propagates nonlegacy
failures rather than exposing them before the outer boundary.

Independent review found another on-path bypass: a successful explicit-to-legacy
transition before a later template failure still enabled nested hash-fallback
logging. Two added regressions failed before correction. A turn-local diagnostic
scope now carries the initial restriction through nested/worker calls, with
tests for nested scopes, concurrent requests and cleanup after exceptions.

All 60 diagnostics cases pass. Turning off the actor catch's policy check made
both explicit-transition cases fail again. The final focused error/auth/runtime
set passed 142 tests; mypy passed 1,026 source files. Remaining
outer-transport/constructor/event diagnostic
routes are still open; neither E1 nor ADR-038 is accepted by this evidence.

The final unchanged code passed three consecutive full Python runs across all
three roots: 30,141 passed and 952 skipped in each run (four workers). The
independent read-only re-review found no further issues within the named mount
diagnostic scope after the policy-transition fix. No browser or Rust-suite
acceptance is inferred from these Python results.

## Current acceptance checklist

### Runtime event diagnostic slice

The root foreground/deferred handler catches and full-render catch now preserve
entry-time diagnostic restrictions and recheck policy after callbacks. Tests
cover log, traceback-ring and response-frame destinations, legacy behavior,
unprintable protected exceptions, and policy changes inside rendering itself.
The 68-case regression file passes; its initial matrix reproduced 24 leaks and
the independent review's render-transition matrix reproduced six more before
correction. Outer transport errors, constructor failures and other callback
catches remain E1 work. This slice does not accept ADR-038 or open its
production construction guard.

Final unchanged code passed three consecutive full Python runs across all three
roots: 30,209 passed and 952 skipped per run (four workers; 235.39s, 240.42s,
239.87s). Mypy passed 1,027 source files. Independent read-only re-review found
no further issues in this bounded slice. The earlier full run failed only the
new changelog fragment's required bullet format; that was corrected before these
three clean runs. No browser or Rust-suite acceptance is inferred.

### Callback diagnostic slice

The runtime's four waiter notification paths share a protected helper, and
time-travel/deferred-drain catches preserve entry and current owner restrictions.
Native waiter predicates and activity queue dispatch also protect their internal
catches without dropping pending waiters or preventing later queued work.
The regression suite first reproduced 24 outer callback leaks, one owner-replacement
leak and 12 native-mixin leaks. Independent review found three further nested-root
transition leaks; diagnostic scopes now watch the runtime-owned view slot so
nested catches see current policy and replacement. Restrictions observed after
successful callbacks persist through the rest of the notification/drain pass.

All 86 callback regressions pass; the focused runtime/mixin/exposure set passed
331 tests, and mypy passed 1,028 source files. Independent re-review ran 26 native
and scope tests successfully. Disabling owner-slot registration in-process made
all three nested-root predicate regressions fail with sentinel disclosure,
confirming the guard is exercised. Constructor, outer transport, layout,
persistence and other callback diagnostics remain E1 work; no ADR is accepted
or activated by this slice.

Final unchanged code passed three consecutive full Python runs across all three
roots: 30,295 passed and 952 skipped per run (four workers; 235.25s, 235.72s,
236.48s). Repository-pinned Ruff checks and formatting passed. No browser,
Rust-suite or website-delivery acceptance is inferred from these results.

### Shared inbound diagnostic boundary

Real WebSocket and SSE HTTP probes reproduced twelve protected disclosure cases
at outer error handlers while four legacy cases retained expected diagnostics.
`dispatch_message` now contains protected failures within its diagnostic scope,
sending generic correlated event errors rather than exposing details through
outer transport or Django error handling. Delivery and close failures remain
value-free, and cancellation still propagates.

Independent review found a nested-unwind gap: a handler observed as explicit
could later become legacy in a failing hook, and scope cleanup restored the outer
permission before the catch. Four real-transport regressions reproduced this;
protected exceptional unwinding now carries its restriction to the enclosing
scope without retaining it after the outermost exit. The transport regression
file has 65 cases, including both SSE endpoints, malformed direct calls,
correlation, delivery failure, cancellation and scope reset. The focused exposure
set passed 279 tests. Independent re-review passed 19 focused boundary/scope
cases with no further bounded findings. Direct mount/constructor, replacement,
WebSocket-only and already-swallowed hook errors remain E1 work; this is not
activation or completion of the four dependent ADRs.

The final unchanged inbound-boundary code passed three consecutive full Python
runs across all three roots: 30,360 passed and 952 skipped per run (four workers;
233.88s, 232.50s, 231.85s). Mypy passed 1,029 source files; repository-pinned lint
and formatting passed. These are Python/ASGI results, not browser, Rust-suite or
website-delivery acceptance.

Updated 2026-09-20. This section is the current work queue; the implementation
sections below are chronological evidence, not independent open-task lists.
An earlier "pending" statement may be superseded by a later implementation
section. The ADR decisions and acceptance sections remain authoritative: this
checklist groups their requirements, it does not reduce them.

The original four ADRs are 034–037. ADR-038 is their additional exposure-policy
prerequisite. **No complete ADR is accepted, and explicit exposure is disabled.**
No completion percentage or delivery date is inferred from commit/test counts.

### Completion rules

- A checked foundation means only the named boundary has evidence, not that its
  enclosing ADR is complete. An unchecked gate may already have partial code.
- Close a gate with implementation commit, named asserting tests, completed run
  results, and any required browser/deployment evidence. Record skipped coverage
  and limitations. Frame replay is not live backend/browser verification.
- Each implementation slice names one gate and its exit tests before editing.
  Record new findings against an existing gate; if none fits, explicitly amend
  this queue and explain the scope change. Do not silently broaden a slice.
- Security or correctness failures on the slice's actual path must be resolved
  before closing it. Independent findings stay visible in their own gate.
- Do not activate explicit exposure or publish proposed APIs as supported while
  their gates are open. Changing scope requires an explicit ADR decision, not
  checking off an unsupported case as if it passed.

### ADR-038 — explicit context and state exposure

Source: [decisions and acceptance](038-explicit-context-and-state-exposure.md).

- [x] Foundation: typed per-instance state and separate immutable exposure
  projections, bounded primitive validation, and guarded construction.
- [x] Foundation: server-session and signed-snapshot adapters, staged context,
  debug/direct-state boundaries, and staged HTTP/shared-runtime integration.
- [x] Foundation: eager sticky-child identity, authorization, restoration,
  persistence, disposal/pruning, and selected-child background dispatch.
  Evidence and limitations are in the corresponding sections below.
- [ ] **E1 — exporter inventory and closure.** Enumerate actual rendering,
  persistence, snapshot, browser-storage and diagnostic sinks, including actor
  and root-background paths. For each, map policy enforcement and a sentinel
  test at the destination; identify unsupported paths explicitly. No implicit
  context/attribute fallback or unclassified sink may remain at activation.
- [ ] **E2 — provider contract closure.** Complete bounded manifests and
  lifecycle tests for components, forms, actions, streams and uploads, including
  inheritance, dynamic context and invalidation. Resolve schema/codec needs and
  migration/expiry handling. Exercise deliberate ORM rendering without granting
  automatic persistence or client disclosure.
- [ ] **E3 — ownership/lifecycle closure.** Cover mount/parent-queued child work,
  descendant and repeated-instance routing, lazy/nonsticky and mixed policies,
  shell reconstruction, removal/re-addition, and root-background authorization
  and persistence. Test revocation and failed storage without stale delivery.
- [x] **E4 — correlated transport lifecycle.** The bounded request-correlation
  milestone below passed its exit audit at cf73aeaf8. Acknowledgements/loading
  are request-owned and background work is distinct. E3 lifecycle authorization
  and E5 deployment/renderer acceptance remain separate open gates.
- [ ] **E5 — end-to-end safety matrix.** Run actual HTTP, WebSocket and SSE
  flows with Django/Rust rendering, browser reconnect/back navigation and
  cross-worker restoration. Assert sentinels at every destination under DEBUG,
  failures and forged/expired/cross-identity/old-schema restores. Include legacy
  coexistence and provider/no-op parity as those dependent APIs land.
- [ ] **E6 — activation review.** Measure serialization/render cost and migration
  effort; publish supported backend/provider/codec boundaries and migration
  guidance. Review E1–E5 evidence and dependent API integration before removing
  the constructor guard. No zero-leakage or performance claim without evidence.
- [ ] **ER — retirement.** Delete the implicit-exposure machinery the explicit
  policy replaces, per [ADR-038 Step R](038-explicit-context-and-state-exposure.md):
  `_FRAMEWORK_INTERNAL_ATTRS` (`live_view.py:105`) and its six consumers, the
  `get_context_data` attribute walk (`mixins/context.py:215`, `:242`), private
  persistence selection (`live_view.py:644`, `:853`, `:871`), the
  `serialization.py` sensitive-name floor as an implicit-walk backstop
  (`:60`, `:69`, `:78`, `:81`), and last the `"legacy"` arm (`_exposure.py:90`,
  `:97`, `:113`). One deletion PR per target, code and tests together; re-run the
  94-source/160-test reference inventory and record the delta. Any target NOT
  deleted is reported with its reason, as ADR-027 Step 5 did — a survivor
  contradicts the simplification premise and is not quietly dropped.

### ADR-036 — typed event parameters

Source: [decisions and acceptance](036-typed-event-parameter-contracts.md).

- [ ] **P1 — canonical contract.** Implement signature-derived metadata and
  freeze the valid/invalid conversion matrix, including optional/collection and
  unsupported types, resource limits, duplicate/extra/missing values and
  framework-versus-application arguments.
  The staged [strict core](notes/036-strict-contract-core.md) now provides shared
  binding/conversion/metadata with 144 core regression cases. The focused strict
  and legacy/security set passes 377 tests. The subsequent
  [server integration](notes/036-strict-server-integration.md) adds opt-in policy
  resolution, canonical metadata and bound-call invocation through Python and
  Rust actor paths. Complete registration/check coverage, trusted argument
  separation and client/transport acceptance remain open; legacy is the default.
  Independent review ran the 144 core cases; final full Python validation passed
  30,504 tests with 952 skipped, and mypy passed 1,031 source files.
  Server integration re-review passed the then-current 188 core/integration cases;
  the final owner-default cache regression brings the focused suite to 190 passed.
  Independent source review found no further cache issues. The Rust target,
  mypy (1,032 files), security and repository hooks passed. Three consecutive
  full runs on the final unchanged implementation each passed 30,550 tests with
  952 skipped (249.29, 246.81 and 239.29 seconds, four workers). Earlier runs
  predate the cache fix and are not final evidence. Browser acceptance remains open.
- [ ] **P2 — wire/dispatch parity.** Route real DOM extraction and every server
  dispatch path through that contract. Verify forms' open payloads,
  keyword-only arguments, forged component injection, `coerce_types=False` and
  unchanged legacy behavior. Invalid input must never invoke application code.
  The [staged client collector](notes/036-strict-client-collection.md) now rejects
  malformed typed literals, normalized collisions, unsafe numeric values and
  reserved routing keys, and returns bounded detached JSON snapshots. Its 57
  parser tests pass; independent review verified the serialization-hook fix.
  It is not yet connected to native binders: scoped public contract delivery,
  wire-hint conflicts, generated-value conventions and real browser/transport
  integration remain open. Legacy bindings are unchanged.
  Three final unchanged-code JavaScript runs each passed 2,093 tests in 190
  files; 91 client-asset tests and zero-warning bundle ESLint also passed.
  Subsequent [owner-addressed mount manifests](notes/036-owner-contract-manifests.md)
  now reach actual WS (including actors) and SSE clients. Public contracts are
  separated by transport, mount path and registered owner address, not merged
  by handler name. Focused server coverage passes 61 cases; the browser-bundle
  mount fixture covers primary/additional mounts, navigation and invalid
  replacement metadata. Initial HTTP delivery, applied-render refresh,
  DOM-generation matching and native binder activation remain open.
  Three final full Python runs each passed 30,565 tests (952 skipped); three
  full JavaScript runs each passed 2,104 tests in 191 files. Asset checks (91),
  mypy (1,034 files) and zero-warning bundle ESLint also passed.
  Shared-runtime render responses now rebuild owner manifests, including
  explicit clears after strict-owner removal and redacted discovery failures.
  Normal WS and SSE endpoint tests assert these snapshots; actors, bespoke WS
  producers, child background frames and HTTP delivery remain open. Receivers
  now install snapshots after successful DOM application and before binding
  reinitialization. Transport-local receipt ordering survives buffered clones
  and prevents older replay from replacing newer root/child snapshots. Invalid
  snapshots fail closed without leaking pending child requests. Owner-generation
  matching, cached DOM updates, complete delivery and native binder activation
  remain open; this is not complete browser/transport acceptance.
  Applied-refresh verification: 27 bundle regressions; three full JavaScript
  runs each passed 2,131 tests (192 files), full Python passed 30,583 (952 skipped),
  and 91 asset checks plus zero-warning bundle ESLint passed.
  URL changes now use the transport render lock and reject owner replacement
  while waiting. Three final full Python runs each passed 30,576 tests with 952
  skipped; independent bounded review and normal pre-commit checks passed.
  Recovery now caches detached public contracts from the matching normal-runtime
  parent frame, serializes recovery under the render lock, rejects replaced
  owners and missing strict snapshots, and pairs fresh sticky-child HTML with
  fresh contracts. Cancellation waits for the render worker without delivering
  its result. Legacy-only recovery keeps its wire shape. Actor/bespoke producers
  remain incomplete; no strict native binder is activated by this change.
  Final recovery verification: 18 dedicated regressions, 76 expanded focused
  cases, full Python 30,601 passed (952 skipped), and mypy 1,037 files clean.
  Both legacy and explicit child background paths now use the shared contract
  helper under their existing event context. Runtime regressions cover callback
  and queue shapes, removed owners, unchanged legacy fields, and redacted
  discovery failures without losing background-batch completion. Actor/bespoke
  producers and HTTP delivery remain open.
  Child-background verification: 46 focused tests, final full Python 30,611
  passed (952 skipped), and mypy 1,037 files clean.
  Actor render results now carry snapshots captured inside Rust and retain full
  recovery HTML. The Python transport serializes actor dispatch, guards owner
  identity and cancellation, and forwards the captured metadata. Strict failure
  clears the unsent VDOM baseline; failed mounts release unregistered views.
  This covers legacy-exposure actors only: explicit actor support, bespoke
  producers, HTTP delivery, owner generations and native binding remain gated.
  Actor verification: 14 dedicated cases; final unchanged-code Python 30,625
  passed (952 skipped); Rust workspace and all 75 live-library tests passed;
  warnings-denied Rust lint and mypy (1,038 files) passed. Native browser
  acceptance is still open.
  The bespoke-producer audit also reproduced dropped full-HTML updates after
  actual Rust baseline loss in ticks, server pushes and database notifications.
  Those paths now deliver a root-content HTML fallback, arm recovery with the
  original raw HTML and advance the consumer-owned wire version. Three native
  Rust regression cases fail before the fix and pass afterward, including
  recovery replay; the expanded focused suite passes 43 tests.
  This fixes delivery, not contract capture: bespoke-producer metadata,
  cancellation and owner-generation acceptance remain open.
  The full Python run passed 30,627 tests (952 skipped) and failed only the new
  changelog entry's formatting check. After correcting that entry and a helper
  return annotation, all 81 final targeted tests passed; mypy passed 1,039 files.
  The full suite was not repeated after those corrections.
  Ticks, pushes, database notifications and both async-result branches now
  capture raw HTML, fallback content and public contracts in one worker under
  the render lock. Strict snapshots require the mounted runtime identity;
  legacy frames remain unchanged and strict removal sends explicit clears.
  Cancellation waits for the render worker and discards its unsent baseline.
  Metadata failures leave the previous recovery pair intact, suppress the
  frame, reset the unsent diff baseline and return redacted errors without
  invoking application result handlers again. Owner replacement is checked
  across lock and render waits. The 48-case matrix and 155-test expanded
  focused group pass; mypy passes 1,041 files. Deferred/debug/hot-reload
  producers and HTTP delivery remain open, along with native activation.
  Two full Python runs passed 30,676 tests (952 skipped). A subsequent review
  corrected unsolicited error labels to `source="async"`, preserving the
  existing foreground-request correlation contract. Final focused tests (155),
  client correlation tests (51) and mypy passed after that correction; the full
  suite was not repeated afterward. The shared helper also consumes the forced
  render flag on success, including async retries after a withheld render.
  Time-travel root/component jumps and forward replay now serialize restoration
  and rendering under the consumer lock, capture render-bound contracts, and
  reject stale owners across waits. Cancellation waits for workers before
  releasing the lock and withholds the DOM/cursor response. Their update/error
  source labels preserve foreground request correlation. The 39-case debug
  matrix, 175-test expanded Python group and four new bundled-client cases pass.
  This does not close replay argument-validation, hot-reload/deferred producers,
  HTTP delivery, native binding or explicit-exposure acceptance.
  Final debug-delivery verification passed 30,715 Python tests (952 skipped),
  2,135 JavaScript tests (192 files), and mypy over 1,043 files. Direct replay
  argument-binding probes still fail: strict numeric conversion is skipped and
  boolean-as-integer input reaches the handler. This is a concrete remaining
  P1/P2 dispatch gap, not acceptance of the complete replay route.
  The subsequent replay adapter resolves server-owned policy and validates strict
  calls before restoring state, using the canonical positional/keyword call plan.
  It preserves raw legacy values and original history parameters, awaits async
  handlers through Django's sync bridge, and refuses invocation after failed
  restoration. Invalid strict calls do not restore state, grow history or fork a
  branch. The synchronous API requires `sync_to_async` when called from async
  Python code for an async handler; the consumer already supplies that boundary.
  Replay-binding verification: 26 dedicated cases, 329 expanded focused cases,
  30,741 full-suite Python tests passed (952 skipped), and mypy 1,044 files clean.
  These close the reproduced replay-binding defect, not the remaining P1–P3
  checks, delivery, native-binding and browser-acceptance gates.
- [ ] **P3 — acceptance.** Execute documented examples under their stated policy;
  verify redacted diagnostics and the ADR's complete conversion/parity matrix.
- [ ] **PR — retirement.** Delete the superseded coercion path per
  [ADR-036 Step R](036-typed-event-parameter-contracts.md): `coerce_parameter_types`
  (`validation.py:137`), `_coerce_value` (`:219`), `_coerce_single_value` (`:249`),
  with their tests. `validate_handler_params` (`:440`) is rewired, not removed.
  Fires only once strict is the default, which this ADR does not yet approve.
  Grep-verify that no second coercion implementation remains.

### ADR-035 — Django-native form and object lifecycle

Source: [decisions and acceptance](035-django-native-form-and-object-lifecycle.md).

- [x] Foundation: public form-construction hooks, empty binding, initial values,
  prefixes and legacy `_create_form` bridge; see `test_form_hooks_adr035.py`.
- [ ] **F1 — managed object.** Implement resolve → authorize → bind, managed
  `self.object`, opt-in ModelForm integration and explicit application policy.
  Prove authorization precedes construction/validation and within-dispatch reuse
  avoids duplicate queries without persisting ORM objects or permission caches.
- [ ] **F2 — form acceptance.** Exercise no-custom-mount edit, create and
  non-model examples; independent/inherited hooks; choices, relations, uploads,
  empty submission, validation and exactly-once save. Cover missing/tampered/
  revoked targets, callback failures, HTTP/WS reconnect/back navigation,
  Django/Rust rendering, typing and browser-visible input/errors/save feedback.
- [ ] **FR — retirement.** Delete the pre-hook object plumbing per
  [ADR-035 Step R](035-django-native-form-and-object-lifecycle.md):
  the `_model_instance` attribute (`forms.py:51`, reads `:93-95`, `:215`),
  `_ensure_model_instance()` (`:223-225`) and the docstring example (`:39-42`),
  with their tests. `_create_form` (`:281`) is a bridge that stays; its removal is
  a separate later decision and is not counted as a saving here.

### ADR-034 — component-scoped events and bindings

Source: [decisions and acceptance](034-component-scoped-events-and-bindings.md).

- [x] Foundation: native child lifecycle isolation and component-owned loading.
  This is not the proposed typed subscription API or repeated-request support.
- [ ] **C1 — typed binding API.** Implement per-instance binding, declared outputs
  and subscriptions with positive/negative typing fixtures for inheritance,
  renames, misspellings, wrong sources and async callbacks. Route only through
  registered identities; reject direct client invocation of subscriptions and
  unknown targets without a view-handler fallback.
  The [initial isolated proof](notes/034-typing-proof.md) passed both type checkers;
  current fixtures exercise the real staged framework classes instead.
  Production now compiles private subscription declarations at LiveView class
  construction, revalidates inheritance/replacements, rejects duplicate/foreign
  bindings and conflicting transport decorators, and blocks direct callback
  invocation in all shared event-security modes. Actual HTTP fallback rejection
  is tested; see the staged compiler section of the proof document.
  The private concrete dropdown now binds real per-owner instances, emits typed
  outputs with trusted source injection, and passes real HTTP/WS/session reconnect
  tests. Snapshot change detection sees public binding state; unchanged closes
  produce no-op responses. The type fixtures now use these real classes: both
  mypy and Pyright reject all twenty-one negative locations. Mypy follows Django's
  installed source declarations, and the LiveView stub matches its runtime
  constructor, removing the inherited-Any gap without a new dependency or test
  waiver. Both debug scrubbers now restore the
  concrete state schema within the existing lifetime, without callbacks or ID
  changes; malformed component state is rejected before component mutation.
  Fixed bindings now also have a validated manifest in signed navigation
  snapshots, with real WS restore/dispatch and rejection coverage. Interactive
  resumes send current HTML rather than retaining historical controls. Browser
  signed-navigation/debug transport, actor lifecycle, public export and full
  browser acceptance remain open; see the proof document for evidence boundaries.
- [ ] **C2 — dropdown pilot and observations.** Implement the documented state
  owner, local mechanics and semantic outputs. Verify two same-type menus,
  source injection, valid/forged/disabled selections and callback rendering.
  Optional native-toggle observations must report actual visibility without
  blocking/rolling back UI; unchanged observers do no render/diff/patch.
  The private server contract now validates client-mode observations, source,
  subscription, lifetime and sequence; unchanged HTTP/WS observers return no-op
  while reactive observers render. Cursor state is separate from authoritative
  visibility. Browser listeners/selection dismissal and reconnect coalescing are
  not wired yet. Independent state-backend claims now pass the former HTTP
  exception/retry failure and prevent stale session copies from replaying reports.
  Concurrent memory and actual Redis claims are tested; memory remains
  process-local. Missing/expired cursors fail closed until a fresh binding is
  rendered. Browser recovery for this boundary remains open. This is not C2
  acceptance or an exactly-once application callback guarantee.
- [ ] **C3 — collection lifecycle.** Prove keyed repetition, reorder, duplicate
  keys, removal/re-addition, nesting, reconnect and restore. Include a separate
  authorized delegated-row example; do not present it as stateful repetition.
- [ ] **C4 — acceptance and publication.** Run HTTP/WS and real browser tests
  with both template backends: focus/dismissal/default actions, user isolation,
  async/error behavior, duplicate/reordered observations and stale identities.
  Publish runnable examples, generated reference, website navigation and AI
  guidance together through D2 below; preserve legacy plain handlers.
- [ ] **CR — retirement (expected empty).** [ADR-034 Step R](034-component-scoped-events-and-bindings.md)
  records that this ADR retires **no** existing code: `event=` is kept per ADR-033 D5,
  the string-routed alternative was rejected rather than shipped, and the existing
  `name=` / `toggle_event=` / item `event` arguments continue. This gate closes by
  confirming that still holds after C1-C4, or by naming a target C1-C4 revealed.
  It must not be closed by inventing one — this ADR is justified on developer-facing
  value, not on code removed.

### ADR-037 — checks and executable documentation

Source: [decisions and acceptance](037-event-contract-checks-and-executable-documentation.md).

- [ ] **D1 — shared checks.** Use the same contract as runtime for ownership,
  arguments, injection and exposure checks. Test positive/negative fixtures,
  inherited/decorated handlers, native controls, intentional catch-alls,
  authorized ORM rendering, includes/shared/dynamic templates, locations,
  reasoned suppressions and machine-readable output. No mounts, handlers or
  querysets may execute during checking.
- [ ] **D2 — executable documentation and catalogue.** Make examples canonical
  fixtures and deliberately break each test layer to prove its gate fails.
  Verify website navigation and report skipped fixtures. Include the originally
  reported code-snippet whitespace, checkbox appearance, dropdown items and
  missing menu handler, plus multi-menu interactions and visible server errors.
  A read-only audit of all 179 generated usage snippets found placeholder
  handlers referencing an uninitialized component in dropdown, modal and tabs.
  The shared generator now emits view-owned state and working handlers for these
  template tags, preserves slot content and includes the modal opener. Six tests
  execute the displayed Python and assert before/after output under Django and
  Rust rendering. An execution sweep then found four namesake import collisions
  (accordion, collapsible, carousel and sheet); examples now import the exact
  renderer demonstrated by the preview. Of 179 catalogue entries, all 178 view
  examples now mount and render without exceptions; server_event_toast documents
  a mixin rather than a view. Tests no longer silently skip generation failures.
  This is initial execution coverage, not full interaction acceptance or the
  proposed multi-instance API. The final focused catalogue suite passes 240 tests.
  The final full Python suite passes 30,583 tests with 952 skipped.
  The corrected usage sections were checked in an isolated browser catalogue;
  this does not establish publication on the user's running site or D2 closure.
- [ ] **D3 — final acceptance.** Run the ADR acceptance matrices at the final
  revision, complete migration/AI guidance, and verify actual website delivery
  rather than equating repository Markdown with publication. Record remaining
  static-analysis limits; only then change the relevant ADR status.
- [ ] **DR — retirement decision.** [ADR-037 Step R](037-event-contract-checks-and-executable-documentation.md)
  requires D1 to settle whether this ADR is consolidation or addition: enumerate every
  place that re-derives handler parameters, ownership or event names independently of
  the runtime contract, cited `file:line`, and mark each `RETIRE` (with a deletion PR)
  or `KEEP` (with the reason it is distinct). An empty enumeration is recorded plainly
  and the ADR claims no saving. Every `RETIRE` row carries a merged deletion PR before
  D3 acceptance.

### Completed milestone: E4 — request correlation

Owner: current task implementer. Status: exit checklist verified at cf73aeaf8.
This is a transport correctness slice, not permission to enable ADR-038.

The original `tests/js/request-correlation.test.js` reproducer reported four
failures and two passes: overlapping same-trigger replies cleared loading early
in WS/SSE; SSE supplied neither request refs nor an awaitable server completion.
Those regressions now pass, including the formerly unreachable duplicate-reply
assertions. The expanded suite covers cancellation and response variants.

Deliverable: one shared request register/acknowledge/cancel contract used by WS
and SSE. Inventory existing callers first: embedded responses, patch/HTML/no-op
responses, errors, disconnects, event dispatch, cache/HTTP fallbacks and loading.
Implementation must preserve their invariants rather than add parallel counters.

Exit checklist:

- [x] Distinct request identities and awaitable completion on the actual server
  reply; SSE POST acceptance alone does not complete the event.
- [x] Two requests from the same trigger remain pending after the first reply;
  out-of-order, duplicate and unknown refs cannot clear unrelated work.
- [x] Embedded, patch, HTML and no-op responses resolve only their own request;
  background frames do not acknowledge a foreground request.
- [x] Failure, disconnect, replacement transport and removed/morphed controls
  drain only owned work and settle promises without stranding loading state.
  Preserve documented `async_pending` and legacy no-ref/fallback behavior.
- [x] Focused failing-before/passing-after tests, rebuilt-client regressions,
  full JS suite, affected server wire tests and live backend/browser WS/SSE
  overlapping-request evidence pass; record exact revision and limitations.

Exit evidence:

- Request IDs, out-of-order/duplicate/unknown refs, WS/SSE promise completion,
  all response shapes, removed/morphed controls, modern/legacy async completion,
  transport replacement and buffered-update/error/disconnect ordering:
  tests/js/request-correlation.test.js and tests/js/event_sequencing.test.js.
- HTTP overlap, errors, cache hits, stale page effects and navigation abort:
  tests/js/http-request-correlation.test.js. Existing teardown and SSE suites
  preserve keepalive and tokenless compatibility. Mount-time async frames cannot
  acknowledge an unrelated foreground event.
- Runtime/root/deferred/component/child batches: test_sse_runtime_convergence_1887,
  test_component_scoped_render_2917, test_runtime_child_routing_1892,
  test_async_batch and test_exposure_child_async. Refreshed affected server set:
  455 passed at 8515822e2; no server code changed afterward.
- Final bundled-client run: 2,036 passed across 189 files at cf73aeaf8; lint and
  commit hooks passed. Native browser overlap at 8515822e2 passed over both
  actual WS and SSE connections (distinct refs, loading retained after first
  reply and released after second). Earlier root/component/child batch browser
  evidence is recorded in the corresponding sections. The later legacy fix
  is covered by bundled-client tests, not a claim of an old-server deployment.

Limits: legacy tokenless async work retains its pre-existing event-name
completion semantics, not modern per-task guarantees. Controlled frame/fetch
tests are not live network tests. Cross-worker restore, production explicit
authorization, complete renderer/provider combinations and publication remain
E3/E5/E6 and the dependent ADR gates; none is inferred from E4 completion.

Stop this slice when its exit checklist passes. Next is E1's sink inventory,
then E2/E3 closure and the E5 integration matrix. Implement P1–P3, F1–F2 and
C1–C4 against the guarded exposure foundation; complete D1/D2 alongside their
contracts. E5/E6 final activation and D3 follow the dependent integrations, so
the dependency order does not require a premature exposure release.

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

Each ADR ends in a **retirement gate** (`ER`, `PR`, `FR`, `CR`, `DR`) on
ADR-027's `dormant-define -> wire -> flip -> delete` playbook, whose Step 5
shipped as #2628. The arc's case rests on replacing heuristic machinery, not
sitting beside it, so the deletions are scheduled work with their own PRs rather
than an assumed consequence. Two of the five are expected to retire nothing
(`CR`, and `DR` pending its D1 enumeration); they say so explicitly, because an
invented target would overstate the saving. A retirement gate is closed by a
merged deletion PR or by a written account of why a named target survived.

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

### Parent-driven child persistence

The staged explicit path now saves registered descendants after a successful
parent-event render, before its success frame, and after HTTP POST rendering.
An explicit parent's unchanged assign snapshot no longer implies its children
are unchanged: parent handlers can mutate a child directly. Deliberate
`_skip_render` still skips rendering, but saves the authorized child tree before
acknowledging the event. The existing production construction guard is unchanged.

The shared helper walks only the current owned registry, never arbitrary public
attributes. It checks exact mount identity, schema, request binding, ownership,
disposal status and current view/object authorization, including nested children.
Only declared server fields enter the batch. All captures must validate before
any session entry changes. The batch has a maximum of 256 descendants and one
aggregate root-contract JSON budget, in addition to each child's contract and
the existing ancestry limit. After projection and application authorization hooks,
the helper rechecks the captured scopes and registry membership before writing.
One backend flush stores the child batch; root persistence remains separate.

The runtime save retains the 150ms deadline. Failure emits a static error instead
of an update/noop acknowledgement. Because rendering may already have advanced
the server VDOM, a subsequent successful event is forced to send full HTML.
HTTP storage exceptions are also sanitized, including chained exception text;
the new failure regression first reproduced a backend sentinel in both DEBUG
JSON and logs. Failed or cancelled flushes restore the local session entries
and modified flag. This is **not** a transaction across the root, children and
application side effects. A backend may have committed before an error/timeout
was observed; restoring the in-memory session cannot undo that uncertain commit.
Synchronous application hooks cannot be preempted by asyncio cancellation.

Tests exercise real shared-runtime parent events, explicit no-render events,
actual HTTP POST plus fresh GET restoration, direct and nested scopes, a single
batch flush, no partial writes on authorization/identity/budget failures, total
budget enforcement, hook-driven registry changes, storage errors/timeouts,
private error redaction and full-HTML recovery after a withheld update.

Verification: the focused persistence/HTTP/timeout set passed 68 tests; the full
Python suite passed 30,004 tests with 952 skipped; mypy passed across 1,014 source
files. These are server/protocol tests, not browser or cross-worker evidence.

This is a **registry sweep, not rendered-slot reconciliation**. A child omitted
from a new template may still be registered; removing its runtime ownership and
stored envelope requires a successful-render inventory and scoped pruning index.
GET/reconnect-wide sweeps, scoped child background dispatch, lazy/nonsticky and
mixed-policy providers, repeated/nested event routing, browser/cross-worker
verification and the remaining ADR034–037 work still block activation.

### Render-aware eager child pruning

The staged eager-sticky provider now reconciles runtime ownership from completed
server renders. Only children invoked through the server component renderer are
candidates; markup alone cannot create or retain a registered child. The final
HTML decides which of those candidates survived rendering and page-shell/root
composition. Unregistered wrappers cannot hide a real candidate, and inert
`textarea`/`template` output does not keep a component alive.

Nested plans commit after the outer wrapped render succeeds. HTTP and shared
runtime calls also wrap subclass render overrides, so raising after a call to
the base renderer does not prematurely commit its removal plan. Full-page
renders and live-root renders have different authority: root updates preserve
known shell children, while a completed full render can remove them. The named
template inheritance fallback is explicitly a root fragment, not proof of a
completed shell. Runtime unregister/disposal clears region references as well
as the child registry.

Server child writes now maintain a bounded, versioned slot-path index. It stores
paths and shell membership, not arbitrary deletion keys or application values.
Pruning derives keys again from the current authorized request route. A malformed
index fails without deleting entries; a copied/forged slot path cannot select
another route's child key or a Django authentication key. The index has at most
256 paths, at most 16 ancestry levels, and the normal bounded JSON validation.
Single-child initial saves record their path, so a later completed render can
clean up state from a failed initial render. These initial writes are not made
transactional with the whole parent render.

Parent events, shared-runtime mounts and HTTP GET/POST sweep the reconciled
registry. Routed child events use the same post-render batch rather than a
separate pre-render child flush, so removed descendants are pruned before an
`embedded_update` success frame. The existing 150ms runtime storage deadline is
owned by the shared helper. Failed/cancelled flushes restore local staged keys
and the modified flag, not an uncertain remote commit or application effects.

A fresh root-only instance preserves previously indexed shell state it has not
rendered; this is storage preservation, **not** evidence of shell-instance
reconstruction or shell-event routing on reconnect. Full-render provenance lets
an owner that actually knew a shell remove it via explicit unregister. Unindexed
envelopes from older gated prototypes cannot be reconstructed by guessing hash
keys; migration/expiry handling remains an activation concern.

The focused tests include real Rust full-page/root renders, Django child renders,
parent and routed-child events, nested same-name slots, failed render overrides,
fragment fallbacks, shell preservation/removal, forged markup, inert output,
index corruption, route confinement and failed-prune local rollback. Verification:
488 focused tests passed; the full Python suite across all three roots passed
30,024 tests with 952 skips, and mypy passed for 1,017 source files. The first
full run exposed an outdated mount-order source pin; it now checks the
reconciliation wrapper and its delegation, while retaining the real second-mount
backend-clone regression. All 10 restore-contract tests also passed separately.
Source/security review found no further issue in this slice. This does not
enable explicit exposure, finish lazy/nonsticky or mixed-policy providers,
prove browser/cross-worker behavior, or complete ADR034–037.

### Scoped child background work: runtime boundary

The staged shared-runtime child-event path now drains the selected explicit
child's named/legacy task queue, not the root queue. Tasks are tracked on the
child, and callbacks use the existing shared sync/async callback runner.
Completion renders and persists the child before emitting an `embedded_update`
frame tagged `source="async"`. This does not temporarily replace the runtime's
root to make root-only methods operate on a child.

Before execution and completion, the worker reloads the supported server session
and Django authentication from the trusted mount identity. It does not reuse the
original POST as current authority or consume SSE's next POST request slot.
Root and ancestry authorization, object permissions, current registration and
generation are checked under the runtime/transport locks. Authorization hooks
can replace an owner themselves; a regression demonstrated stale result-handler
delivery until the post-hook identity check was added. Disposal, generation
change and root replacement suppress stale completion. Running synchronous
application side effects remain non-preemptible.

Callback failures may reach the owning application's `handle_async_result`, but
the framework emits only a static failure if unhandled or if a provider fails.
It does not log/export the callback exception. Focused tests cover sync, async
and coroutine-returning callbacks, both queues, independent root work, scoped
storage, cancellation, permission/session revocation, stale ownership and error
recovery/redaction. The exposure/async regression group passed 551 tests;
the full Python suite passed 30,037 tests with 952 skips. Mypy passed for
1,019 files. Source/security review also checked callback error handling,
post-hook ownership and the session-reload boundary.

The runtime review exposed a client gap addressed in the next section: SSE had
no `embedded_update` case, and WebSocket child completion consumed unrelated
last-event state. Full backend/browser verification, scoped loading, child work
queued from mount or parent handlers, and descendant/repeated-instance routing
remain required work. Legacy child dispatch and root background persistence
also remain separate paths requiring audit. Explicit exposure remains disabled.

### Scoped child client responses

WebSocket and SSE now use the same child-response handler and DOM morph core.
The existing WebSocket `handleEmbeddedUpdate` method delegates to that core.
The child wrapper stays in place, only its contents morph, and inserted-script
warnings and event rebinding still run. Missing targets or non-string HTML do
not consume another event's acknowledgement.

Background frames (`source="async"`) do not clear current event/loading state.
Normal referenced replies resolve the matching WebSocket event promise and
drain buffered patches when its pending-reference set becomes empty. They do
not clear a different newer event/trigger's pointer. A no-ref reply must match
the pending trigger's child scope and, when supplied, its event name. That scope
is captured before the morph, so a successful reply can remove its trigger
without losing the acknowledgement association.

The real built-client regression tests reproduced both missing SSE updates and
unrelated WebSocket loading teardown. A real-browser smoke fixture replayed
scoped frames through both transport handlers: the selected child showed its
completion while the other child's button remained disabled/pending. This is
client-frame replay, not a live backend-session/browser or cross-worker test.
All 1,965 JavaScript tests across 186 files passed, including the 13 focused
client regressions; ESLint and source/security review passed. Generated bundles
were rebuilt from source, not hand-edited.

That response fix did not itself change the globally keyed loading manager;
the next section describes component ownership there. Concurrent no-ref SSE
acknowledgements and multi-task background pending indicators still require
further work. Neither change activates explicit exposure.

### Component-owned loading state

Loading records now combine event name, nearest native embedded-view/component
wrapper, and triggering element. Existing `pendingEvents` remains an aggregate
set of names; it is not the ownership key. A `save` in one component therefore
does not disable another component's controls merely because it also handles
`save`. Stopping one scope preserves another scope's indicators and the page's
global loading class while work remains.

Ownership uses the wrapper DOM instance, not an ID string. A replacement wrapper
with the same ID does not inherit removed work. Nested components use their
nearest owner. Scanning removes disconnected scopes and reapplies pending state
to newly morphed controls in an existing scope. A reply can remove its trigger:
completion finds the original pending record instead of using detached DOM
ancestry. A no-trigger legacy page completion clears page-scoped work only.

All seven initial scope regressions failed against the old manager. The focused
group now passes 32 tests, including referenced same-handler replies through the
actual WebSocket client. A browser frame-replay fixture verified both buttons
disabled, then only the left enabled after its reply, then both enabled with no
pending work. This is not a live backend-session/browser test. The full
JavaScript suite passed 1,975 tests across 187 files; ESLint and source review
passed. Bundles were regenerated from source.

Records coalesce repeated dispatches from the same element within one scope;
they are not a per-request counter. Overlapping same-trigger requests, concurrent
no-ref SSE replies, error/disconnect draining and multi-task background loading
still need correlated lifecycle coverage before the broader ADR is accepted.

### Foreground request correlation: WS and SSE

Both transports now register requests in the same reference sequence and record
the owning transport. Ordinary SSE sends return a server-response promise;
accepting the HTTP POST is not completion. Teardown keepalive sends retain the
existing fire-and-forget contract because the outgoing page cannot await its
stream. Unknown/duplicate references never fall back to last-event pointers.
No-ref compatibility can acknowledge one outstanding request, but does not guess
between several. A background frame cannot acknowledge a foreground request.

Loading consults outstanding requests before clearing a coalesced trigger or
page scope. Replies can arrive out of order or after removal of the child owner.
Targeted errors and failed SSE POSTs cancel only their reference; disconnect
settles that transport's promises without consuming a replacement transport's
requests. Cached/HTTP behavior and teardown tests remain in the full suite.

Verification: the original six-test reproducer failed before the change; the
expanded request tests and full JavaScript suite pass (2,001 tests, 188 files).
Selected actual SSE endpoint/runtime and child-async server tests pass (55 tests).
ESLint and whitespace checks pass. Generated clients were rebuilt from source.
Live browser checks against an ephemeral Django backend observed a patch then
no-op with loading true then false for WS; SSE returned ref 2 before ref 1 and
still preserved loading until both completed. The temporary server reused the
staged exposure fixture with its constructor bypass; this is not production
activation, cross-worker coverage, or a complete provider/browser matrix.

E4 remains open: background-task completion identity/multiplicity, ambiguous
legacy no-ref overlap, and the remaining lifecycle matrix are not proven by
foreground acknowledgements. In particular, acknowledging `async_pending`
still separates the foreground promise from loading retained for later work;
it does not establish per-task completion tracking. E1–E3 and ADR034–037 remain
open, and explicit exposure is still unavailable to applications.

### Owned child background batches

The staged explicit-child path captures its queued work before sending the
foreground acknowledgement, then dispatches that captured batch after the send.
`AsyncBatch` in `python/djust/_async_batch.py` assigns an opaque token, advertises
`async_pending: true` plus `async_batch` on the acknowledgement, and emits
`{"type": "async_complete", "async_batch": token}` once every task settles.
Task done callbacks cover cancellation before coroutine entry; an owner removed
before dispatch discards its captured callbacks and releases its token. The
completion contains no results, callback names, rendered state or authority.

The client associates the token with the acknowledged request's transport and
trigger. Foreground promises resolve normally, while batch records keep loading
active independently. Duplicate/unknown completions and another transport's
completion cannot release owned work. Background errors carry `source="async"`
and the token; they do not cancel newer foreground events. The final completion
still waits for all tasks, including handled failures. Disconnect clears owned
batch records as well as foreground requests.

The real browser fixture uncovered a separate load-bearing ownership bug:
`live_render` stamps routing hints on individual controls, not just wrappers.
Treating the nearest `data-djust-embedded` hint as an owner selected the button
itself; morphing removed that hint and stopped loading reapplication. The manager
now prefers the actual `[dj-view][data-djust-embedded]` or component wrapper,
retaining its legacy marker-only fallback. New WS/SSE regressions fail before
this fix. Both live backend/browser fixtures now stay disabled through the
acknowledgement and two background updates, and enable only on batch completion.

Verification: full Python suite 30,042 passed, 952 skipped; full JavaScript suite
2,011 passed across 188 files; mypy passed 1,021 source files; Ruff and generated
bundle ESLint passed. The initial batch test failed on the missing pending flag;
the initial six client batch tests failed before implementation. Tests also
cover pre-start cancellation, distinct tokens, repeated discard, empty batches,
multiple tasks, cross-transport completion, and errors with a newer request.
Browser fixtures use real WS/SSE transports but a test-only explicit constructor
bypass. Temporary tabs and servers were closed.

This is **not complete E4 or ADR-038 acceptance**. Root runtime background work,
deferred/component paths, mount/parent-queued child work, and the full legacy
no-ref/replacement matrix still require integration. Older clients do not know
the batch-completion message: activation requires a compatible client rollout,
not only a server update. The explicit constructor guard remains closed and
no proposed API is advertised as supported.

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
