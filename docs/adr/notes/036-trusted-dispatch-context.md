# ADR-036 D5: trusted dispatch context

Status: staged P1 evidence on the ADR 034–037 branch, not P1 closure or ADR
acceptance. This is the server half of D5. Client collection and browser
acceptance remain open; see [the ledger](../component-conventions-implementation.md).

## The rule

A strict handler receives only application arguments, and only from the
client payload. Framework context never arrives through that namespace:

- **Routing identities** (`view_id`, `component_id`) are consumed by the
  router that resolves the target. If one reaches strict validation, that
  route did not handle it, and the event fails closed with a value-free
  error. It never falls back to the root handler.
- **Transport bookkeeping** (`_cacheRequestId`, `_activity`) is read by the
  transport and dropped before binding, on every path.
- **The positional envelope** (`_args`) is consumed by the binder, as before.
- **Any other key** is an application argument. An undeclared key, including
  ADR-034's source name `component` or an unknown `_`-prefixed key, is
  rejected as an extra argument. The
  [registration checks](036-registration-checks.md) already make
  keyword-capable parameters with reserved names a declaration error.
- **Framework-injected parameters** are *trusted*.
  `ParameterContract.compile(handler, trusted=...)` excludes them from type
  compilation and from the public metadata. `bind(..., trusted=...)` binds
  them only from server-owned values:
  - A client key naming one is rejected.
  - A positional value reaching its slot collides and is rejected.
  - A `**catch-all` cannot absorb it.
  - The dispatcher must supply exactly the declared set, or the call fails
    as a `ContractError`.

  Contracts are cached per trusted set.

The single definitions are `FRAMEWORK_ARGUMENT_NAMES` and
`TRANSPORT_METADATA_KEYS` in `_parameter_contract.py`, applied in
`validation._validate_strict_handler_params`. Every strict path goes through
that function. Legacy validation is not touched.

## ADR-034 output callbacks

`DropdownMenu._emit` previously called the subscription callback directly with
`{"component": self, **payload}`. It now binds through the callback's strict
contract, with `component` trusted (`_component_subscriptions.SOURCE_PARAMETER`,
which is also the name `OutputContract` forbids in payloads). The declared
output payload is validated by that contract, without conversion. A callback
whose payload annotation the strict contract does not support is rejected
before invocation. V016 reports it at startup, through the same cached
contract.

This is the foundation ADR-034 C1 consumes; it is not a new public API. The
parameter name `component` is ADR-034 D1's. Callbacks were already
unreachable from the browser, because the shared event-security check rejects
subscriptions. No ordinary `@event_handler` gains injected arguments: none
exist today, and exposing one (for example a request or a component source for
a view handler) would need its own API decision.

## Path map

| Path | Routing consumed there | Strict behavior before | Now |
| --- | --- | --- | --- |
| WS and SSE, `ViewRuntime.dispatch_event` | pops `_args`, `_cacheRequestId`, `_activity`; `view_id` (child), `component_id` (component) | forged keys rejected; unknown child/component errors | unchanged |
| WS actor: runtime actor branch, `dispatch_actor_event`, Rust, `actor_handler_arguments` | only `_args`; `component_id` is not a route there | any strict actor event carrying `_cacheRequestId`/`_activity` was rejected; `component_id` reached the root handler's validation | metadata dropped; routing keys fail closed |
| HTTP fallback (`mixins/request.py`), nested and flat bodies | `component_id`; no child routing | `_cacheRequestId` failed strict `@cache` events in both shapes, and `_activity` in the nested one; the flat body silently discarded unknown `_` keys | metadata dropped; unknown `_` keys rejected in both shapes; `view_id` fails closed |
| Exposed event API, server functions (`api/dispatch.py`) | none | metadata rejected | metadata dropped |
| `LiveViewTestClient` | none | metadata rejected | metadata dropped |
| Time-travel replay | none | recorded metadata rejected replay | metadata dropped |
| Component routes (runtime, HTTP) | router strips `component_id` and binds the resolved component as `self` | correct | unchanged |
| ADR-034 output callbacks | server-invoked only | contract bypassed | strict contract, trusted source |

Legacy handlers keep their existing mapping on every path. For example,
`**kwargs` still receives `_cacheRequestId`, and the flat HTTP body still drops
other `_` keys. Two tests pin this.

## Evidence

`python/djust/tests/test_trusted_dispatch_context.py` has 68 cases:

- trusted-contract unit tests;
- the forged/metadata matrix on the shared runtime, both HTTP body shapes,
  the exposed API, server functions, the test client, replay and the actor
  bridge;
- real WebSocket (normal and actor mode) and real SSE sessions;
- legacy controls;
- ADR-034 source forgery over the validator and a real HTTP component event;
- output-callback strict binding.

No response echoes the forged value. Against the previous code, 25 of these
cases fail: every metadata case on HTTP, API/RPC, the test client, replay and
the actor bridge, the real actor WebSocket, the flat-body unknown key, and the
trusted-contract and callback cases. The non-actor WS/SSE runtime already
rejected forged keys; those cases pass on both versions.

## Remaining

- Client side: strict `dj-value-*` collection is not yet activated in native
  binders (P2). The collector already refuses reserved names.
- A `view_id` equal to the root view on the actor path fails closed, where the
  non-actor runtime pops it. The client never sends the root's id.
- The legacy descriptor alias (`LiveComponent._make_event_handler`) still takes
  `component_id` from the client and resolves a view attribute by that name.
  It is a legacy handler and unchanged. Under a project-wide strict policy its
  contract does not compile (unannotated `value`, reserved `component_id`), so
  those events fail; it needs an explicit legacy pin or a strict replacement.
- Public injected-argument API for ordinary handlers: not proposed; it would
  need an ADR-034/ADR-036 decision.
