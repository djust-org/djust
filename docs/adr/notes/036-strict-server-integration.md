# ADR-036: staged strict server integration

Status: server implementation on the component-conventions branch, not full
ADR-036 acceptance or a recommendation to migrate an application globally yet.
The [strict core](036-strict-contract-core.md) defines supported input types and
bounds; [the ledger](../component-conventions-implementation.md) tracks remaining
P1–P3, component, form, documentation and exposure gates.

## Policy and invocation

`@event_handler(parameter_policy="strict")` and
`@server_function(parameter_policy="strict")` opt individual server callables in.
`parameter_policy="legacy"` overrides a strict project setting. Omitting the
argument inherits `LIVEVIEW_CONFIG["event_parameter_policy"]`, whose default is
`"legacy"`. Invalid policy values reject instead of falling back permissively.
This setting is distinct from `event_security`, which decides whether a method
is callable at all. Existing authentication, permission and rate-limit gates
remain in their transport dispatchers.

The shared validator returns a server-only bound call for strict handlers.
`validated_call_arguments()` supplies both positional and keyword arguments to
the invoker. It refuses invalid validation results. Root, sticky-child,
LiveComponent and deferred runtime dispatch, the WebSocket deferred helper,
JSON HTTP fallback, exposed event APIs, server-function APIs and the test client
consume this plan. A date/Decimal/UUID or positional-only argument is not flattened
back into an incompatible keyword dictionary.

The inline `_args` envelope is distinct from named application arguments. A
malformed envelope, including explicit null for a zero-argument handler, is not
the same as an absent envelope. Positional/named duplicates fail before invocation.
The HTTP fallback supports both its nested event envelope and its flat/header form.

`coerce_types=False` is read by the common validator, including exposed API,
server-function and test-client paths that previously ignored that declaration.
It disables conversion, not validation. Legacy conversion rules remain unchanged;
honoring an explicitly disabled coercion setting on those paths is a correction
to the old behavior, not a promise of byte-for-byte compatibility for that bug.

## Actor boundary

Rust view and component actors prepare their calls through the same Python
contract before invoking the application. Converted values remain Python objects;
they are not serialized back through Rust's generic `Value` representation.
The WebSocket actor adapter preserves inline arguments for this boundary after
its own permission and parameter checks.

Preparation rejection uses a distinct `InvalidParameters` result. It must not
enter the old render-on-error path or a component actor's raw-state fallback.
Bare, undecorated native actors without the Python framework loaded retain their
existing raw-keyword calls; decorated handlers cannot use that fallback when the
bridge is absent. Once the bridge is loaded, lookup or validation errors reject.

Strict async actor handlers currently reject explicitly. Async non-actor handlers
are supported by the shared async invoker. Async actor support and the complete
actor/transport type matrix remain acceptance work, not silently claimed parity.

## Declarations and public metadata

Strict declaration inspection does not stringify server default values or require
the containing class to exist yet. Contract resolution happens after declaration;
only input annotations are resolved. A forward return reference such as
`-> "MyView"` cannot break an otherwise valid input contract.

Compiled contracts are cached by function declaration and bound/unbound shape,
not by live owner instance. Metadata extraction, debug handler metadata and
signature/schema inspection use the canonical strict contract and omit defaults.
This is not a claim that every debug/state destination is safe under ADR-038:
that separate exposure inventory and construction guard remain in force.

## Verification

The integration fixture covers actual WebSocket (including actor mode), SSE,
both JSON HTTP fallback envelopes, exposed event/RPC APIs, direct Rust view and
component actors, the test client, and routed child/component/deferred runtime
execution. It also checks coercion disabled on an actual RPC call, malformed
arguments, owner lifetime, and default-value redaction.

Independent review found and reproduced two boundary gaps: extracted null inline
arguments were treated as absent, and the debug panel still read raw defaults.
Five promoted regressions failed before those fixes. A later two-case regression
reproduced eager resolution of containing-class return references. These are
fixed, with independent re-review passing the then-current 188 core/integration
cases. A subsequent failing regression exposed cached signatures retaining an
owner through a default object. Binding signatures now retain only default
presence, leaving Python to apply actual defaults at invocation. Both lifetime
and actual-default behavior are tested; the final focused suite passes 190 cases.
Independent source review found no further issues in that final cache fix.

