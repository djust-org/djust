# ADR-036: Python-owned event parameters and canonical dj-value markup

**Status**: Proposed
**Date**: 2026-09-19
**Deciders**: Project maintainers
**Evidence baseline**: `0d1aeb882` on `feat/components-catalogue`.
**Related**:

- [ADR-008](008-auto-generated-http-api-from-event-handlers.md): exposed handler APIs.
- [ADR-023](023-incremental-type-enforcement.md): Python type enforcement.
- [ADR-033](033-plain-component-state-and-identity.md): typed component wire values.
- [ADR-034](034-component-scoped-events-and-bindings.md): scoped component callbacks.
- [ADR-035](035-django-native-form-and-object-lifecycle.md): form binding and authorization.
- [ADR-037](037-event-contract-checks-and-executable-documentation.md): shared checks and docs.
- [ADR-038](038-explicit-context-and-state-exposure.md): outbound exposure, distinct from inbound parameter validation.

## Summary

The Python handler signature is the authoritative event parameter contract.
Teach `dj-value-<parameter>` as the one namespace for application event arguments;
ordinary examples need no HTML type suffix when the handler is annotated.
Add an opt-in strict policy for predictable coercion, missing/extra arguments,
and invalid values. Preserve legacy behavior during migration.

This is partly consolidation, not a new coercion system: djust already resolves
type hints and converts several parameter types. The proposal closes concrete
gaps and makes the same rules govern browser, HTTP, component, tooling, and docs.
The server-side configuration and decorator argument are now staged on this
implementation branch; strict browser collection and full acceptance remain
incomplete. See [server integration evidence](notes/036-strict-server-integration.md).

## Evidence and current behavior

- [event_handler](../../python/djust/decorators.py) already exposes
  `coerce_types=True` and records handler metadata.
- [coerce_parameter_types and validate_handler_params](../../python/djust/validation.py)
  already convert strings using annotations and validate handler arguments.
- A focused baseline probe confirms that `"42"` becomes `int` and `"true"` becomes
  `bool` without typed HTML attributes. A `date` annotation leaves an ISO date as
  a string and validation rejects it. `"not-a-bool"` is accepted as `False`.
- The [client parameter parser](../../python/djust/static/djust/src/08-event-parsing.js)
  supports `dj-value-*`, generic `data-*`, `data-dj-*`, and legacy `dj-params`.
  `dj-value-*` currently wins collisions with the legacy namespaces.
- The [AI event guide](../ai/events.md) still teaches `data-*`, catch-all kwargs,
  and defaults for every parameter. Those recommendations obscure required input
  and differ from a closed, typed contract.

## Decision

### D1. Make Python the source of truth

The callable's named parameters, annotations, defaults, and explicit catch-all
define its public inputs. Required parameters stay required; do not recommend
`id=0` or `**kwargs` merely to make malformed events run.

```python
# Staged server API; ADR-036 acceptance is not complete.
from djust import LiveView, event_handler


class ItemSelectionView(LiveView):
    template_name = "items/select.html"

    def mount(self, request, **kwargs):
        self.selected_id = 0
        self.active = False

    @event_handler(parameter_policy="strict")
    def select_item(self, item_id: int, active: bool = False) -> None:
        # A demonstration allowlist, not a substitute for database authorization.
        if item_id not in {42, 87}:
            raise ValueError("Unknown item")
        self.selected_id = item_id
        self.active = active
```

```html
<button type="button" dj-click="select_item"
        dj-value-item-id="42" dj-value-active="true">Select item</button>
```

Business operations still perform domain validation and current object-level
authorization. An integer ID is not an authorized object. Form handlers continue
to use Django Forms for field and cross-field validation, not a duplicate schema.

Mypy/Pyright can check Python calls and declarations, but cannot by themselves
prove that these template strings match the handler. ADR-037 supplies that bridge.

### D2. Define strict conversion before expanding its type surface

