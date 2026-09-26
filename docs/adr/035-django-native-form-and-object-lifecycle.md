# ADR-035: Django-native form hooks and an authorized object lifecycle

**Status**: Accepted — delivery verified on staged 1.3 docs (djust-docs pinned to 1.3.0rc3, 2026-09-25; production docs.djust.org is bumped with 1.3.0). Gates F1–F2 closed on `feat/adr-034-037`, with evidence in the [acceptance review](component-conventions-implementation.md#adr-035-acceptance-review--f2); acceptance is confirmed at that branch's review. `ModelFormMixin` is available from djust 1.3. FR (Step R) is open: its trigger is met for adopting views, and it is scheduled as its own deletion PR on ADR-027's playbook, after the deprecation window this ADR requires for removing legacy support.
**Date**: 2026-09-19
**Deciders**: Project maintainers
**Evidence baseline**: `0d1aeb882` on `feat/components-catalogue`.
**Implementation**: [Staged implementation ledger](component-conventions-implementation.md).
**Related**:

- [ADR-017](017-object-permission-lifecycle.md): object-level authorization.
- [ADR-034](034-component-scoped-events-and-bindings.md): explicit ownership and typed component events.
- [ADR-036](036-typed-event-parameter-contracts.md): event parameter contracts.
- [ADR-037](037-event-contract-checks-and-executable-documentation.md): checks and executable documentation.
- [ADR-038](038-explicit-context-and-state-exposure.md): proposed explicit rendering, persistence, and browser exposure.

## Summary

Application authors should customize forms through Django-shaped public hooks,
not populate `_model_instance` before an order-sensitive `super().mount()` call.
Keep `FormMixin` for ordinary forms and create forms. Add an opt-in
`djust.forms.ModelFormMixin` for declarative single-object editing, with
`model`, `get_queryset()`, `get_object()`, `get_form_kwargs()`, and `self.object`.

The public object is an authorized, request/event-local framework object, not an
ordinary persisted reactive attribute. The existing object-permission lifecycle
remains the authority. The public form-construction hooks are implemented on this
branch, and so are F1's authorized object lifecycle (see
[Lifecycle decisions](#lifecycle-decisions-2026-09-25)) and F2's form
acceptance. `ModelFormMixin` is available from djust 1.3; it is not in the
1.3.0rc1 pre-release.

## Context

At the baseline:

- [FormMixin](../../python/djust/forms.py) and the [AI form guide](../ai/forms.md)
  teach assigning `_model_instance` before calling the mixin's `mount()`.
- `_create_form()` constructs the form privately; model identity is stored in
  `model_pk`/`model_label`, and `_ensure_model_instance()` reconstructs the model
  with a default-manager lookup after serialization.
- [LiveView.get_object](../../python/djust/live_view.py) already exists, but its
  default returns `None`. Setting `model = Project` does not currently provide
  Django's single-object lookup behavior.
- [check_object_permission](../../python/djust/auth/core.py) re-resolves the
  object and publishes `_object` only after permission succeeds. This existing
  policy must not be duplicated or bypassed by form reconstruction.
- Rendering models/querysets through the [context pipeline](../../python/djust/mixins/context.py)
  and storing them in [state snapshots](../../python/djust/tests/test_state_snapshot_orm_early_validation.py)
  have different contracts. Making a model public by spelling it `self.object`
  is not sufficient to make persistence safe.

Django's [form hooks](https://docs.djangoproject.com/en/5.2/ref/class-based-views/mixins-editing/)
and [single-object hooks](https://docs.djangoproject.com/en/5.2/ref/class-based-views/mixins-single-object/)
provide the vocabulary. djust still needs an explicit event/reconnect lifecycle;
ordinary Django HTTP view code cannot simply be copied into `mount()` unchanged.

## Decision

### D1. Provide a public form-construction pipeline

`FormMixin` gains the familiar hooks below. All existing entry points, including
initial rendering, field validation, submission, and restore, use the same
pipeline. Framework caches must not be separate construction paths.

| Hook | Contract |
| --- | --- |
| `get_form_class()` | Return `form_class`; report missing configuration clearly. |
| `get_initial()` | Return an independent dictionary of initial values. |
| `get_prefix()` | Return the configured form prefix, without inventing component identity. |
| `get_form_kwargs()` | Assemble initial values, prefix, current bound data, and supported upload references. |
| `get_form(form_class=None)` | Instantiate through the hooks; preserve normal Django form validation. |
| `form_valid(form)` / `form_invalid(form)` | Remain the existing application success/error extension points. |

Distinguish no submitted data from an empty submitted mapping: `{}` is a bound
form submission, not an unbound initial form. Merge semantics must preserve
deliberate application overrides. Reuse Django's field initialization and binding
rules rather than independently copying model attributes into a competing form.

Event payloads are not `request.POST`. Native JSON HTTP fallback and WebSocket
events supply the same validated form data to these hooks; neither implies that
JSON transports binary files. Existing upload support supplies files through its
own authorized lifecycle. JavaScript-disabled HTML POST support remains separate.

### D2. Use an opt-in model-form adapter for editing

`ModelFormMixin` extends djust's `FormMixin`; it is not Django's
same-named class imported under an alias. It supplies the single-object editing
contract and is declared before `LiveView` in the MRO:

```python
# Available from djust 1.3 (not in the 1.3.0rc1 pre-release).
from djust import LiveView
from djust.forms import ModelFormMixin
from .forms import ProjectForm
from .models import Project


class EditProjectView(ModelFormMixin[Project], LiveView):
    template_name = "projects/edit.html"
    model = Project
    form_class = ProjectForm
    pk_url_kwarg = "pk"
    login_required = True

    def get_queryset(self):
        return super().get_queryset().filter(owner=self.request.user)

    def has_object_permission(self, request, obj):
        return obj.owner_id == request.user.pk

    def form_valid(self, form):
        self.object = form.save()
        self.success_message = "Saved!"
```

The route supplies `pk`; no application `mount()` override is needed solely to
find the object or construct the form. `ProjectForm` must explicitly declare its
editable fields. Authorization fields such as owner are not mass-assignable merely
because the view exposes a form.

`get_queryset()` honors an explicit queryset or `model` using a fresh queryset.
`get_object(queryset=None)` follows Django's configured pk/slug lookup vocabulary;
calls without arguments remain compatible with ADR-017. A supplied queryset must
not bypass the subsequent object-permission check. Ambiguous or absent lookup
configuration raises an actionable configuration error.

This adapter is for single-object editing. Create forms continue to use
`FormMixin` with a `ModelForm` and no existing instance. Never infer creation from
a missing, deleted, unauthorized, or invalid edit identifier. A failed edit lookup
must stop the edit flow, not instantiate a new record. Generic create/update view
classes and automatic URL generation are outside this ADR.

### D3. Resolve, authorize, bind; never reconstruct around authorization

For the new adapter, the framework must establish this order:

1. Establish the trusted request and server-resolved URL kwargs, then run the
   existing view-level authentication/permission checks.
2. Resolve the edit target through the configured queryset and run ADR-017's
   object-permission policy before exposing object data or initializing its form.
3. Bind the authorized object and initialize the form. Application `mount()`
   customizations must not be necessary to manufacture an instance for the mixin.
4. Before every later form event, re-resolve/re-authorize the target, then build
   the form against that authorized instance and the current submitted data.
5. Run validation and the application's success/error hook; render only authorized
   state. After mutation, subsequent events must observe current access rules.

Use the existing permission helpers and transport failure behavior. An opt-in
preparation step may be needed before form initialization; do not globally reorder
legacy `mount()` semantics or invent an application-owned second auth lifecycle.
The prototype must prove the ordering on both HTTP and WebSocket paths, including
legacy post-mount enforcement. A cached authorized object may be shared within
one dispatch; it must not become a session-long permission bypass.

Denied or missing edit targets prevent validation/save hooks from running.
Authentication failures, lookup failures, and developer exceptions fail closed.
The new edit adapter must enforce "an object is required" even though the generic
ADR-017 helper also supports views for which no object is applicable.

### D4. `self.object` is a managed public API, not raw reactive storage

Use ADR-038's explicit contracts for new-policy views rather than growing a
denylist for every form feature. Legacy views still require compatibility filters.

Expose `self.object` through framework-managed storage associated with the
authorized object for the current dispatch. It must not create a second model
cache alongside `_object` and `_model_instance`.

- Rendering may expose the authorized object through the native context/JIT
  pipeline, honoring sensitive-field exclusions. Do not serialize every field
  merely to provide the familiar name.
- Persist only the permitted identity/reference and ordinary form state; exclude
  live ORM objects, forms, querysets, and permission caches from public snapshots.
- On reconnect/back-navigation restore, reconstruct the reference through the
  view's configured queryset and current permission policy. A restored client
  field cannot select a different model class or edit target.
- The public setter supports the ordinary `self.object = form.save()` idiom for
  the same edit target; it is not an alternate authorization path or a way to
  silently retarget the view. Identity changes require explicit navigation.
- Declared `model`, `queryset`, and form configuration are framework configuration,
  not accidental template or snapshot fields. Their typing and context visibility
  must be explicit, including when `self.object` is unbound.

Retain unsaved form input and validation errors where existing restore semantics
permit it, but revalidate before saving. Object deletion or access revocation
during a disconnect must not restore an editable form for that record.

### D5. Preserve explicit application policy

Keep the current meaning of `form_valid`: the application decides whether to
save, call a service, or redirect. The adapter must not save a second time, invent
a success URL, grant object permission, or wrap all operations in an undocumented
transaction. Multi-record transactions and concurrency policy remain application
decisions using normal Django facilities.

Fresh lookup prevents reuse of a persisted stale object, but does not eliminate
lost-update races between lookup and save. Applications still choose appropriate
update fields, database expressions, optimistic version checks, or locking for
their operations; no blanket concurrency guarantee is implied by this adapter.

Prefer small public hook overrides over new wrappers around `mount()`. Existing
ordinary forms and composite forms must not acquire model lookup requirements.

## Lifecycle decisions (2026-09-25)

F1 left six public questions open. They are decided. Q1–Q6 are owner
decisions. N1–N6 are implementation choices within the rules above, which the
owner accepted. A later change to any of them changes its implementation and
tests, not just this table.

| # | Question | Decision | Reason |
| --- | --- | --- | --- |
| Q1 | How does the object reach templates? | Always as `object`, render-only: never persisted, never in a client snapshot, `None` when unbound. `context_object_name` is an opt-in alias, default `None`. There is no automatic `<model_name>` alias. | Owner decision. D4 requires explicit visibility. An automatic alias such as `project` could silently collide with application state. |
| Q2 | What does a missing, or filtered-out, target return? | `PermissionDenied`, exactly like a failed permission check. Each transport gives its existing denial: HTTP 403, a `permission_denied` frame, WebSocket close 4403. | Owner decision. It reuses the existing transport failure behavior (D3), and a client cannot tell a missing record from a forbidden one. |
| Q3 | Where does the lookup id come from? | `self.kwargs`, set to the route's resolved URL kwargs on every transport. It is a framework slot, kept out of context and snapshots, and never the client's mount parameters. | Owner decision. It is Django's own spelling (`View.setup` sets it on HTTP), so Django-style `get_object()` overrides work unchanged. The WebSocket mount merges client parameters into mount's `**kwargs`, so those cannot identify the object. |
| Q4 | What may be assigned to `self.object`? | The same record: same concrete model and the same pk, as in `self.object = form.save()`. Anything else, including `None` or an assignment while unbound, raises a plain `ValueError` that points to navigation. | Owner decision. D4: identity changes require explicit navigation. |
| Q5 | What if a view overrides neither `get_queryset()` nor `has_object_permission()`? | The default stays permissive (`has_object_permission` returns `True`, as in Django's `UpdateView`). `djust.S013` warns about such a view. | Owner decision. Refusing the class outright would break public-data and staff-only editors. The warning makes the IDOR shape visible. |
| Q6 | How is `self.object` typed? | `ModelFormMixin` is generic in the model: `ModelFormMixin[Project]`. It still works at runtime without a type argument. | Owner decision. This matches django-stubs' single-object mixins. |
| N1 | Lookup vocabulary | Django's: `model`, `queryset`, `pk_url_kwarg`, `slug_url_kwarg`, `slug_field`/`get_slug_field()`, `query_pk_and_slug`, `get_queryset()`, `get_object(queryset=None)`. A route with neither kwarg is `ImproperlyConfigured`, which fails closed. `get_object()` does not authorize; the lifecycle does. | Django vocabulary; D2. |
| N2 | Mount ordering and reuse | `mount()` resolves and authorizes through the ADR-017 helper before the form is built. It leaves a one-shot verdict that the next object check consumes, whatever that check is, and honors only for the same request object. So the post-mount check on every transport does not repeat the lookup, and the next event always resolves again. On denial, mount builds no form and the post-mount check reports the denial. | Authorization precedes form construction (D3) without reordering legacy `mount()`. One lookup per dispatch, and no session-long cache. |
| N3 | Storage | `self.object` is a managed view over ADR-017's existing `_object` slot, not a second cache. `_djust_object_required` makes "no object" a denial in the shared check, which also clears the slot on any denial. | D4: no second model cache. |
| N4 | Route binding | HTTP GET/POST bind the kwargs Django resolved. The WebSocket/SSE runtime binds `page_url`'s kwargs only when that route serves the mounted view class, after both restore mechanisms and before `mount()`. Mounts that have no route (the HTTP API, `{% live_render %}` children) fail closed. | Client parameters, restored state and URLs routed to other views never select the object. |
| N5 | Legacy compatibility filters | The adapter's configuration names and `kwargs` are skipped by the legacy attribute walk. `object` enters the legacy context before model serialization, so it renders field by field like any other model. It is dropped from all three legacy session saves (GET, HTTP POST, WebSocket event). Explicit-policy views receive it from a render-only provider. | D4 for legacy views, without a denylist per form feature. |
| N6 | Application policy | No save, success URL or transaction is added (D5). `form_class` must be a `ModelForm`, and a form is never built without an authorized object. `_model_instance` on an adapter view is a configuration error. | D2 and D5; "Do not mix both instance mechanisms". |

**Publication decision (2026-09-25, owner).** The form guide and the AI form
reference teach `ModelFormMixin` now, in new sections marked "Available from
djust 1.3" (not in the 1.3.0rc1 pre-release), with a migration recipe from
`_model_instance`. The owner judged that the version note satisfies "do not
change current release examples to import an unavailable class", since it
states that the class is not in the current release. The existing
`_model_instance` examples are unchanged. The examples are executed as tests.
The generator and AI-schema updates this ADR also names are recorded as
pending ADR-037 work in the ledger.

Known limitation: SPA `live_patch` navigation within one adapter view keeps
the mounted route kwargs, so it cannot retarget the record. Use a full
navigation (`live_redirect`) to edit a different record.

## Alternatives considered

| Alternative | Assessment |
| --- | --- |
| Document `_model_instance` more prominently | Low change cost, but retains a private, order-sensitive application contract. |
| Rename it to public `object` everywhere | Superficially familiar; unsafe without auth, snapshot, and configuration handling. |
| Mix Django's editing CBVs directly into LiveView | Familiar names, but HTTP binding, MRO, dispatch, and save behavior do not automatically match reactive events. |
| Public form hooks plus opt-in djust `ModelFormMixin` | Chosen: standard vocabulary with an explicit djust lifecycle and additive migration. |

## Compatibility and migration

Add the hooks and adapter without changing existing `FormMixin` model-instance
behavior. Preserve `_create_form()` overrides and `form_instance` consumers via a
documented compatibility bridge; test delegation direction to prevent recursive
construction or skipped customizations.

Publish a migration recipe from `_model_instance` to the new adapter only after
the lifecycle gates pass. Do not mix both instance mechanisms in one new-style
view; report conflicting configuration. Any removal of legacy support needs an
explicit deprecation window. No API is removed by accepting this proposal alone.

Update the website form guide, AI form reference, generators, and lifecycle checks
together under ADR-037. Clearly version the new adapter; do not change current
release examples to import an unavailable class.

## Scope and verification

At the baseline, a lexical search for `FormMixin|_model_instance|_create_form`
in Python files finds **10 source files and 12 test files**, counting test files
across `tests/`, `python/tests/`, and `python/djust/tests/`. These are reference
counts, not an estimate that every file must change or a complete dependency graph.

Implementation acceptance requires:

- A no-custom-`mount()` edit example, a create form, a non-model form, and hooks
  overridden independently and through inherited views.
- Correct binding for empty submission, initial values, prefixes, choices, related
  fields, and supported uploads; no double save or divergent validation paths.
- Unauthorized/missing target, tampered identity, access revoked between events,
  and failed developer permission callbacks: no form mutation or data disclosure.
- Real HTTP, WebSocket, reconnect, and back-navigation tests; input/errors preserved
  as specified while ORM objects and permission caches are not persisted.
- An authorization-order test that fails if form construction or validation occurs
  before authorization; a query-count test for the within-dispatch reuse contract.
- Django and Rust template rendering, type-checking of the public object/hooks,
  legacy override compatibility, and browser-visible validation/save feedback.

## Retirement (Step R — delete)

The stated problem is that authors populate `_model_instance` before an
order-sensitive `super().mount()`. If the Django-shaped hooks land and the
private attribute survives, the order-sensitivity survives with it, so the
deletion is a named gate following ADR-027's terminal-delete playbook
(its Step 5 delete landed as #2628).

Step R fires only after **F2**, and only for views adopting `ModelFormMixin`.

| Target | Cited at | Retired because |
| --- | --- | --- |
| `_model_instance` attribute and its declaration | `forms.py:228`; used at `:342-344` (the `model_pk`/`model_label` stamp in `mount()`) and `:527-531` (`get_form_kwargs()`); the adapter's two conflict guards at `:996` and `:1105` go with it | Replaced by `self.object`, resolved through `get_object()` under the authorized lifecycle |
| `_ensure_model_instance()` | `forms.py:471-501`; called at `:463`, `:627`, `:686` | Exists only to "re-hydrate `_model_instance` from stored PK if lost after WS serialization" — a repair for state the new lifecycle does not create |
| The `_model_instance` docstring example | `forms.py:213-220` | Teaches the pattern this ADR replaces |

Line numbers were refreshed with F1 (2026-09-25).

`_create_form` (`forms.py:541`, called at `:353`, `:368`, `:464`, `:630`, `:689`, `:774`) is a
**compatibility bridge, not a Step R target** — it stays until the public
construction hooks are the only caller, at which point its removal is a separate,
later decision with its own evidence. Naming it here prevents it being counted
as a saving this ADR delivers.

**Exit conditions.** A deletion PR removing the attribute, the re-hydration
method and their tests together; a grep showing no `_model_instance` reference
outside history; and a recorded account of anything retained.

**Status at acceptance (2026-09-25): open; trigger met for adopting views.**
F2 is closed. For views adopting `ModelFormMixin`, the targets are already
unreachable: `_model_instance` on such a view is a configuration error, and
`self.object` is never rebuilt from a stored pk. Tests pin both. The deletion
is scheduled as its own PR, following ADR-027's terminal-delete playbook, and
nothing is deleted at acceptance.

The targets themselves still serve every legacy `FormMixin` edit view, which
is the current release's documented pattern. Deleting them therefore removes
legacy support, and [Compatibility and migration](#compatibility-and-migration)
requires an explicit deprecation window first. The deletion PR lands after
that window. Citations above were refreshed at F1.

## Consequences and non-goals

The public API becomes easier to discover and generate correctly, but the work
is a lifecycle change, not a documentation-only rename. It must retain the stronger
event/reconnect authorization guarantees that ordinary request-only CBVs do not
have to implement. This ADR does not ban ORM values from rendering or automatically
make every public attribute persistable.
