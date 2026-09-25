# ADR-036: registration and system-check coverage

Status: staged P1 evidence on the ADR 034–037 branch, not P1 closure or
ADR-036 acceptance. The [strict core](036-strict-contract-core.md) and the
[server integration](036-strict-server-integration.md) define what strict
dispatch accepts; this slice reports declaration problems at startup instead
of on the first event. [The ledger](../component-conventions-implementation.md)
tracks the remaining P1–P3 gates.

## One resolver for the check and dispatch

The checks call the runtime's own functions: `get_project_parameter_policy`
and `get_handler_parameter_policy` for the policy, and
`get_strict_handler_contract` for the contract. The method is bound to a
placeholder owner, the same bound shape dispatch compiles, so the check and
dispatch read one cached contract. A test asserts that the check's contract
object *is* the one a real view instance binds.

Before this slice, deferred annotations resolved against module globals only.
Under `from __future__ import annotations`, or with a quoted forward
reference, a name defined in the class body (`ItemId = int`) failed at event
time. `ParameterContract.compile` now resolves each input annotation against
the defining class body first and then the module globals, as eager
evaluation would. The defining class is found from the function alone: its
`__qualname__` is walked from `sys.modules`, and the class must actually hold
the function. So the result never depends on which subclass or instance asks,
and the function-keyed cache stays sound. Classes defined inside a function
body are not reachable by qualified name, so their methods resolve against
globals only. The check and dispatch agree on that too, and a test pins it.

Resolution runs one parameter at a time, so every declaration error names its
parameter. The return annotation is still never evaluated.

## What the checks report

| ID | Severity | Condition |
| --- | --- | --- |
| `djust.C021` | Error | `event_parameter_policy` is not `'legacy'` or `'strict'`. Reported once; handlers inheriting it are not repeated. |
| `djust.V016` | Error | A strict handler's contract fails to compile. Causes: an unresolvable annotation (misspelled, `TYPE_CHECKING`-only, or another class's name), an unsupported type or shape, a named parameter without an annotation, a reserved argument name, or invalid per-handler policy metadata. |
| `djust.V017` | Error | A strict `async def` event handler on a `use_actors = True` view. The actor bridge rejects it. |
| `djust.V018` | Warning | `@event_handler(params=[...])` names a different set from the strict signature. |

The checks inspect user `LiveView` and `LiveComponent` subclasses, including
routed views: the URLconf is walked first, as `check_liveviews` does. Each
class's public `@event_handler` and `@server_function` members are listed
through its MRO, mirroring owner-manifest discovery. Components stop at the
framework component base. Each declaration is reported once, under the
declaring class when that class is checked, otherwise under its first user as
`View.handler() (declared as Mixin.handler)`. No view, component or handler
is constructed, mounted or invoked. Messages carry a file, a line, a hint and
a fix hint. The IDs are suppressible like every other djust check. Dispatch
still rejects the events either way.

**Framework-reserved names (D5).** The compiled contract now rejects a
keyword-capable parameter named `view_id` or `component_id`, or one starting
with `_`. Every transport extracts those keys as routing context before
validation, the HTTP flat body drops `_`-prefixed keys, and the strict client
collector refuses them. Such a parameter could therefore never receive an
application value: a required one failed every event, and an optional one
silently kept its default. Positional-only parameters may use any name, since
their values arrive in `_args`. This changes strict dispatch only: the error
now appears at compile time instead of as a missing argument. Legacy binding
is unchanged.

**Legacy is silent.** A legacy-policy handler never produces an ADR-036
message, whatever its annotations. The ADR's migration inventory (reporting
future strict failures to legacy code) is deferred rather than put into
default `manage.py check` output for every existing project. With the project
policy set to strict, inheriting handlers are checked like decorated ones,
including framework mixin handlers; the demo project reports none.

**V007.** "Add `**kwargs`" is no longer reported for strict handlers: a closed
strict signature is the contract, and a catch-all would open it (D1, ADR-037
D3). Legacy handlers keep V007 unchanged.

## Evidence

`python/djust/tests/test_parameter_contract_checks.py` has 32 tests. The
fixture views live in a generated module with deferred annotations. It is
imported under a top-level name, so qualified-name resolution behaves as it
does for application code. It is unloaded and marked `abstract` after each
test, and a test proves the classes are collected. The matrix has 14 invalid
handlers, each producing exactly one expected ID and exact message. It also
covers V017 on an actor view, a component, an inherited handler, a mixin
declaration, project-wide strict, an invalid project value, invalid handler
metadata and suppression. Valid handlers (class-local alias, a class name
shadowing a module name, a forward reference to a later module name,
positional-only `_`-prefixed and keyword-only parameters, a server function,
and an async handler on a non-actor view) produce no message, and each binds
and runs through `validate_handler_params`. Every V016 case raises the same
`ContractError` at dispatch that the check printed.

Against the previous resolver, 6 of these tests fail, covering the
class-local, mixin-local and reserved-name cases.

Focused verification: the new file, the strict core, dispatch and metadata
tests, the check-ID uniqueness, docs-table and import-footprint pins, and the
existing check suites. mypy is clean over `python/djust`. The full-suite
result is recorded in the ledger.

## Remaining

- Trusted argument separation (D5) beyond reserved names: injected component
  sources, and ADR-034 typed subscriptions.
- Client and transport acceptance: strict binder activation, wire-hint
  conflicts and browser verification (P2/P3).
- Template-binding checks: handler existence, missing and unknown arguments
  from `dj-value-*`, and wire-type conflicts. These are ADR-037 D1/D3; this
  slice checks declarations, not bindings.
- The legacy-to-strict migration inventory. Reduced-checking (`Any`,
  catch-all) reporting beyond the public contract metadata.
- Components hosted by actor views (V017 inspects views only), and handlers
  reachable only under `event_security` `open`/`warn` without a decorator.