Strict mode must either produce a valid declared value or reject the event before
invoking the handler. It must not guess, truncate a prefix, silently substitute
zero/False, or invoke arbitrary constructors named by a client.

| Declared type | Strict input rule |
| --- | --- |
| `str` | Accept a string; do not stringify arbitrary objects/containers. |
| `int` | Accept an integer or a complete decimal-integer string; reject bool, blank text, fractional values, and partial numeric strings. |
| `float` | Accept finite numeric values or complete finite numeric strings; reject bool, NaN, and infinities. |
| `bool` | Accept bool or the case-insensitive strings true/false, 1/0, yes/no, on/off. Reject all other strings and non-boolean JSON numbers. |
| `Decimal` | Accept a finite Decimal, integer, or valid decimal string; reject bool and binary floats to avoid implicit precision loss. |
| `UUID` | Accept UUID or a valid UUID string; reject invalid input rather than forwarding the string. |
| `date` | Accept a date excluding datetime, or a valid ISO `YYYY-MM-DD` string. Reject locale-dependent, timestamp, or impossible dates. |
| `Optional[T]` / `T \| None` | Accept JSON null or a valid T. Optional does not make a missing required argument optional, and empty text is not implicit null. |
| `list[T]` | Accept a JSON array and validate each member using the supported T contract; do not silently interpret a comma-separated string as an array. |

For non-string scalar conversions, trim surrounding ASCII whitespace consistently;
never trim application string values. Limits on payload size, collection length,
and nesting still apply before expensive conversion. Python's bool/int subtype
relationship must not let `True` satisfy an `int` input.

The initial strict surface is intentionally bounded. Unsupported annotations,
ambiguous unions, or unresolved forward references produce registration/check
errors; they must not fall back to unvalidated values. Broader datetime/timezone,
Literal/enum, mapping, and custom schema support require defined semantics and
tests before being advertised. An explicit `Any` or unannotated catch-all is an
escape hatch with reduced checking, reported as such by tooling.

An already correctly typed value is accepted. `coerce_types=False` continues to
disable conversion, not validation: strict mode still rejects values of the wrong
type. Transport-specific shortcuts must not create different accepted values.

### D3. Keep typed wire values, but make them optional and non-authoritative

ADR-033's generated typed attributes remain supported. A component may need a
numeric/boolean value on the client before a server handler runs. This proposal
does not remove those attributes or make existing annotated-string handlers
incompatible under the legacy policy.

For new application templates, prefer untyped `dj-value-*` plus Python annotations.
If an explicit wire type conflicts with a strict handler's type, report it instead
of silently applying a second incompatible conversion.

Strict client parsing must reject invalid typed literals instead of using permissive
prefix parsing or manufacturing null/zero. Preserve enough validation information
to reject malformed values consistently. Client checks improve feedback only;
the server validates every received value, including hand-crafted event messages.

### D4. Standardize the parameter namespace without breaking old pages

The canonical spelling is `dj-value-item-id` for Python `item_id`. Normalize
kebab-case once. Do not teach `data-dj-item-id`, `data-item-id`, or JSON `dj-params`
as equivalent new styles.

In strict-policy bindings, collect explicit `dj-value-*` arguments and documented
event values (such as `value` for input/change); do not turn arbitrary third-party
`data-*` attributes into Python kwargs. The framework must expose the resolved
public collection policy to its client event binder. Server validation remains
mandatory and cannot trust that metadata as an authorization boundary.

In legacy bindings, preserve the existing namespaces and precedence until a
separate removal decision. Deprecation diagnostics must identify actual legacy
event-argument use, not warn on unrelated Bootstrap or application data attributes.
An automated migration must preserve handler parameter names and rendered values.

For strict bindings, duplicate normalized explicit keys or a value supplied both
positionally and by name are errors. Do not silently choose one. Client-generated
event values and explicitly supplied context must have a documented collision rule;
the strict rule is to reject overlapping names rather than overwrite them.

