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

This is delivery, not client installation. A receiver must not update its
contracts merely because a frame arrived: version rejection, buffering and
failed patches must be resolved first, and successful installation must precede
`reinitAfterDOMUpdate()` (which can fire `dj-mounted`).

Actual normal WebSocket and SSE event tests now assert render snapshots as well
as mount snapshots. The same regression on actor WebSocket responses still
fails: actor results are emitted through a separate Rust render path, outside
the Python runtime's render lock. Capturing contracts from the Python owner
after awaiting that result is not sufficient evidence that they describe the
same render. Actor capture, bespoke WebSocket tick/push/recovery paths, explicit
child background frames, initial HTTP and HTTP fallback remain delivery gates.
No claim of complete transport parity is made.

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

1. Deliver initial HTTP-only contracts and refresh contracts with applied render
   responses, including child/component creation, removal and replacement.
2. Match the current DOM owner and its generation, not just reusable string IDs.
   Cover buffered/stale frames, reconnect and repeated same-path root instances.
3. Connect every binder to the strict collector before loading/optimistic effects,
   with typed-literal provenance, generated native values and form conventions.
4. Execute the real browser/transport matrix and retain server-side validation of
   hand-crafted messages. No browser activation or website publication is claimed
   by mount-frame tests.
