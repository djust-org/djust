# ADR-034 C1 typing proof and integration inventory

Status: concrete binding staged; real-view typing gate open. C1 and ADR-034
remain incomplete. Release unassigned.
This supplements [ADR-034](034-component-scoped-events-and-bindings.md), not a
new public API or acceptance decision.

## Readiness audit

The roadmap's active component-conventions block delegates to the implementation
ledger. D1/D2 require a typing proof before production binding is chosen. The
[executable proof](../../tests/typing_component_bindings/README.md) now targets
the real private dropdown and LiveView, replacing the isolated prototype.
That transition exposed an inherited `Any` from Django's unstubbed View in
mypy: nineteen of twenty negative cases are rejected, but a misspelled component
attribute is not. Pyright rejects all twenty. Development-only Django stubs have
been proposed for approval; the missing case remains a failing gate, not waived.

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

Next C1 deliverable: close the real-view typing dependency gap, then complete
lifecycle and transport acceptance (including signed snapshots/debug restore,
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
keyed collections, full signed-snapshot/debug lifecycle, actor integration and
website/AI-reference publication. Actor binding is explicitly refused rather
than pretending the actor's separate dispatch path supports the new registry.

Verification of this staged binding: the full Python suite passed 30,838 tests
with 952 skipped; the focused binding/catalogue/compiler/dispatch matrix passed
232 tests; package mypy passed 1,048 source files. The separate real-view typing
gate remains failing at one expected negative mypy location (a component-name
typo inherited through untyped Django); Pyright rejects all twenty locations.
These are not browser or complete lifecycle acceptance results.
