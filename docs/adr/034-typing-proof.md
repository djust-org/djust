# ADR-034 C1 typing proof and integration inventory

Status: concrete binding staged; real-view typing proof passes. C1 and ADR-034
remain incomplete. Release unassigned.
This supplements [ADR-034](034-component-scoped-events-and-bindings.md), not a
new public API or acceptance decision.

## Readiness audit

The roadmap's active component-conventions block delegates to the implementation
ledger. D1/D2 require a typing proof before production binding is chosen. The
[executable proof](../../tests/typing_component_bindings/README.md) now targets
the real private dropdown and LiveView, replacing the isolated prototype.
That transition initially exposed inherited `Any` from Django's unstubbed View
in mypy. The supported proof configuration now follows Django source declarations,
and the LiveView stub includes the actual runtime constructor signature. Both
checkers reject all twenty negative locations with clean positive fixtures and
passing runtime assertions. No new dependency, plugin, substitute owner or
negative-test waiver was needed; the earlier Django-stubs proposal is unnecessary
for this gate. A regression checks the constructor stub against the source AST.

The milestone-audit categories concerning ORM migrations, auditlog and Celery
are not applicable to this experiment: no models, persisted schema or jobs are
changed. Existing components are acknowledged in ADR-034's context; the explicit
integration inventory below is the remaining production boundary. The proposal
and followups remain consistent with the ledger's open C1–C4 gates and roadmap.
The two-dropdown scenario now has real HTTP/WS dispatch tests; browser acceptance
still remains.

## Existing surfaces that a production binding must preserve

| Existing surface | Required integration |
| --- | --- |
| `components/base.py`: `LiveComponent.__get__`, `_bind`, `BoundComponent` | Replace/adapt the new-family binding with a genuinely concrete object; retain legacy behavior. Do not add a misleading return annotation to the proxy. |
| `runtime.py`: `_dispatch_component_event`, component-only patch selection | Registry-only lookup for the new family; no legacy descriptor alias fallback for stale targets. Preserve security checks, callbacks in the originating cycle, and parent/sibling changes. |
| `_parameter_metadata.py`: `_event_methods` | Discover actual component actions without exposing subscription callbacks or invoking application descriptors. |
| `mixins/components.py`, `mixins/context.py`, `mixins/rust_bridge.py` | Register, render, expose and dirty-track the new binding consistently in both template engines. |
| `serialization.py`: `BoundComponent` handling | Persist only declared serializable configuration/state/lifetime data; reconstruct subscriptions from server code. Never serialize owner-bound callbacks. |
| ADR-038 exposure and lifecycle contracts | Keep construction guard closed; validate ownership and safe provider/exporter behavior before activation. |

These are integration requirements. The staged binding now touches registry,
session persistence and change detection; the remaining acceptance is not implied
by the typing proof.

## Chosen proof technique and alternatives

Use a concrete output namespace, callable protocols with named keyword payloads,
and bounded callable type variables that return the original method unchanged.
The receiver uses `Never` only as a contravariant compatibility constraint; see
the experiment README for the important non-dispatch boundary.

A plain `Callable`/`ParamSpec` decorator preserves signatures but by itself does
not impose each output's keyword payload contract. An untyped registry or an
`Any` descriptor hides misspellings. A cast from `BoundComponent` to a concrete
dropdown would misrepresent the runtime object. None is used in this proof.

The prototype creates real concrete objects rather than requiring a type-checker
plugin. This enables normal method completion and rename/type diagnostics, but
does not decide how production state storage and restoration should be adapted.
That integration must keep one public type without duplicating legacy state or
weakening exposure. Once released, source/output names and callback keyword names
become compatibility commitments, so they need shared runtime/check/docs coverage.

## Verification and next deliverable

`make test-component-binding-types` checks twenty negative source locations in
both strict configurations, clean positive/prototype files, and executable
identity/isolation/async assertions. General pytest tests protect the runner's
missing/unexpected diagnostic detection and execute the runtime proof.

Next C1 deliverable: complete
lifecycle and transport acceptance (including browser signed-navigation/debug transport,
dynamic registration/checks and real-browser verification). Neither a standalone
type proof nor session reconnect alone is sufficient to close C1.

## Production subscription compiler (staged)

The private `djust._component_subscriptions` module now supplies immutable output
contracts and a class-time subscription compiler, called by
`LiveView.__init_subclass__`. It records declaration/output/callback names rather
than bound view methods. It rejects aliased/reused declarations, foreign sources,
duplicate subscriptions, removed/incompatible replacement declarations, invalid
callback signatures and conflicting event/API/RPC decorators. Inherited method
overrides are revalidated and retain their output subscription and template
mutation guard. Signature failures identify owner, source, output and callback
without echoing annotation-evaluation errors.

`event_handler` and `server_function` reject subscription stacking in either
order. The shared event-security check rejects marked subscription callbacks
even under the legacy `open`/`warn` policy. Tests exercise that actual shared
validator and an actual HTTP fallback POST, not only declaration inspection.

This closes a prerequisite, **not C1**. The compiler is not a second application
subscription spelling; do not advertise the private implementation as the released
`djust.components.interactive` API.

