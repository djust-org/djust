# ADR-036: owner-addressed mount contracts

Status: staged delivery/read model, not strict browser binding activation.
The [server integration](036-strict-server-integration.md) remains authoritative
for received arguments. The [client collector](036-strict-client-collection.md)
is not yet called by native event bindings.

## Wire shape and ownership

Shared runtime mount frames include `parameter_contracts` when at least one
currently registered owner has a strict event handler. Version 1 contains an
`owners` list; each entry has `view_id`, `component_id` and `handlers`.

| Address | Native target |
| --- | --- |
| `(null, null)` | The mounted root view |
| `(null, component_id)` | A root-registered LiveComponent or BoundComponent |
| `(view_id, null)` | A registered embedded child view |

Equal IDs in different address positions do not collide. Child/component
combinations are not advertised: current child dispatch resolves handlers on the
child itself, not a nested component. Repeated root mounts of the same view path
are not distinguished by this protocol and remain an integration gate.

Strict handler records contain policy, coercion setting and canonical parameter
metadata. Legacy records contain policy only. No defaults, state, descriptions,
contexts or RPC-only methods are copied. Native wrapper attributes take precedence
over component-descriptor declarations, matching dispatch. Discovery binds only
decorated known Python method types; it does not evaluate application properties,
custom descriptors or a shadowed `__dict__`. Unsupported strict declarations fail
instead of falling back to legacy. All-legacy trees omit the field.

The emitted manifest is bounded to 1,024 owners, 10,000 handlers and 65,536
serialized bytes. These new declaration limits do not reject all-legacy trees.
Invalid/ambiguous registered identities reject a strict manifest. Disposed
children are excluded, and rebuilding reflects registry removals. No global
cache retains owners or mutable manifest dictionaries.

## Client lifetime

WebSocket and SSE mount receivers install manifests on that transport instance,
indexed by mount view path, then by the exact owner address and handler name.
Additional WS mounts do not overwrite primary rules. A primary mount clears old
mount scopes; omission on a legacy mount clears that mount's previous contracts.
Explicit `disconnect()` clears only that connection's map. Unexpected transport
loss, reconnect and HTTP fallback lifetime remain part of the activation gates
below; explicit teardown tests do not establish that broader lifecycle.

The client copies and freezes only recognized public contract fields. An invalid
replacement manifest records an invalid state rather than retaining old rules or
falling back to legacy. Unknown mounts, owners and handlers are distinct from a
known legacy mount. These are advisory public records, never authorization.

## Render-frame delivery (in progress)