### D5. Separate framework context from application arguments

Routing identities, request objects, bound component sources, and internal event
metadata belong in trusted dispatch context. They are not unexpected kwargs that
application authors must absorb. Never accept client-provided values for a trusted
injected argument merely because a handler declares that parameter name.

After extracting dispatch context, validate only the documented application
payload against the handler contract. A closed signature rejects extra keys.
An explicit `**form_data`/`**kwargs` remains an intentional open contract and may
receive arbitrary application fields subject to its own validation. Do not treat
that as a reason to require catch-alls on every handler.

Preserve positional and keyword-only Python semantics and useful error messages.
Do not hide a misspelled application parameter by dropping every unfamiliar key.
Errors identify the event, parameter, expected type, and correction without echoing
secrets or unbounded client values into logs or user-facing output.

### D6. Resolve policy once and use it everywhere

Opt-in server configuration (staged):

```python
# Staged server setting; keep legacy during migration until acceptance passes.
LIVEVIEW_CONFIG = {
    "event_parameter_policy": "strict",
}
```

The initial default remains `legacy`. A new optional
`@event_handler(parameter_policy=...)` accepts `"strict"` or `"legacy"` and overrides
the project default; these are constrained configuration values, not handler-name
cross-references. Register the resolved contract once and share it with runtime
validation, client collection, system checks, and generated references.

All relevant paths must honor it: WebSocket, JSON HTTP fallback, actor dispatch,
scoped components, and deliberately exposed API handlers. Preserve each path's
authentication and transport protections. New ADR-034 typed subscriptions use
strict contracts, with trusted source injection outside the client payload.

## Completion decisions (2026-09-24)

P2 left the strict-collection conventions open. These are decided. Q1 and Q2
are owner decisions. N1–N3 are implementation choices the owner accepted. A
later change to any of them changes its implementation and tests, not just
this table.

| # | Question | Decision | Reason |
| --- | --- | --- | --- |
| Q1 | Which generated event values (`value`, `field`, `key`, `code`, form fields, paste/copy text) does a strict native binding send? | Contract-aware. A generated key is sent only if the handler declares a parameter with that name, or has a `**` catch-all. `dj-value-*` arguments are always sent, and one that collides with a generated key is rejected. A missing or invalid contract manifest fails closed rather than guessing. The server stays authoritative and still rejects hand-crafted extra keys. | Owner decision. A closed signature is the encouraged contract (D1). A fixed per-directive set would force every strict handler to declare names it doesn't use, such as `field` on every input handler. The client already holds the owner-scoped public contract, so it can send exactly what the handler asks for. |
| Q2 | `_target` (the triggering field or submitter name) under strict | Not sent. Use `field` or an explicit `dj-value-*`. Legacy bindings are unchanged and keep sending `_target`. | Owner decision. `_`-prefixed names are framework-reserved and cannot be declared by a strict handler (D5). Renaming it would add public API that `field` and `dj-value-*` already cover. |
| N1 | How is a client-side strict rejection shown? | The existing error path: a fixed, value-free `console.error` and the `djust:error` event (dev overlay, application toasts). It happens before `dj-disable-with`, optimistic and loading effects. | No new UI or API, and nothing is applied that would then have to be rolled back. |
| N2 | How do HTTP-only pages and the HTTP fallback get contracts? | The initial page renders a framework-internal JSON block (`<script type="application/json" data-djust-parameter-contracts>`) outside the live root. HTTP-fallback render responses carry the additive `parameter_contracts` / `parameter_contract_view` fields that socket frames already use. | Additive, and it keeps the server's VDOM baseline free of framework markup. |
| N3 | Server-issued owner generation tokens | Not added unless the parity matrix shows a concrete stale-owner failure. Two root mounts of the same view path on one page remain a known limitation. | Manifests are whole-tree snapshots applied atomically with the DOM they describe, in receipt order. |

