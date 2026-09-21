# ADR-034 C1 typing proof and integration inventory

Status: prototype verified; C1 and ADR-034 remain incomplete. Release unassigned.
This supplements [ADR-034](034-component-scoped-events-and-bindings.md), not a
new public API or acceptance decision.

## Readiness audit

The roadmap's active component-conventions block delegates to the implementation
ledger. D1/D2 require a typing proof before production binding is chosen. The
[executable experiment](../../tests/typing_component_bindings/README.md) proves
concrete descriptor results and signature-preserving output decorators using
ordinary Python typing, in strict mypy and Pyright, without additional packages.

The milestone-audit categories concerning ORM migrations, auditlog and Celery
are not applicable to this experiment: no models, persisted schema or jobs are
changed. Existing components are acknowledged in ADR-034's context; the explicit
integration inventory below is the remaining production boundary. The proposal
and followups remain consistent with the ledger's open C1–C4 gates and roadmap.
The triggering two-dropdown scenario still requires real dispatch/browser tests.

## Existing surfaces that a production binding must preserve

| Existing surface | Required integration |
| --- | --- |
| `components/base.py`: `LiveComponent.__get__`, `_bind`, `BoundComponent` | Replace/adapt the new-family binding with a genuinely concrete object; retain legacy behavior. Do not add a misleading return annotation to the proxy. |
| `runtime.py`: `_dispatch_component_event`, component-only patch selection | Registry-only lookup for the new family; no legacy descriptor alias fallback for stale targets. Preserve security checks, callbacks in the originating cycle, and parent/sibling changes. |
| `_parameter_metadata.py`: `_event_methods` | Discover actual component actions without exposing subscription callbacks or invoking application descriptors. |
| `mixins/components.py`, `mixins/context.py`, `mixins/rust_bridge.py` | Register, render, expose and dirty-track the new binding consistently in both template engines. |
| `serialization.py`: `BoundComponent` handling | Persist only declared serializable configuration/state/lifetime data; reconstruct subscriptions from server code. Never serialize owner-bound callbacks. |
| ADR-038 exposure and lifecycle contracts | Keep construction guard closed; validate ownership and safe provider/exporter behavior before activation. |

These are integration requirements, not newly reproduced defects. The experiment
does not change any of these production paths.

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

Next C1 deliverable: implement and test concrete per-owner bindings and validated
subscription metadata on the existing registry/dispatch lifecycle, including
duplicate/foreign ownership, inherited replacements, trusted source injection,
direct callback rejection, async/error behavior and safe restoration. Reuse these
type fixtures against the real implementation before C1 can be marked complete.

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

This closes a prerequisite, **not C1**: there is still no exported interactive
dropdown, per-owner concrete production binding, output emission/source injection,
registry lifetime restoration or end-to-end component dispatch. The private
compiler is not a second application subscription spelling. The typed prototype
must be rerun against the eventual concrete implementation; do not advertise it
as the released `djust.components.interactive` API.

Verification for this staged compiler: 41 focused contract/dispatch-guard tests;
full Python suite 30,788 passed / 952 skipped; mypy 1,046 files clean; the separate
strict mypy/Pyright prototype retains all twenty expected negative locations.
No new browser, Rust or client-JavaScript acceptance is claimed for this slice.
