# ADR-038: Explicit context, persistence, and browser exposure

**Status**: Accepted — gates E1–E6 closed and `exposure_policy="explicit"` activated on the completion PR #2954 (targets 1.3; acceptance is confirmed at that PR's review). ER is closed by the written account in D-z; the deletions are scheduled for the major release that makes `explicit` the default.
**Date**: 2026-09-19
**Deciders**: Project maintainers
**Evidence baseline**: `0d1aeb882` on `feat/components-catalogue`.
**Implementation**: [Staged implementation ledger](component-conventions-implementation.md).
**Related**:

- [ADR-012](012-framework-internal-attrs-filter-vs-rename.md): the existing internal-attribute denylist.
- [ADR-034](034-component-scoped-events-and-bindings.md): component ownership and optional observations.
- [ADR-035](035-django-native-form-and-object-lifecycle.md): managed objects and form state.
- [ADR-036](036-typed-event-parameter-contracts.md): inbound event contracts.
- [ADR-037](037-event-contract-checks-and-executable-documentation.md): shared metadata and checks.

## Summary

A public Python attribute must not automatically become template context, browser
data, or persisted state. Introduce an opt-in explicit exposure policy combining:

1. Typed declarations for reactive state and its persistence destination.
2. Django-style `get_context_data()` for deliberate rendering inputs.
3. Separate permission to expose raw values or restorable snapshots to the browser.

This is a hybrid of declarative state and explicit Django context, not another
implicit "all annotated fields are public" rule. Normal Python attributes remain
available for application orchestration without being automatically exported.
The policy and new declaration options below are proposed, not implemented.

## Evidence and problem

At the baseline, several selection rules overlap:

- [ContextMixin.get_context_data](../../python/djust/mixins/context.py) collects
  non-private instance and class attributes, with callable/type exclusions and
  separate handling for components and ORM values.
- [LiveView.get_state and snapshot capture](../../python/djust/live_view.py) walk
  public attributes and apply `_FRAMEWORK_INTERNAL_ATTRS`. Private persistence
  instead tracks private attributes created during mount.
- The [HTTP request path](../../python/djust/mixins/request.py) persists much of
  its pre-context-processor render context as session state, with exceptions such
  as streams. Rendering context therefore also influences restoration.
- [Model serialization](../../python/djust/serialization.py) has sensitive-name,
  field-type, and per-model policies. Those cannot establish the intent of an
  arbitrary string or list stored on a view.