Verification for this staged compiler: 41 focused contract/dispatch-guard tests;
full Python suite 30,788 passed / 952 skipped; mypy 1,046 files clean; the separate
strict mypy/Pyright prototype retains all twenty expected negative locations.
No new browser, Rust or client-JavaScript acceptance is claimed for this slice.

## Concrete server-owned binding (staged)

`djust.components._interactive.DropdownMenu` is now an actual `LiveComponent`
instance per owner, not a typed facade over `BoundComponent`. It uses opaque
registry IDs, a framework-owned per-instance cache, constructor-owned copied
configuration and typed `open`/`selected` state. Class declarations cannot be used
as live instances. Local toggle/close/select actions validate their owner and
selection before emitting their declared output, inject the actual bound source,
and await the matching callback in the same dispatch cycle. Callback errors do
not roll back already applied state or cause a second invocation.

Native HTTP/WS routes, permission checks and error handling remain in place.
The HTTP fallback now awaits declared async handlers. Session save/restore uses
an explicit binding record, preserves opaque identities, rejects collisions and
malformed state before mutation, and validates restored selection against current
server configuration. It never restores callbacks or constructor configuration.
The component event path now invokes the existing bounded, opt-in session save
for this new family; actual reconnect tests verify state and IDs survive.

Change detection snapshots materialized bindings under their public context keys
without persisting the cache. A view handler changing `self.menu.open` renders;
an unchanged component close produces a no-op. Changed sibling/view state remains
part of the full parent render. These tests exercise Rust-backed HTTP/WS rendering;
a separate Django Engine test verifies bound markup/state rendering and escaping.

Still open: public export, browser accessibility, native client observations,
keyed collections, browser signed-navigation/debug transport, actor integration and
website/AI-reference publication. Actor binding is explicitly refused rather
than pretending the actor's separate dispatch path supports the new registry.

Verification of this staged binding: the full Python suite passed 30,838 tests
with 952 skipped; the focused binding/catalogue/compiler/dispatch matrix passed
232 tests; package mypy passed 1,048 source files. At that revision, the separate
real-view typing gate missed one expected mypy location; the source-following
configuration and truthful constructor stub described above subsequently closed it.
These are not browser or complete lifecycle acceptance results.

## Same-lifetime debug restoration

Debug capture now reads the concrete dropdown's explicit `open`/`selected` state,
not its public framework attributes. Both the whole-view and single-component
scrubbers use the same exact state validator. Malformed component records are
rejected before changing that component; stale/unmounted owners are rejected.
Restoration preserves the registry identity, applies current configuration when
validating selections, and emits no output callback. Whole-view restoration
retains the existing per-field/partial-failure semantics for other view state;
this does not introduce a transaction across the entire snapshot.

Thirteen new regression cases cover capture, both restore paths, malformed
records, current configuration and stale owners. The initial eight cases failed
before implementation. The focused binding and time-travel suites pass 131
tests. This is server-helper coverage, not live debug-panel or signed
back-navigation acceptance. Those transport/lifecycle gates remain open.

The full Python run passed 30,850 tests with 952 skipped and one failure in the
new changelog fragment's formatting. The fragment was corrected and its complete
test module rerun; no Python source changed after that full run. Package mypy
passed all 1,048 source files.

## Fixed-binding signed navigation restoration

Strict capture for the existing signed navigation path now adds a versioned
`__interactive_bindings__` manifest. Records contain the current declaration
name, opaque binding ID, and `open`/`selected` state, not callbacks or constructor
configuration. Capture uses the same exact record validation as restore, rejecting
extra exported fields, invalid primitive types and aliased cache entries before
anything is signed. Ordinary debug capture retains its separate same-lifetime schema.

The existing transport must first verify the signature, age, view and session
binding. The fixed-binding adapter then validates the whole manifest, resolves
declarations statically, recompiles their subscriptions, and rejects unknown
declarations, malformed values, duplicate IDs and registry collisions before
changing component or view state. It rebinds current server code, normalizes
selection against current items, and emits no output while restoring. A later
constructor/registration failure rolls back this adapter's registry and binding
changes before the native fresh-mount fallback. This is not a general transaction
for arbitrary application-constructor side effects or legacy public view fields.

Interactive resumes send current rendered HTML even when the client reports a
prerendered page, so current configuration is not hidden behind stale controls.
Removing that condition makes the native resume test fail on the missing HTML.
The twenty-three new tests include a real WebSocket mount-produced signed blob, fresh
consumer restore with the same IDs, subsequent correctly scoped selection,
unsigned/cross-session/schema-invalid rejection, and registration rollback.
The initial focused binding, signing and exposure matrix passed 153 tests; package mypy
passes 1,050 source files. This does not prove browser focus/patch behavior,
collections, actor support, or explicit-exposure activation. The existing
construction guard remains closed. The separate real-view typing proof now passes
both supported checkers as described above; that is not full C1 acceptance.

Final verification for signed bindings and the real-view typing correction:
30,875 Python tests passed with 952 skipped, the expanded focused matrix passed
221 tests, and package mypy passed 1,050 source files. The standalone mypy/Pyright
proof and its runtime assertions also passed. The preceding full run's only two
failures were whitespace-sensitive source pins for the wrapped resume condition;
those retain the same legacy gate assertion with whitespace normalization, while
the native regression and removal canary verify the interactive behavior.