## Alternatives considered

| Alternative | Assessment |
| --- | --- |
| Require HTML type suffixes everywhere | Duplicates Python declarations and cannot enforce server trust boundaries. |
| Leave coercion permissive and document edge cases | Retains silent false/zero values and makes annotations a weak promise. |
| Switch all existing handlers to strict behavior immediately | Breaks accepted payloads, legacy metadata, and form catch-alls. |
| Add a separate schema library as the default | Duplicates signatures and Django Forms; not necessary for the bounded initial contract. |
| One signature contract with an opt-in strict policy | Chosen: consolidates existing behavior with a reviewable migration. |

## Compatibility and migration

Inventory legacy namespaces, blanket defaults/catch-alls, unsupported annotations,
and wire-type conflicts first. Offer diagnostics and canonical examples before
changing defaults. Convert small groups of handlers to strict mode and test both
transports. Do not combine rollout with a silent global removal of `data-*` support.

ADR-037 retires V007's blanket catch-all advice and supplies targeted checks. Do not
claim ordinary Python linters validate template strings. Existing legacy behavior,
including intentionally loose callbacks, stays available during the migration;
changing the project default requires a separately announced compatibility step.

## Scope and acceptance gates

At the baseline, lexical references to
`coerce_parameter_types|validate_handler_params|coerce_types|extractTypedParams|collectDjValues`
appear in **17 source files and 16 test files**, counting Python/JavaScript files
and all three test roots. These are reference counts, not a complete call graph.

Before strict mode is considered supported:

- Freeze a valid/invalid conversion matrix, including false-like booleans, blank
  numbers, partial numbers, bool-as-int, overflow/resource limits, date boundaries,
  nullable values, arrays, and unsupported annotations.
- Test real DOM extraction through wire dispatch, not just direct Python calls;
  add parity tests across every dispatch path and `coerce_types=False`.
- Test missing/extra/duplicate arguments, keyword-only signatures, forms' open
  payloads, client metadata separation, and forged component injection attempts.
- Verify legacy precedence and values remain unchanged for legacy handlers.
- Prove invalid input never invokes application code and diagnostics do not expose
  sensitive payloads. Execute the published examples under the intended policy.

## Retirement (Step R — delete)

This ADR is described above as partly consolidation. Consolidation that leaves
both implementations in place is duplication, so the superseded coercion path is
a named delete gate, on ADR-027's `dormant-define -> wire -> flip -> delete`
playbook (its Step 5 delete landed as #2628).

Step R fires only after **P3**, and only once strict is the default rather than
opt-in — which this ADR does not yet approve.

| Target | Cited at | Retired because |
| --- | --- | --- |
| `coerce_parameter_types` | `validation.py:137` | Superseded by the signature-derived metadata in `_parameter_contract.py` |
| `_coerce_value` | `validation.py:219` | Same conversion, second implementation |
| `_coerce_single_value` | `validation.py:249` | Same conversion, second implementation |

`validate_handler_params` (`validation.py:440`) is **not** a Step R target: it
keeps its role as the dispatch-path guard and is rewired onto the shared
contract, not removed.

Already retired, and recorded here so it is not double-counted: the untyped
`data-value` emission this ADR's *Evidence* section describes was removed by
ADR-033 S3's `event_attrs` sweep. No `data-value` attribute remains in
`static/djust/src/`. Step R therefore claims no client-side deletion.

**Exit conditions.** One deletion PR removing the three functions and their
tests; a grep-verified absence of a second coercion implementation; and a
recorded account of anything retained.

## Consequences and non-goals

New examples become simpler while the server contract becomes stricter. This needs
coordinated client/runtime/tooling changes; changing a doc snippet alone cannot
deliver the guarantee. Coercion does not replace Forms, authorization, business
validation, database constraints, or browser testing. No latency or CPU savings
are claimed without measurement.