- [Signed snapshots](../../python/djust/security/state_snapshot.py) establish
  integrity, lifetime, and binding, not confidentiality. Encoded signed values
  remain readable; see [Django's signing contract](https://docs.djangoproject.com/en/5.2/topics/signing/).

A synthetic-value probe confirmed that an unused public string entered render
context, `get_state()`, and snapshot capture. An underscore-prefixed string stayed
out of context but entered tracked private persistence. No production secrets or
HTTP/WebSocket traffic were inspected. This proves implicit inclusion, not that
a particular production secret has been disclosed.

ADR-012 preserved familiar configuration names by filtering them, rather than
renaming everything. That compatibility choice still requires every new internal
attribute to be noticed. Managed objects, components, actions, streams, and debug
state make the omission risk broader. Explicit inclusion should become the basis
of a new policy; underscores and sensitive-name filters remain supporting tools.

## Decision

### D1. Separate projections instead of sharing one state dictionary

| Destination | Permitted source | Not implied by |
| --- | --- | --- |
| Server memory | Ordinary attributes and registered state | Python visibility alone exports nothing. |
| Reactive tracking | Typed state/component declarations | Public spelling or arbitrary properties. |
| Template context | Explicit context additions and registered providers | A walk of instance/class attributes. |
| Server persistence | Fields opted into a server backend | Rendering or an underscore prefix. |
| Raw browser data | Fields explicitly permitted for client use | Being JSON-serializable or in template context. |
| Client-restorable snapshots | Explicit client-persistence selection | Server-persistence permission or signing alone. |
| Debug output | A redacted, purpose-specific projection | The entire server context or state. |

Template context is input to trusted server rendering; rendered HTML is visible
to the browser. Returning an object to a template may deliberately disclose its
rendered fields. It does not authorize dumping the entire object into a state
mirror, debug panel, or snapshot. Python-to-Rust conversion is an in-process
rendering boundary, not automatically a browser disclosure.

### D2. Use explicit Django-style context

Proposed authoring shape:

```python
# Proposed policy/options; they are not available in the current release.
from djust import LiveView, event_handler
from djust.decorators import state


class CounterView(LiveView):
    exposure_policy = "explicit"
    template_name = "counter.html"
    count = state(default=0, persist="server")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["count"] = self.count
        return context

    @event_handler()
    def increment(self) -> None:
        self.count += 1
```

```html
<p>Count: {{ count }}</p>
<button type="button" dj-click="increment">Increment</button>
```

In explicit mode, the base context supplies registered framework values and
deliberate context additions, not reflected attributes. The counter renders as
HTML without requiring a raw browser state dictionary. Unrelated `self.service`,
`self.object`, or `self.internal_note` does not automatically enter context.

Configured Django context processors remain rendering providers with documented
merge precedence. Their output never becomes persistence or raw browser state.
Reserved-key collisions must be diagnosed, not silently widen access. The raw
view instance is not a default template value; any compatibility `view` helper
must be a bounded facade, not a route back to every undeclared attribute.
Audit request/user helpers, context sharing, and nested component contexts too.
Context kwargs are deliberate server-side additions, not an automatic merge of
event payloads, request parameters, or restored dictionaries.

An authorized model/queryset can still be returned directly for native rendering;
there is no need to shuttle it from `_items` to `items` merely to activate a
reflective serializer. Template field analysis remains an optimization, not proof
of authorization or a complete confidentiality policy. Dynamic includes, filters,
properties, and trusted template code require their own review and tests.

### D3. Declare reactive state and persistence with typed fields

Extend the existing `state(default=...)` concept into a typed descriptor with one
inspectable field contract. Do not duplicate names across string-based lists such
as `context_fields`, `persist_fields`, and `client_fields`. Python annotations
alone do not opt a value into any projection: configuration and service attributes
also deserve type hints without becoming client data.

For the proposed explicit policy:

- `state(default=...)` declares reactive state; persistence defaults to none.
- `persist="server"` allows a supported value in server-side persistence.
- `client=True` separately permits raw client exposure.
- `persist="client"` enables client snapshot/restore and requires `client=True`.
  Contradictory declarations fail registration rather than implicitly widening.
- Ordinary attributes are internal/transient by default, regardless of underscore.

The flags and typed descriptor behavior are proposals, not today's `state` API.
Prove type inference, independent mutable defaults/default factories, inheritance,
and nested-container mutation tracking before acceptance. A new field in an
application base class must not silently widen a subclass's exposure contract.

Derived context must remain reactive. Track declared state/component dependencies
and explicit invalidation. For opaque dependencies, render conservatively or
require a documented invalidation mechanism; never silently drop an update.
ADR-034's no-op notifications can skip rendering only when no relevant dependency
changed, not because the exposure policy hides the changed attribute.

Live services, requests, authorization caches, callables, and ORM objects are not
persistable just because a fallback serializer can stringify them. Supported
persisted types need explicit codecs and schema/version information.

### D4. Keep server persistence server-side and restore narrowly

`persist="server"` must not put values into browser-readable signed-cookie sessions
or client snapshots. Use a capable server-side backend and an opaque bound handle,
or reject the configuration. Check backend capabilities, not just a backend name.
Storage failure must not fall back to exporting server-only state to the browser.

Preserve user/session/tenant/view binding, expiry, cleanup, and fresh authorization.
Validate the schema and reject undeclared restore keys before assigning anything.
A legitimate old signature is not permission to populate newly introduced internal
attributes. Translate a known prior schema explicitly or remount; do not hydrate
a legacy context dictionary into an explicit-policy view.

Implemented (E2-9, D-i, D-j):
- `exposure_schema_version = N` on the view class (default 1, a positive `int`,
  read without evaluating descriptors) is part of the schema digest. Bumping it
  rejects older envelopes, server and snapshot, and the view remounts.
- A view may define `migrate_state(self, old_schema, values) -> dict`. It runs
  only for a server envelope whose recorded contract version is older than the
  current one, before `prepare_restore`. `old_schema` is that older version and
  `values` is a detached copy. The result is validated as a fresh current
  envelope: missing or undeclared keys and non-primitive values are rejected. A
  raising or invalid hook remounts and logs only the view class and versions.
  The server envelope format is now 2 and records `schema_version`; format-1
  envelopes are unindexed and remount. Child state envelopes remount on a
  version bump; they do not call the hook.
- `DJUST_SERVER_STATE_MAX_AGE` (seconds, 1 to 86400, default 3600) is the
  restore lifetime of explicit server-state envelopes, including child state.
  An invalid value fails closed at runtime and is reported by system check
  `djust.C018`. It does not change the Django session's own lifetime.
- The only codec is `json-primitives-v1`. `Decimal`, dates, `UUID`, model
  instances and other objects are rejected at capture, never stringified.

Store approved identities, not live ORM instances, across persistence boundaries.
Resolve object references using current server-side query/permission rules.
Signing establishes origin/integrity, not current permission or data secrecy.
Client snapshots and server persistence require distinct projections and restore
paths even when they share lower-level encoding utilities.

### D5. Framework features declare bounded contracts

Forms, actions, streams, uploads, and component bindings register the fields they
render, track, persist, or expose. A component descriptor is not a wildcard export
of its state. Nested components follow the same rules as the parent view.

ADR-035's `self.object` is a managed server object, deliberately exposed for
authorized rendering when needed, not ordinary snapshot state. Form input and
errors have separate contracts; sensitive inputs must not enter snapshots/debug
output simply because a form exists. File handles and permission caches stay
server-owned.

Retain sensitive-field exclusions as defense in depth. No raw browser serializer
may fall back to a model-field dump, arbitrary `__dict__`, or string representation.
Safe root inclusion does not make every nested field safe; nested codecs and
explicit client projections must obey the same policy.

### D6. Share policy metadata across all automatic exporters

Purpose-specific serializers consume the same declared metadata instead of
maintaining independent lists of safe names. Cover HTTP, WebSocket, actor paths,
reconnect, live navigation, components, generated client metadata, service-worker
snapshots, time-travel, and debug tooling.

Debugging tooling is not an exception: the debug panel, time-travel and
bug-capture projections show server-only fields as names/types or redacted
values in every mode. Error reporting follows Django instead (decision D-a,
revised): under DEBUG a failure shows its exception and traceback, as Django's
own development output does; in production it is value-free.
Explicit application API responses and push messages still need their own review;
this is not a general data-loss-prevention system.

## Alternatives considered

| Alternative | Assessment |
| --- | --- |
| Expand denylists or require underscores | Needed for legacy compatibility, but each new attribute can reopen an omission risk; private persistence is a separate boundary. |
| Treat every annotated class attribute as public state | Confuses typing/configuration with disclosure and reintroduces ambient inclusion. |
| Explicit `assigns` dictionary for everything | Better than reflection, but a dictionary does not by itself separate rendering, server persistence, and client exports; it also weakens ordinary field typing. |
| Only use explicit `get_context_data()` | Familiar rendering boundary, but insufficient if persistence/debug still scrape attributes or reuse context. |
| Typed state plus explicit Django context and separate client permissions | Chosen: one field contract, clear destinations, familiar attribute access. |

## Compatibility and rollout

Retain implicit behavior as `legacy`; introduce the proposed
`exposure_policy="explicit"` per view. No immediate global default flip or removal
of legacy support is approved. New generators adopt explicit mode only after
transport/rendering parity and migration tests pass.

Provide a values-redacted inventory of inferred names and their destinations
before migration. Use it to identify intended declarations, not automatically
allowlist everything the old implementation found. Migrate context, state,
framework providers, and stored schemas together, with safe remount behavior.

If accepted, this revisits ADR-012's filter-as-primary-design assumption for the
new policy without renaming Django-style public configuration. ADR-012 remains
the legacy policy; this proposal does not change its historical status.
ADRs 035 and 037 must consume explicit declarations instead of adding another
special-case exclusion for each new feature.

## Scope and acceptance gates

At the baseline, lexical references to
`get_context_data|get_state|_capture_snapshot_state|_get_private_state|_FRAMEWORK_INTERNAL_ATTRS`
appear in **94 source files and 160 test files** across Python/JavaScript and the
three test roots. These are reference counts, not an implementation estimate or
complete call graph. This is a broad boundary change, not a small refactor.

Required evidence:

- Synthetic sentinels for undeclared public/private fields, class configuration,
  properties, nested dictionaries, models, context processors, and components.
- Assertions at actual destinations: HTTP HTML/embedded JSON, WebSocket frames,
  state backends, signed snapshots, browser storage, debug panels, time-travel,
  and restored values. Source inspection alone is not enough.
- Deliberately rendered values are absent from raw JSON/snapshots unless separately
  allowed. Internal sentinels stay out of all client surfaces even under DEBUG,
  codec failure, inherited configuration, or unusual nested values.
- Server-only persistence rejects browser-readable storage; reconnect and
  cross-worker restoration work without widening disclosure.
- Forged, expired, cross-session/tenant, old-schema, and extra-key restores fail;
  deleted or newly unauthorized objects cannot be resurrected by a valid snapshot.
- Django/Rust parity for components, forms, actions, streams, typed state, dynamic
  context, invalidation, and no-op notifications; mixed legacy/explicit views do
  not change one another's contracts.
- Measure serialization/rendering cost and migration effort; no unsupported
  latency, CPU-saving, or "zero leakage" claims.

## Completion decisions (2026-09-22)

The gates left these questions open. All are decided. D-a was revised by the
maintainer; the rest were decided with the reasons given, and each is
implemented and tested as listed in the ledger's activation review. A later
change to any of them changes its implementation and tests, not just this
table.

### Runtime and transport

| # | Question | Decision | Reason |
| --- | --- | --- | --- |
| D-a | Explicit-view errors under DEBUG | Errors follow Django. Under `DEBUG` they show full detail: the technical 500 page, detailed WebSocket/SSE error frames and dev overlay, logs with tracebacks, and the traceback ring. In production they are value-free: a generic page or frame, a static log line, and `got_request_exception` sent with a value-free exception. | Maintainer decision. `DEBUG` is a development setting, and developers expect Django's behaviour there. Production, where exposure matters, stays value-free. One rule (`diagnostics_policy_allows`) drives every error site. |
| D-k | A background result whose authorization was revoked | Dropped, with the foreground denial (a static error and close 4403). | A result computed for a principal who is no longer authorized must not be delivered. Matching the foreground denial gives the client one recovery path. |
| D-l | Server-originated turns (tick, push, NOTIFY, `url_change`) | Each is authorized against a fresh session before its hook runs. It commits declared state before its frame and refreshes the client snapshot. | Without persistence a reconnect would restore stale state. Without fresh authorization a revoked session would keep receiving renders. The commit follows the foreground event path. |
| D-r | Client snapshot on server-originated frames | Primary-view `async`/`tick`/`broadcast` frames carry the refreshed token, and the client accepts it. | The turn committed declared state, so back-navigation must not restore a pre-turn value. Frames are serialized under the render lock and the token is captured after the commit, so the last frame always carries the latest state. |
| D-o | Actors under explicit policy | Excluded: `use_actors` with `"explicit"` is a configuration error, and the runtime refuses as a second line. | Actor render state lives outside the projections this ADR enforces. Supporting it would need its own exposure contract, and no user depends on the combination. |
| D-p | Older clients | Explicit views need the client that speaks the `async_complete` batch protocol. The bundled client does; no version negotiation is added. | djust ships its client with the framework (no CDN or npm), so server and client versions move together. A negotiation layer would guard against a mismatch the deployment model already prevents. |
| D-q | `DjustLogSanitizerFilter` covers only the `djust` logger | Outside ADR-038; tracked in #2947. | Log injection concerns control characters, not view values, so it is independent of this policy. |

### Browser storage

| # | Question | Decision | Reason |
| --- | --- | --- | --- |
| D-b | Service-worker VDOM and shell caches for explicit pages | Not written. Explicit pages mark themselves ineligible, through a header and a mount-frame field. | Those caches persist rendered HTML at rest with no identity binding. An explicit page opted out of implicit persistence. |
| D-n | Service-worker state-cache lifetime | The worker enforces the snapshot max age on lookup. The client clears the state, VDOM and shell caches when the HMAC identity marker changes or disappears. This applies to every page. | A stored token outliving the session, or a previous user's cached pages, is the same problem for legacy pages (#2948). The marker never contains a raw id or session key. |
| D-s | A warm shell cache can briefly show the previous user's page chrome | Accepted and documented. | Explicit pages never enter the shell cache (D-b), so the residue is legacy layout chrome, not explicit view state. Closing it would need a network round trip before serving the shell, which defeats opt-in `instantShell`. Covered with #2948. |

### Framework providers

| # | Question | Decision | Reason |
| --- | --- | --- | --- |
| D-c | Presence metadata | The meta is application output. `track_presence` no longer injects `username`/`user_id` for explicit views, and the rebroadcast is documented. | The application chooses what peers see. The framework should not add identity fields the application didn't pass. |
| D-v | The presence id (`str(user.id)`) that peers receive | Kept. | Peers need a stable id to deduplicate cursors and lists. A user id is an identifier, not view state. Applications that need anonymity override `get_presence_key`. |
| D-d | Observability SQL parameters | Redacted for nonlegacy owners and restricted scopes. SQL text, tags and timing are kept. | Parameters usually come from view state. The endpoint is DEBUG- and localhost-only, but it is a server-to-browser surface. |
| D-e | Form input and errors | Not persisted by default. The per-field opt-in is `persisted_form_input(...)`. Password and sensitive fields are refused at configuration, render empty, and are scrubbed from error text. | Form input is exactly the undeclared data ADR-038 exists to keep out of storage. A sensitive field must never be one opt-in away from persistence. |
| D-f | `@action` error text | A generic "Action failed", unless the handler raises `ActionError`. | Exception text can carry view state or query values. `ActionError` gives developers an explicit, deliberate channel. |
| D-g | Uploads in flight across reconnect | Not resumed. Resume is refused, and resumable writers keep no resume record for explicit views. | The remount posture is simpler and safer. A resume record that nothing reads (client filename, progress) would be data kept for no purpose. |
| D-h | Components assigned on the instance | A configuration diagnostic names the attribute at the first explicit render. Components are declared at class level and are transient across reconnects. | Automatic discovery is the reflection this ADR removes. Class-level declaration makes the manifest static and reviewable. |
| D-m | Lazy, non-sticky and mixed-policy children | Non-sticky explicit children are transient: identity-checked and never persisted. `lazy=True` explicit children are refused before any context is built. Explicit server persistence under a legacy parent is refused. | Lazy fill has no authorization path yet, and refusing is safer than a half-authorized render. Transient children cover the common "embed a widget" case without a persistence contract. |
| D-t | Tags the Rust renderer does not handle (`dj_activity`, `colocated_hook`, form tags) | Outside ADR-038; tracked in #2958 and documented in the guide. | The gap exists under both policies and is about renderer coverage, not exposure. |
| D-w | Two same-type sticky children at different depths sharing a view id | The server refuses to route an ambiguous id. There is no render-time check. | Refusing fails closed. A duplicate id is an application bug the client can't address either, and a render-time check would add a tree walk to every render for a rare mistake. |

### Contracts, codecs and schema

| # | Question | Decision | Reason |
| --- | --- | --- | --- |
| D-i | Codecs | v1 is JSON primitives only. `Decimal`, dates, `UUID` and model references are refused, never stringified. | Every added codec is a new restore path to secure. Refusing is visible and safe. Storing an id and reloading the object is the pattern D4 asks for anyway. |
| D-j | Old or unindexed envelopes | Rejected, followed by a remount. A class-level `exposure_schema_version` plus an opt-in, validated `migrate_state` handle deliberate schema changes. | Guessing at old shapes is how undeclared keys come back. An explicit version and hook make any translation a reviewed decision. |
| D-x | `ProviderContract.tracked` has no reader | Kept as declared metadata. Invalidation uses the conservative change walk (E2-8). | The manifest is the input ADR-037's checks need. Narrowing change detection to declared keys risks dropping updates, which D3 forbids. |
| D-y | `FormMixin` combined with `WizardMixin` | A provider collision at contract compile. | Both claim `form_data`/`form_choices`. Failing loudly beats one silently shadowing the other. No combination exists in the repository. |

### Retirement

**D-z: the ER schedule.** Every Step R target survives activation. Each one
still serves legacy views, and legacy remains the default:

- the `_FRAMEWORK_INTERNAL_ATTRS` walk exclusions;
- `ContextMixin`'s attribute walk;
- private-attribute persistence;
- the sensitive-name floor under the walk;
- the `"legacy"` arm.

The retirement starts when a future major release makes `explicit` the
default. At that point each target gets its own deletion PR with the reference
inventory re-run. Step R's exit conditions accept a written account of any
target that survives; this paragraph is that account for activation.

## Retirement (Step R — delete)

The case for explicit exposure is that it *replaces* heuristic machinery rather
than sitting beside it. That claim is only true if the implicit machinery is
deleted, so the deletion is a named gate here and not a hoped-for consequence.
This follows ADR-027's `dormant-define -> wire -> flip -> delete` playbook
(ADR-022), whose terminal delete landed as #2628 — the precedent that a terminal delete
is scheduled work with its own PR.

Nothing here approves the flip. `legacy` remains the default and ADR-012 remains
the legacy policy, exactly as *Compatibility and rollout* states. Step R fires
only after **E6**, and only for views under `exposure_policy="explicit"`.

**Named targets.** Each is cited at the evidence baseline; the count is the
reference inventory already recorded above (94 source files, 160 test files).

| Target | Cited at | Retired because |
| --- | --- | --- |
| `_FRAMEWORK_INTERNAL_ATTRS` (65 entries) | `live_view.py:105`; consumers `live_view.py:1063`, `:1341`, `_exposure.py:261-263`, `runtime.py:2709`, `time_travel.py:273`, `:310` | D1 makes inclusion explicit, so a denylist of framework names is no longer a policy boundary |
| `ContextMixin.get_context_data` instance/class attribute walk | `mixins/context.py:215` (`self.__dict__.items()`), `:242` (class-level collection) | D1: template context comes from explicit additions and registered providers |
| `_user_private_keys`, `_snapshot_user_private_attrs`, `_get_private_state` | `live_view.py:644`, `:853`, `:871` | D1: an underscore prefix stops being a persistence selector |
| `_ALWAYS_EXCLUDED_FIELDS`, `_SENSITIVE_MODEL_METHODS`, `_SENSITIVE_MODEL_METHOD_PREFIXES`, `_sensitive_field_types()` | `serialization.py:60`, `:69`, `:78`, `:81` | Name-matching is a floor under an implicit walk; with no implicit walk it has nothing to floor |
| The `"legacy"` policy arm | `_exposure.py:90`, `:97`, `:113` | Deleted last, with the flag, once no supported path reaches it |

**What Step R does not delete.** ADR-012's naming decision stands: Django-style
public configuration keeps its familiar names and is not renamed. The
`serialization.py` sensitive-name floor also governs *deliberate* ORM rendering
(D1's "Template context" row), so it is retired as an implicit-walk backstop
only — if deliberate rendering still needs a floor, Step R records that and keeps
it, naming the reduced surface.

**Exit conditions.** A deletion PR per target, each removing the code and its
tests together; the reference inventory re-run and the delta recorded; and, as
ADR-027 did, a written account of any target that was **not** deleted and why.
A target that survives contradicts the simplification premise and is reported,
not quietly dropped.

## Consequences and acceptance questions

New attributes no longer acquire audiences by accident. Explicit declarations and
context mappings add some work, but become inspectable by reviewers and ADR-037's
tooling. Application code can still deliberately render or send sensitive data;
the framework prevents implicit discovery, not every possible application leak.

Before acceptance, prove the typed `state` extension/default factories, select the
server-backend capability checks, and specify the full framework context-provider
manifest. These are design gates, not permission to leave a reflective fallback
inside explicit mode.
