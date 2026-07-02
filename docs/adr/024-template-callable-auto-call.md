# ADR-024: Template callable auto-call — Django parity in the sidecar resolution walk

**Status**: Accepted — 2026-07-02 (implementation to follow; tracked as a v1.1.x pipeline task)
**Relates to**: [ADR-022](022-v1.1-code-quality-single-path-convergence.md) (single-path convergence — this is the same parallel-path-drift class, #1646, at the template-resolution axis)

## Context

Django's template engine auto-**calls** callables during variable resolution
(`django.template.base.Variable._resolve_lookup`): `{{ user.get_full_name }}`
calls the method, `{{ workspace.memberships.count }}` calls the manager
method, at *every* lookup segment. djust's Rust template engine does not, and
the divergence is **silent** — it renders *something*, just the wrong thing:

- `{{ request.user.get_full_name }}` → the literal string
  `<bound method AbstractUser.get_full_name of <User: jordan>>`
- `{{ workspace.memberships.count }}` → empty output

Both were hit in downstream production app builds (DJUST_LESSONS gotcha #7);
the standing workaround is to pre-compute every such value to a primitive in
`get_context_data`. The project goal chosen for this ADR is **Django-template
compatibility**: existing Django templates (and Django muscle memory) should
Just Work when ported to djust.

### Where the divergence actually lives

Context reaches the Rust engine over **two channels** (established each render
in `_sync_state_to_rust`, `python/djust/mixins/rust_bridge.py`):

1. **Eager** — values serialized to Rust `Value`s via
   `normalize_django_value` + `update_state`. On this path djust **already
   auto-calls**: the JIT serializer calls `get_*` methods and the
   manager/queryset methods `all`/`count`/`exists`
   (`python/djust/optimization/codegen.py:198-204`, `:315-321`), and model
   serialization calls `@property` values and explicit scalar `get_*` methods
   (`python/djust/serialization.py:514` `_add_property_values`, `:444`
   `_add_safe_model_methods`).
2. **Lazy sidecar** — non-JSON-friendly values (Django model instances,
   `request`, and request-scoped context-processor keys like `user`/`perms`,
   which are *deliberately excluded* from the eager channel —
   `rust_bridge.py:681-685`) are handed to Rust as live `Py<PyAny>` objects
   (`set_raw_py_values`). At render time `Context::resolve`
   (`crates/djust_core/src/context.rs:235-263`) walks them **`getattr`
   segment-by-segment under the GIL** — a genuine render-time Python callback
   that never checks `is_callable()` and never calls.

So the bug class is **not** "djust can't auto-call" — it is parallel-path
drift: auto-call exists on the eager path and is absent on the sidecar path.
Anything resolvable only via the sidecar — request-scoped objects
(`user.get_full_name`), reverse relations / managers
(`workspace.memberships.count`; reverse relations are skipped by eager
serialization) — silently misses it. The `<bound method …>` text is produced
when the un-called bound method falls through to the `FromPyObject for Value`
string catch-all (`crates/djust_core/src/lib.rs:235`); the empty output is the
same walk resolving to `None`.

Neither path implements Django's two safety attributes: the repo has **zero**
occurrences of `alters_data` or `do_not_call_in_templates` (the eager path is
protected only by its `get_*`/`all`/`count`/`exists` allowlist).

### The LiveView-specific cost dimension

In Django, a template renders once per request. In djust, a LiveView
re-renders on **every WebSocket event**, so an auto-called ORM method is a
query per event (potentially per keystroke). "Do what Django does" is
therefore not automatically free; the chosen posture (below) is parity with
observability, not parity alone.

## Decision

### 1. Auto-call in the sidecar walk, with Django's exact semantics

Port `Variable._resolve_lookup`'s callable handling into `Context::resolve`
(`crates/djust_core/src/context.rs:235-263`) — the single point where the
divergence lives. Inside the per-segment loop, after each `getattr` **and** on
the final resolved value (Django calls at every segment: `{{ obj.get_settings.theme }}`
must call `get_settings()` mid-walk), if the object `is_callable()`:

| Condition (checked in order) | Behavior | Django parity note |
|---|---|---|
| `do_not_call_in_templates` truthy | use the object as-is, do not call | covers Model classes (`ModelBase`) and `Choices` enums for free |
| `alters_data` truthy | resolve to `Value::Null` (renders empty); never call | **the data-destruction guard** — stops `{{ user.delete }}` / `{{ qs.update }}`; Django stamps `alters_data=True` on `Model.save/delete`, `QuerySet.delete/update`, etc. |
| otherwise | `call0()` | |
| `call0()` raises `TypeError` | probe `inspect.signature(obj).bind()`: bind **fails** (callable needs args) → `Value::Null`; bind **succeeds** (the `TypeError` came from inside the method) → propagate | Django's exact distinction; the probe runs only on the cold error path |
| `call0()` raises anything else | propagate | djust's existing render-error path handles it, as Django propagates |

The call result feeds the existing `current.extract::<Value>()` conversion
unchanged. The shared `FromPyObject for Value` catch-all
(`crates/djust_core/src/lib.rs:235`) is **not** modified — it also serves
`update_state` ingestion, and auto-calling there would change eager-channel
semantics.

The GIL is already held in this block (`Python::attach`); the added hot-path
cost is one `is_callable()` check per segment, on the sidecar path only. The
eager fast path is untouched.

**Guard ordering is load-bearing**: the `alters_data` refusal ships in the
*same commit* as the `call0()`. An auto-call without it is a data-destruction
footgun — a template typo like `{{ user.delete }}` would delete the row on
first render.

### 2. Unify guard semantics across the existing eager auto-call sites (#1646)

The two eager auto-call sites gain the same `alters_data` /
`do_not_call_in_templates` checks before calling:

- `python/djust/optimization/codegen.py:198-204` and `:315-321` (JIT `get_*`
  + `all`/`count`/`exists`),
- `python/djust/serialization.py:444` (`_add_safe_model_methods`).

Today their allowlists make a violation unlikely (a custom `get_*` method
*could* still set `alters_data = True`), but after this ADR "djust auto-calls
template callables" is a single documented behavior — every site that
auto-calls must share one guard semantics, or the drift class this ADR fixes
is reintroduced one level down.

### 3. Observability rider: one-shot ORM-call warning (debug only)

When a sidecar auto-call's `__self__` is a `Manager`/`QuerySet` instance
(checked against classes imported once and cached), emit a **one-shot warning
per (view class, dotted path)** through the existing Python logging bridge
(the GIL is held at the call site):

> `[djust] Template path 'workspace.memberships.count' auto-calls an ORM
> method — this runs on EVERY re-render (each WebSocket event). Consider
> precomputing it in get_context_data() if this view re-renders frequently.`

Debug mode only; production renders stay silent. This is the chosen posture:
**parity + observability** — fresh data on every render is arguably what a
*live* view wants, and the warning (not a cache) is the guardrail. Per-turn
memoization and query-budget enforcement were considered and rejected
(Alternatives).

### 4. Config kill-switch

`LIVEVIEW_CONFIG["template_auto_call"]`, default `True`. Setting `False`
restores pre-ADR sidecar behavior (no call). This is a kill-switch for
unforeseen production regressions, not a feature toggle — candidate for
removal at 2.0. Rationale: the change alters render output for any template
currently (mis)rendering a bound-method string, and a one-line escape hatch is
cheap insurance on a framework with external consumers.

### 5. Testing requirements (doc-claim-verbatim, #1046)

Every row of the semantics table above gets an asserting test, plus:

- the two reported symptoms as regression tests through the real render path
  (`user.get_full_name` renders the name; `workspace.memberships.count`
  renders the number — reverse relation, sidecar-only);
- a mid-path call test (`{{ obj.get_settings.theme }}`);
- an `alters_data` refusal test proving the method was **not executed**
  (side-effect sentinel, not just output);
- gate-off via the kill-switch: `template_auto_call=False` restores the old
  output (#1468);
- Rust unit tests in `djust_core` for the resolve-walk branches + Python
  integration tests; the eager-site guard sweep (Decision 2) gets its own
  tests at each site (#1104: N sites, N tests).

## Consequences

- **Ported Django templates work.** `{{ user.get_full_name }}`,
  `{{ qs.count }}`, `{{ obj.method.attr }}` behave as in Django. The
  pre-compute-in-`get_context_data` pattern remains documented as the
  *performance* pattern, no longer a correctness requirement.
- **Behavior change, strictly less broken.** Templates that today render
  `<bound method …>` text or empty output start rendering values. No
  plausible template depends on the old output; downstream consumers
  (djust.org, djustlive) should be spot-checked after the framework bump per
  the #1849 browser-test rule.
- **Per-event ORM cost is accepted and documented.** A template ORM call is a
  query per re-render by design; the debug warning and the docs guide carry
  the mitigation guidance. No caching is introduced (no staleness bugs in a
  framework whose promise is live data).
- **Security surface**: auto-call executes model/manager methods named in
  templates. Templates are developer-authored (not user input) in djust's
  model, and the `alters_data` guard blocks the destructive built-ins —
  matching Django's threat model exactly. The docs gain a note that
  `alters_data = True` should be set on any custom mutating method, as in
  Django.
- **Docs**: the templating guide's divergence note (DJUST_LESSONS #7 / the
  JSONField-note sibling) is replaced by "the engine calls callables like
  Django"; `docs/SECURE_DEFAULTS.md` gains the `alters_data` pattern row.

## Alternatives considered

1. **Eager dep-driven pre-call at serialization** — the template-variable
   extraction (`crates/djust_templates/src/parser.rs:1125-1146`) already
   yields full dotted paths, so the serializer *could* pre-call exactly the
   paths the template names. Rejected as primary: it re-architects the
   deliberate request-scoped-key exclusion, must trust dep-extraction
   completeness (includes, filters, dynamic paths), duplicates the existing
   JIT mechanism — and the sidecar fallback still exists, so the drift class
   survives. The JIT eager auto-call remains as the existing complement.
2. **Docs + detection only** (system check / render-time bound-method
   detector, no behavior change) — rejected by the compatibility goal; the
   silent-wrong-output failure mode would persist for every new consumer.
3. **Parity + per-turn memoization** — caches auto-call results within one
   render turn. Rejected: protects only the within-one-render duplicate case,
   adds Rust↔Python cache machinery, and invites staleness bugs.
4. **Parity + hard query-budget guard** — count sidecar ORM calls per render,
   error above a threshold. Rejected for now as design surface; the one-shot
   warning covers the discovery need. Revisitable if real-world reports show
   the warning is insufficient.