The shared Python runtime rebuilds contracts for each emitted patch, full-HTML
or embedded render response. The snapshot travels on that response together
with `parameter_contract_view`, the mounted path (not a child's `view_id`).
Removing the last strict owner emits explicit `parameter_contracts: null`
on subsequent render responses until remount. All-legacy sessions retain their
existing response shape. No owner or manifest is cached by this mechanism.
Invalid contract discovery suppresses that DOM frame and returns a fixed,
redacted render error instead.

URL-change handling now borrows the same WS/SSE render lock as events and
background results, and rechecks the mounted owner after acquiring it. A
scheduling probe demonstrated that URL handling previously entered its body
while that lock was held. Six regressions using the real transport adapters
cover serialization, replacement while waiting and cancellation cleanup; all
failed before the fix. Related focused coverage passes 124 tests, including
URL wire versions and object-permission checks. This does not prove actor or
external render-producer serialization.

Final render-delivery verification: three unchanged-code full Python runs each
passed 30,576 tests with 952 skipped. Normal pre-commit checks passed. Independent
review found no actionable issues in the bounded server-delivery/URL-lock change.
JavaScript and Rust were unchanged in this slice; the earlier mount-manifest
JavaScript results below are not new browser activation evidence.

### Applied render refresh (staged)

WS/SSE response handlers now retain their originating transport through immediate
and buffered delivery. Accepted patches (including empty patches), full HTML,
embedded updates and WS recovery HTML refresh that transport's primary-mount
snapshot before `reinitAfterDOMUpdate()` can fire `dj-mounted`. Additional mount
maps are preserved. Rejected versions and absent child DOM owners do not install
incoming snapshots. Failed patches invalidate strict rules rather than advertising
a snapshot for partially applied DOM.

Each transport records receipt order in a WeakMap, including the original order
of cloned buffered frames. A mount uses its own receipt, not the newest queued
message's receipt. Older deferred frames cannot overwrite a newer applied
whole-tree snapshot; this also covers child replies without a parent VDOM version.
Explicit disconnect clears these records. This ordering is not a server-issued
owner generation and does not solve late messages from a replaced owner lifetime.

Malformed or missing metadata for an established strict scope invalidates it.
Malformed metadata does not prevent request acknowledgement, including child
replies. Known legacy scopes retain their legacy behavior. Supplied snapshots
must name the known primary mount matching the current page root; unsupported
ownership does not silently select another mount. Native binders still do not
consume these records: DOM generations, complete producer coverage, cached DOM
updates, initial HTTP delivery and transport transitions remain activation gates.

The applied-refresh regression file exercises 27 cases against the actual
client bundle. Three final unchanged-code JavaScript runs each passed 2,131 tests
in 192 files; the full Python suite passed 30,583 tests with 952 skipped.
Minified-asset checks passed 91 tests and bundle ESLint reported zero warnings.
The shipped gzip grew by 484 bytes. These are automated receiver/transport
results, not real-browser strict-binding acceptance.

Actual normal/actor WebSocket and SSE event tests now assert render snapshots as
well as mount snapshots. Actor capture is described below; bespoke WebSocket
tick/push paths, initial HTTP and HTTP fallback remain delivery gates.
No claim of complete transport parity is made.

### Recovery snapshots

Normal runtime WebSocket parent render frames now leave a detached JSON copy of
their public contract metadata beside the matching recovery HTML and consumer
version. Every new recovery baseline clears the previous metadata. Embedded
frames cannot overwrite it. Cached recovery never rebuilds a manifest from a
later owner state; a missing snapshot for a strict scope fails closed.

Recovery runs under the consumer render lock and checks owner identity after
acquiring it. Cached ownership is a weak reference, not a retained old view.
Sticky-child recovery captures fresh HTML and metadata together in its render
worker; if that fails, fallback uses the old HTML and its old snapshot together.
Errors omit exception text. Cancellation keeps the lock until an in-flight
render worker settles, then propagates without delivering a result.

This does not make actor or bespoke producers contract-aware. Their uncaptured
strict recovery is rejected rather than fabricated from current declarations.
Legacy-only recovery retains its existing wire shape. The real normal-WebSocket
endpoint test checks recovery HTML, version, manifest and mount-path identity.
This is server recovery evidence, not native strict-binding acceptance.

Final recovery verification: 18 recovery-specific regressions; 76 tests in the
expanded recovery/transport group; full Python suite 30,601 passed and 952
skipped. Mypy passed for 1,037 files. A recovery that first advertises strict
contracts also marks that scope active so subsequent legacy renders emit an
explicit clear.

### Child background frames

Both legacy and explicit embedded-child background completion paths now use the
shared render-frame helper while holding the existing transport event context.
Their scoped HTML includes the current root/child owner manifest and mount-path
identity. No separate metadata serializer or client message is introduced.
Legacy-only responses still omit the optional fields.

Runtime regressions cover both child policies, sync/async/returned-coroutine
callbacks, named/legacy task queues, and suppressed delivery after removal.
Contract-discovery failure withholds HTML and redacts exception data while the
original background batch still completes. These are runtime and persistence
tests, not live-browser strict-binding acceptance.

Verification: seven missing-metadata regressions failed before routing these
frames through the shared helper. The expanded runtime/metadata/batch group
passed 46 tests; final full Python passed 30,611 with 952 skipped, and mypy
passed for 1,037 files.

### Actor render snapshots

The Django session bridge supplies the public metadata service to its Rust view
actor. The actor captures detached JSON inside the same serialized operation as
rendering and carries it through the session result. Python forwards that
snapshot, never a later rediscovery. Generic Rust/Python actors without the
Django bridge remain independent of Django metadata imports.

The session result exposes `recovery_html` and `parameter_contracts` to Python.
It retains exact full HTML even when returning patches, so
WebSocket recovery can use the same render and consumer-owned version.
Strict snapshots and explicit clears use a JSON envelope even when binary
patches are requested; the legacy binary payload has no place for metadata.
Legacy-only wire frames retain their previous optional-field behavior.

Actor dispatch now holds the consumer render lock, rechecks the owner after
waiting and after the actor result, and retains the lock until a cancelled Rust
operation settles. Cancelled operations send no result or deferred redispatch.
Normal deferred work runs after releasing the lock. Contract failures are
redacted and invalidate the unsent render baseline without discarding assigns;
the next successful actor response is full HTML, not a diff against an unseen
DOM. Failed mounts shut down unregistered actors and release their Python view.

This adds render-contract delivery for the existing legacy-exposure actor path.
Explicit-exposure actor events remain rejected by their existing construction/
dispatch guards. Native binder activation and actor browser acceptance remain
open alongside the other P2/P3 gates.

Verification: the actual actor WebSocket contract test failed before delivery
was added; four subsequent-error tests reproduced an unseen-DOM diff baseline.
The dedicated suite now has 14 passing cases, including real Rust results,
failure recovery, detached metadata, failed-mount view collection, cancellation,
owner replacement and binary-envelope preservation. Final unchanged-code Python
verification passed 30,625 tests with 952 skipped. The Rust workspace suite,
all 75 `djust_live` library tests, warnings-denied Rust lint, and mypy over
1,038 files passed. No live-browser strict-binding acceptance is claimed.

## Evidence and remaining gates

Actual WebSocket (normal and actor mode) and SSE endpoint tests failed before the
mount field existed. Focused tests exercise same-named owner handlers, descriptor
method signatures, wrapper/instance shadowing, property-free discovery, omitted
RPC/defaults, removals and limits. Independent review reproduced a wrapper-method
collision and verified the corrected precedence. Browser-bundle tests drive real
WS/SSE mount receivers for scope separation, additional mounts, remount/navigation,
disconnect, malformed replacement and detached public metadata.

Final unchanged-code verification: three full Python runs each passed 30,565
tests with 952 skipped; three full JavaScript runs each passed 2,104 tests across
191 files. The focused server suite passed 61 tests, asset checks passed 91,
mypy passed for 1,034 files, and bundle ESLint reported zero warnings.
These are automated suite results, not live-browser acceptance evidence.

This does not complete P2. Before activating strict native binding:

1. Deliver initial HTTP-only contracts and complete render-producer coverage,
   including child/component creation, removal and replacement. Verify cached
   DOM updates and recovery producers against the applied-snapshot rules above.
2. Match the current DOM owner and its generation, not just reusable string IDs.
   Cover buffered/stale frames, reconnect and repeated same-path root instances.
3. Connect every binder to the strict collector before loading/optimistic effects,
   with typed-literal provenance, generated native values and form conventions.
4. Execute the real browser/transport matrix and retain server-side validation of
   hand-crafted messages. No browser activation or website publication is claimed
   by mount-frame tests.
