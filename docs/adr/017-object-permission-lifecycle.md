# ADR-017: Object-Level Authorization Lifecycle (`get_object` + `has_object_permission`)

**Status**: Proposed
**Date**: 2026-05-06
**Deciders**: Project maintainers
**Target version**: v0.9.5 (P0, security)
**Related**:
- [`python/djust/auth/core.py:check_view_auth`](../../python/djust/auth/core.py) — current view-level auth surface
- [`python/djust/websocket.py`](../../python/djust/websocket.py) — `handle_mount` (line ~1925), `handle_event` (line 2606), connect-path `PermissionDenied` catch (line 1948)
- [`python/djust/audit_ast.py`](../../python/djust/audit_ast.py) — static-analysis hook for the system check
- Issue [#1373](https://github.com/djust-org/djust/issues/1373) (this ADR's tracking issue)
- ROADMAP.md milestone v0.9.5-1
- DRF `get_object()` + `has_object_permission()` (the prior art mirrored here)
- Phoenix LiveView `on_mount` callbacks (alternative model considered, rejected — see Decision 2)
- v0.8.6 retrospective ([#1122](https://github.com/djust-org/djust/issues/1122)) — split-foundation pattern (this milestone applies it)

---

## Summary

djust currently has no first-class hook for **object-level** authorization. The two existing surfaces (`permission_required` class attribute, `check_permissions(self, request)` hook) handle role-level checks but cannot enforce per-object access for views bound to a single object via URL kwarg. The natural placement (`get_context_data`) runs too late: by the time the check fails, `mount()` has set up the WS-session-scoped state and event handlers can fire. Concrete IDOR reproducer: a user authenticated with `claims.access_claim` but unassigned to claim 99 can navigate to `/claims/99/`, fail the render-time check, and still fire write event handlers against claim 99 over the established WS.

This ADR proposes a DRF-style lifecycle: `get_object()` declares how to fetch the view's primary object; `has_object_permission(request, obj)` declares whether the user can access it. djust calls both at mount AND on every event handler entry, so the bug class is structurally eliminated, not just documented away.

## Context

### What's vulnerable today

```python
# Reproducer — Examiner A authenticated, has claims.access_claim,
# NOT assigned to claim 99.
class ClaimDetailView(LiveView):
    permission_required = "claims.access_claim"  # role check at WS connect

    def mount(self, request, claim_id=None, **kwargs):
        self.claim_id = claim_id  # bound from URL kwarg

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        claim = Claim.objects.get(pk=self.claim_id)
        if not can_view_claim(self.request.user, claim):
            raise PermissionDenied()  # <-- runs during render, AFTER mount
        ctx["claim"] = claim

    @event_handler()
    def add_claim_note(self, subject="", body="", **kwargs):
        claim = Claim.objects.get(pk=self.claim_id)  # <-- NO PER-EVENT CHECK
        ClaimNote.objects.create(claim=claim, subject=subject, body=body)
```

1. WS connect to `/claims/99/`. `check_view_auth` runs `permission_required` (role OK), passes.
2. `handle_mount` runs `mount(request, claim_id=99)`. `self.claim_id = 99`.
3. First render calls `get_context_data`, which raises `PermissionDenied`.
4. `websocket.py` only catches `PermissionDenied` in the **connect** path (line 1948). After mount, the exception propagates as an unhandled error frame; the WS session is **not** closed, `self.claim_id` remains 99.
5. Examiner A sends `{"event": "add_claim_note", "params": {...}}`. `handle_event` (line 2606) dispatches to the handler. The handler calls `Claim.objects.get(pk=99)` and writes a note. **No access check.**

### Why existing hooks don't fit

- `permission_required` is a role check. It can't see which object the user is asking about.
- `check_permissions(self, request)` runs **before** `mount()` (`auth/core.py:94`), so `self.claim_id` doesn't exist yet. URL kwargs are bound by `mount()`, not before.
- `get_context_data` runs **during render**, after `mount()` has committed session-scoped state. `PermissionDenied` from there is not caught in the per-event path.

There's no hook that fires (a) after URL kwargs are bound to `self`, (b) before mount commits, AND (c) re-runs on every event. DRF solved the equivalent split with `get_object()` + `has_object_permission()` — the framework owns the call site, so developers cannot forget the per-event check.

### Why now

Surfaced 2026-05-06 reviewing NYC Claims PR #268. The reproducer above is exploitable in the current production v0.9.4. Affects every djust LiveView whose URL contains a primary-object identifier (`/<thing>/<id>/`) — the most common detail-view pattern. Treating this as documentation ("remember to call your check inside event handlers") would let the bug class persist; the framework should own the call site.

## Decision

### Decision 1: API shape — DRF mirror, two methods

Add two methods to `LiveView`:

```python
class LiveView:
    def get_object(self):
        """Return the view's primary object, or None if not applicable.

        Override in subclasses bound to a single object via URL kwarg.
        Default returns None; views that return None skip
        `has_object_permission` (no behavior change for existing apps).

        Called once by djust per mount, after URL kwargs are bound to self.
        Cached as self._object until the view is unmounted. Override
        get_object() AND call self._invalidate_object_cache() if a handler
        mutates state that affects access (e.g., reassigning the FK that
        determines ownership).
        """
        return None

    def has_object_permission(self, request, obj):
        """Return True if the request user may access obj.

        Called by djust at mount AND before each event handler entry.
        Override to express object-level auth. Default returns True
        (no-op for views that don't override get_object).

        Raise PermissionDenied for an explicit denial with a message;
        return False for a silent denial. Both close the WS at mount
        time and return a permission-error frame at event time.
        """
        return True
```

**Why a method, not a classvar / decorator?** The check needs runtime access to `request.user`, `obj`, and view state. A classvar can only express a static permission name; a decorator on individual handlers would put the burden back on the developer to remember to apply it. The pair-of-methods shape mirrors DRF and Django CBVs (which use `get_object()` plus `UserPassesTestMixin` / `PermissionRequiredMixin`).

**Why not just `check_permissions` extended?** `check_permissions` runs before `mount()` and has no `obj` parameter. Extending it to accept `obj` and run twice (pre-mount with no obj, post-mount with obj) creates two call shapes for one method — confusing. The pair-of-methods stays clean.

### Decision 2: Signature — `get_object(self)`, no request

Pinned to the no-argument shape. The request is already available as `self.request` (set by djust's `RequestMixin` at mount). Adding a `request` parameter creates two ways to access the same value and lets subclasses introduce subtle bugs by reading `request` from the parameter while the rest of the codebase reads from `self.request`.

`has_object_permission(request, obj)` DOES take `request` explicitly — it mirrors DRF and is invoked from contexts where the request is the load-bearing variable (it's what the check is *about*). The asymmetry is intentional and matches DRF.

### Decision 3: Cache lifetime + invalidation

`self._object` is populated by `get_object()` once at mount, after URL kwargs are bound. It persists for the WS session lifetime — `handle_event` reuses the cached value rather than re-fetching every event. The framework provides `self._invalidate_object_cache()` for handlers that mutate state affecting access:

```python
@event_handler()
def reassign_claim(self, examiner_id: int = 0, **kwargs):
    self.claim.assigned_examiner_id = examiner_id
    self.claim.save()
    self._invalidate_object_cache()  # next event re-fetches via get_object()
```

**Why cache?** Per-event re-fetch is a query per event. For most views the assigned-examiner FK doesn't change mid-session; caching is the right default. Invalidation is explicit so the cost model is predictable.

**WS reconnect / state-restore**: cache is invalidated on every state-restore (the post-reconnect path). `get_object()` runs fresh because `self._object` is reset by the restore handler. This handles the "claim was reassigned while user was disconnected" case automatically.

**State mutation that doesn't go through a handler** (signal-driven, push-from-server): out of scope. Apps that need it must call `self._invalidate_object_cache()` explicitly from the push handler.

### Decision 4: Per-event failure response

When `has_object_permission` returns False (or raises `PermissionDenied`) on event entry, djust:

1. Logs a `WARNING`-level access-denied entry with view class, handler name, user pk, object pk.
2. Sends a permission-error frame to the client: `{"type": "error", "code": "permission_denied", "message": "..."}`.
3. Does NOT close the WS. The session remains valid; the user just can't perform that handler against that object.
4. The handler body is NOT executed.

**Why not close the WS?** The user is authenticated and has the role; only this specific object is forbidden. Closing the WS forces a full reload, which is wrong UX for "you can't reassign this claim" — they should be able to navigate elsewhere.

**Why not silent drop?** The client needs to know the action failed so it can revert optimistic UI updates. The error frame is the load-bearing signal.

**Mount-time** failure remains a WS close (matches existing `check_view_auth` behavior at line 1954: `await self.close(code=4403)`). Mount-time means the user shouldn't have access to the view at all.

### Decision 5: Order of auth checks

`check_view_auth` runs in this order (extended from current):

1. `login_required` — is user authenticated?
2. `permission_required` — does user have Django role permission(s)?
3. `check_permissions(self, request)` — custom hook (existing).
4. **NEW**: if `get_object()` returns non-None, call `has_object_permission(request, obj)`. If it returns False or raises `PermissionDenied`, deny.

The new step is **after** `check_permissions` because:

- `check_permissions` is the developer's escape hatch for arbitrary logic — they may want to short-circuit before object lookup (e.g., "is this user banned?").
- Object-level auth requires the object to be loaded, which is a query. Running role + custom checks first lets cheap denials short-circuit before the query.
- Subclasses that want object-aware logic in `check_permissions` can call `self.get_object()` explicitly; the cache prevents double-fetch.

### Decision 6: Views without a primary object — opt-in via `get_object` override

Views that don't have a single primary object (`ExaminerDashboardView`, `ClaimAssignmentView`, list views, search views) inherit the default `get_object() -> None` and `has_object_permission(request, None) -> True`. The new lifecycle is invisible to them — zero behavior change.

To verify this empirically, the regression suite includes a "negative case" test that runs the existing demo view test suite unchanged and asserts no failures. Same for djust.org's test suite (run as a downstream-consumer integration check before merge).

### Decision 7: Per-event re-execution mechanism

`handle_event` (`websocket.py:2606`) gains a pre-dispatch hook:

```python
async def handle_event(self, data):
    # ... existing prelude (event_name extraction, etc.) ...

    # NEW: object-permission re-check (only if get_object is overridden)
    if self.view_instance._has_custom_get_object():
        try:
            obj = await sync_to_async(self.view_instance.get_object)()
            if obj is not None:
                ok = await sync_to_async(
                    self.view_instance.has_object_permission
                )(request, obj)
                if not ok:
                    await self.send_json({
                        "type": "error",
                        "code": "permission_denied",
                        "message": "Access denied for this object.",
                    })
                    return
        except PermissionDenied as exc:
            await self.send_json({
                "type": "error",
                "code": "permission_denied",
                "message": str(exc) or "Access denied.",
            })
            return

    # ... existing handler dispatch ...
```

`_has_custom_get_object()` mirrors the existing `_has_custom_check_permissions()` helper (`auth/core.py:114`) — checks the MRO for a class that overrides `get_object` between the subclass and `LiveView`. This is the gate that makes the lifecycle opt-in: views that don't override `get_object` skip the per-event check entirely (zero overhead).

### Decision 8: System check (`audit_ast.py`)

Static heuristic in `audit_ast.py` flags this exact shape:

- View has `permission_required` set (the IDOR-vulnerable shape always has a role check)
- View's `mount()` assigns from URL kwarg (`self.<x>_id = <x>_id` pattern)
- View has at least one `@event_handler`-decorated method that reads `self.<x>_id`
- View does NOT override `has_object_permission` or `check_permissions`

When all four conditions match, emit a check warning with category `S` (security): "View matches the IDOR shape (URL-bound object + write handlers + no object-permission hook). See `docs/website/guides/authorization.md` for the migration."

**Why heuristic, not strict?** Some apps will legitimately handle object-level auth in `check_permissions` (existing pattern). The heuristic accepts that as opt-out. False positives are tolerable; the warning is informational, not blocking.

## Iteration plan (split-foundation)

Per Action #1122 / #1175, this milestone has high blast radius and warrants split-foundation rollout. **Three iterations**:

### v0.9.5-1a — Foundation (mount-time enforcement)

- Add `get_object()` and `has_object_permission()` methods to `LiveView`.
- Extend `check_view_auth` to call the pair when `get_object` is overridden.
- Add `_has_custom_get_object()` helper in `auth/core.py`.
- Add `self._invalidate_object_cache()` API.
- Regression suite Part 1: mount-time tests (denial closes WS, allow proceeds, cache populated, invalidation works, no-override is no-op).

**Soak this iteration through one release** before stacking per-event work on top. The mount-time semantics are the foundation everything else depends on; getting them wrong is expensive to unwind.

### v0.9.5-1b — Per-event enforcement

- Extend `handle_event` (`websocket.py:2606`) with the pre-dispatch object check.
- Permission-error frame protocol.
- State-restore path invalidates the object cache.
- Regression suite Part 2: per-event tests (cached check, post-mutation re-check via `_invalidate_object_cache`, handler-time denial returns error frame, session stays open after denial).

Lands on top of -1a's foundation.

### v0.9.5-1c — Tooling + docs

- `audit_ast.py` system check.
- New guide `docs/website/guides/authorization.md`.
- `djust-dev` skill principle entry.
- ROADMAP and CHANGELOG entries pointing apps at the new lifecycle.

Documentation-grade work that rides on the now-stable lifecycle.

## Consequences

### Pros

- **Bug class structurally eliminated.** Apps that override `get_object` get per-event enforcement automatically — developers cannot forget.
- **Backwards compatible.** Apps that don't override see no behavior change, no overhead, no migration required. Verified by running existing test suites unchanged.
- **DRF parity.** Familiar shape for developers coming from REST. Lowers the learning curve.
- **Composes with existing hooks.** `permission_required`, `check_permissions`, and `has_object_permission` form a clean four-layer onion (login → role → custom → object).
- **Defense in depth.** System check (`djust check`) catches existing apps that haven't migrated yet.

### Cons

- **New public API surface.** Permanent. We get one shot at the signatures.
- **Cost model relies on developer discipline.** `_invalidate_object_cache()` must be called when state changes affecting access. Forgetting it means stale cache → false negatives.
- **Per-event check has a cost.** ~1 attribute read per event for cached path, ~1 query per event for fresh-fetch path. Acceptable for non-pathological views; could surface in benchmarks.
- **Doesn't help apps that fetch the object in every handler ignoring `self._object`.** They have to refactor to use the cache (or accept the duplicate query). The system check warns about this shape.

### Risks

- **Subclass override of `get_object()` that does expensive I/O.** A naive override that joins half the database makes every event handler eat that cost. Mitigation: documentation emphasizes minimal `get_object()` (just the FK). The cache helps once it's warm.
- **Cache invalidation bugs.** A handler mutates the assigned-examiner FK but forgets to call `_invalidate_object_cache()`. Next event sees stale `self._object`, the (formerly authorized) user retains access. Mitigation: regression test that exercises the mutation-without-invalidate path and verifies the cache eventually updates on state-restore. Plus a `djust check` heuristic for FK mutations on `self._object`.
- **State-restore path interaction.** djust's reconnect / state-restore deserializes view state from msgpack. `self._object` is a Django model instance — not directly msgpack-serializable. The restore path skips serializing `_object` and re-runs `get_object()` after restore, which is the correct behavior but worth pinning explicitly in the test suite.
- **`PermissionDenied` raised by `get_object()` itself** (e.g., `Claim.DoesNotExist` raised as `PermissionDenied` to avoid object-existence leak). djust's mount-time path catches this; the per-event path catches it too. Documented as the recommended pattern for "deny without confirming existence" (OWASP IDOR mitigation: prefer 404-shape over 403-shape on enumeration).

## Alternatives considered

### Alt 1: Decorator per event handler (`@object_permission_required(...)`)

Apply per-handler:

```python
@event_handler()
@object_permission_required(can_view_claim)
def add_claim_note(self, ...): ...
```

**Rejected.** Same forgotten-check failure mode as today — developer has to remember to apply it on every handler. The whole point of the framework owning the call site is that the developer can't forget.

Could ship this as a **secondary** API for views that don't fit the `get_object()` shape (e.g., handlers that operate on a different object than the view's primary). Worth filing as a follow-up issue but not part of the foundation.

### Alt 2: Phoenix `on_mount` callbacks

Phoenix LiveView exposes a chain of `on_mount` callbacks that run before mount and can halt with `:halt`. They're a generalization of `check_permissions`.

**Rejected as the primary mechanism.** They have the same temporal problem as today's `check_permissions` — they run once at mount, not per-event. Phoenix's apps avoid the IDOR by not using URL-scoped state in a stateful socket the same way djust does (Phoenix's LiveView state is closer to React's component state than djust's session-scoped instance attrs). The DRF model fits djust's shape better.

### Alt 3: Bake object-level auth into the queryset manager (`for_user()`)

Encode all access logic in custom managers; require views to use them.

**Rejected as the primary mechanism, but adopted as a secondary defense.** Manager-level filtering is the right pattern for *queries* (it auto-filters list views, it works in `Claim.objects.get(pk=x).for_user(user)`), but it doesn't help when an event handler does `Claim.objects.get(pk=self.claim_id)` raw and forgets to call `for_user`. The framework still has to own the per-event call site. The guide will recommend BOTH the lifecycle hooks AND the manager pattern as defense-in-depth.

### Alt 4: Make object-level auth automatic via URL routing

Inspect URL kwargs (`<int:claim_id>`), auto-fetch the model from the kwarg name, auto-filter via the manager.

**Rejected.** Too magical. Breaks if the URL kwarg name doesn't match the model name, if the view operates on multiple objects, if the model has a custom manager, or if the object isn't a Django model (UUID, slug, virtual object). The opt-in `get_object()` override is the right amount of explicitness — developer states what they're doing, framework enforces it.

## Migration plan for existing apps

Pre-existing views with hand-rolled object-level checks (like NYC Claims `ClaimDetailView.get_context_data` calling `can_view_claim`) migrate by:

1. Override `get_object()` to return the primary object.
2. Override `has_object_permission(request, obj)` and call the existing helper (`return can_view_claim(request.user, obj)`).
3. Delete the in-`get_context_data` IDOR check (it's redundant; the framework now owns it).
4. Run `djust check` — should report 0 IDOR-shape warnings for the migrated view.

The guide includes this as a concrete worked example. The migration is incremental — apps can migrate views one at a time without breaking unmigrated ones.

## Acceptance

- [ ] `LiveView.get_object()` and `has_object_permission()` documented and exported from `djust.live_view`.
- [ ] `check_view_auth` calls `has_object_permission` after `check_permissions` when `get_object` is overridden (v0.9.5-1a).
- [ ] `handle_event` re-runs `has_object_permission` before dispatching (v0.9.5-1b).
- [ ] `_invalidate_object_cache()` documented and tested (v0.9.5-1a).
- [ ] State-restore path invalidates the cache (v0.9.5-1b).
- [ ] `djust check` warns on the IDOR shape (v0.9.5-1c).
- [ ] `docs/website/guides/authorization.md` published with migration example (v0.9.5-1c).
- [ ] `djust-dev` skill principle catalog includes the new entry (v0.9.5-1c).
- [ ] Regression suite covers all six cases (mount denial, mount allow, per-event denial, per-event allow, cache invalidation, no-override no-op).
- [ ] No behavior change for views that don't override `get_object()` — verified by running the existing demo + djust.org test suites unchanged.
- [ ] Reproducer from this ADR's "What's vulnerable today" section is no longer exploitable on the post-foundation iteration.

## References

- Issue [#1373](https://github.com/djust-org/djust/issues/1373) — tracking issue
- ROADMAP.md milestone v0.9.5-1
- DRF docs: [Permissions — `has_object_permission`](https://www.django-rest-framework.org/api-guide/permissions/#object-level-permissions)
- Django CBV: [`SingleObjectMixin.get_object()`](https://docs.djangoproject.com/en/5.0/ref/class-based-views/mixins-single-object/#django.views.generic.detail.SingleObjectMixin.get_object)
- OWASP A01:2021 (Broken Access Control) — IDOR mitigation guidance
- v0.8.6 retro / Action #1122 — split-foundation pattern
- v0.9.1 retro / Action #1175 — CSP-strict defaults (sister rule for "framework owns the call site")