The project Rust target passes, including standalone native actor tests and the
template crate's no-default-features configuration. On this macOS development
host, the stripped release artifact failed to import with a LINKEDIT alignment
error; verification uses the same source rebuilt with
`CARGO_PROFILE_RELEASE_STRIP=false`, installed with `make install-ext` into this
worktree. This is a local build constraint, not a release-packaging fix.

Three full unchanged-code Python runs each passed 30,550 tests with 952 skipped;
timings and scope are recorded in the implementation ledger. Mypy passed 1,032
source files, and repository security, formatting and documentation hooks passed.
No browser/JavaScript, website delivery, cross-worker
restoration or complete ADR acceptance is inferred from server tests.

## Time-travel replay integration

`replay_event()` now resolves the same server-owned policy and canonical
strict binder before restoring any snapshot state. Invalid arguments, invalid
declarations or unavailable contracts refuse replay without restoration,
application invocation, history growth or a new branch. The ephemeral bound
call preserves positional-only and keyword-only arguments, conversion settings
and declaration defaults. History retains the original supplied parameters;
it does not serialize a bound-call object or add declaration defaults.

Legacy replay keeps its raw keyword values. Both policies await declared async
handlers through Django's `async_to_sync` bridge, including nested
`sync_to_async` operations in those handlers. The consumer calls this synchronous
API in its existing worker. Async Python callers should likewise use
`await sync_to_async(replay_event)(...)`; a direct call for an async handler
from a running loop is refused before restoration. Failed restoration refuses
invocation under either policy, but is not a rollback of partially restored
legacy state. Cancellation waits for in-flight replay through the consumer's
existing worker/lock boundary; it does not undo application side effects.

The initial integration tests reproduced skipped numeric conversion, boolean
acceptance for integer parameters, lost positional binding and invocation after
failed restoration. The regression matrix exercises original/override arguments,
recorded/dry replay, invalid/missing/extra/duplicate/forged parameters,
`coerce_types=False`, global policy, explicit legacy override, async execution
and cancellation, declaration failure and default-value nondisclosure.

Verification: 26 replay-specific cases and 329 expanded contract/replay cases
pass. The final full Python suite passed 30,741 tests with 952 skipped; mypy
passed 1,044 source files. No client or Rust production code changed in this
replay-binding slice, and no live-browser or complete ADR acceptance is claimed.

## Remaining P1–P3 work

- Registration/system-check coverage is staged in
  [registration checks](036-registration-checks.md): C021 and V016–V018,
  class-local annotation resolution and reserved argument names. Legacy-code
  migration inventory and template-binding checks (ADR-037) remain.
- Trusted component source injection is staged server-side in
  [trusted dispatch context](036-trusted-dispatch-context.md): one strict
  dispatch-context rule on every transport, trusted contract parameters, and
  ADR-034 output callbacks bound strictly with a framework-supplied source.
  Client collection activation and browser acceptance remain.
- Scope-aware public client contracts for root, child and component identities;
  strict `dj-value-*` collection, typed-literal and collision rejection, and
  unchanged legacy precedence.
- Full supported-type and `coerce_types=False` parity is now executed by one
  shared matrix (see "Transport parity matrix" below). Async actor support
  remains a rejection (V017). Uploads travel their own channel and are not
  event arguments: a strict `**` form handler rejects a file field in the
  browser.
- Executed developer/AI examples, browser verification and migration guidance.

## Transport parity matrix

`python/djust/tests/test_strict_transport_parity.py` sends one 43-row matrix
through eight real paths, and requires identical outcomes on all of them:
- the shared runtime (the WebSocket and SSE dispatcher);
- real WebSocket sessions in normal and actor mode;
- a real SSE session;
- both HTTP-fallback body shapes;
- the exposed event API;
- the test client.

Each row gives either the exact Python value the handler receives, or a
rejection before application code runs. The rows cover:
- every supported type, including partial, underscore, blank and bool-as-int
  numbers, NaN, binary floats to `Decimal`, impossible dates, `Optional`
  null versus missing, and comma-text versus lists;
- `coerce_types=False`;
- positional-only and keyword-only binding;
- an open `**fields: str` form payload;
- extra keys and a forged `component` source.

All eight paths agree on every row, with one recorded difference: the actor
bridge passes a `**` payload through a Rust map, so its keys do not arrive in
the payload's order (equal as a dict; found by a full-suite run, and the
matrix compares open payloads order-independently). The browser half is in
`tests/js/strict_native_binding.test.js`. The strict collector now also
refuses `_`-prefixed `dj-value-*` names, matching the server's reserved-name
rule, along with forged `dj-value-component-id` / `dj-value-view-id`.
