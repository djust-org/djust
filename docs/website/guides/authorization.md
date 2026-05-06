# Authorization

djust ships with a four-layer authorization onion. This guide walks through each layer, with focus on the v0.9.5 object-level lifecycle that closes a structural class of IDOR vulnerabilities for detail views.

## TL;DR

For any LiveView bound to a single object via URL kwarg (`/documents/<int:document_id>/`):

1. Set `permission_required = "app.access"` for the role check (layer 2).
2. Override `get_object()` to return the object.
3. Override `has_object_permission(self, request, obj)` to express access.

The framework runs your check at mount AND on every event handler dispatch automatically. Mount-time denial closes the WS with code 4403; per-event denial sends an error frame and keeps the WS open.

```python
from djust import LiveView
from djust.decorators import event_handler

class DocumentDetailView(LiveView):
    permission_required = "documents.access"  # role check (layer 2)

    def mount(self, request, document_id=None, **kwargs):
        self.document_id = document_id

    def get_object(self):
        return Document.objects.get(pk=self.document_id)

    def has_object_permission(self, request, obj):
        return obj.owner_id == request.user.id

    @event_handler()
    def add_comment(self, body=""):
        # has_object_permission already ran for this event. Reuse the
        # cached object rather than re-querying:
        Comment.objects.create(document=self._object, body=body)
```

## The four-layer onion

djust calls the layers in order. The first denial wins; subsequent layers don't run.

| Layer | What | When |
|---|---|---|
| 1. `login_required` | Is user authenticated? | At WS connect |
| 2. `permission_required` | Does user have Django role permission? | At WS connect |
| 3. `check_permissions(request)` | Custom hook for arbitrary logic (not object-aware) | At WS connect |
| 4. `has_object_permission(request, obj)` | Per-object access (NEW in v0.9.5) | At mount AND every event |

Layers 1-3 run **before** `mount()` (in `check_view_auth`). Layer 4 runs **after** `mount()` because `get_object()` typically reads a URL kwarg the user populates inside `mount()` (e.g., `self.document_id = document_id`).

## Why per-event matters

Without per-event enforcement, a session established with valid mount-time access can be exploited if access is revoked mid-session:

- User A has access to claim 99 at mount time → mount succeeds, WS stays open.
- An admin revokes User A's access to claim 99.
- User A's WS is still open. They send `add_comment` over the existing connection.
- Without per-event re-execution, the event runs because it doesn't re-check the live access state.

The v0.9.5 lifecycle (specifically v0.9.5-1b) re-runs `has_object_permission` on every event handler dispatch, so the revocation takes effect immediately.

## OWASP IDOR mitigation built in

When `get_object()` returns `None` OR raises `ObjectDoesNotExist` (parent of every `Model.DoesNotExist`) OR raises `Http404` (raised by `get_object_or_404`), the framework treats the object as absent and skips `has_object_permission`. The caller (mount or event handler) sees no object, which lets you render a 404 page or send a 404-shape error frame.

This avoids the existence leak that returning a 403 on `Model.DoesNotExist` would imply. **You don't need to manually catch `DoesNotExist`** — the framework does it for you:

```python
def get_object(self):
    return Document.objects.get(pk=self.document_id)
    # Raises DoesNotExist for missing rows. Framework catches it and
    # treats as None → no permission check runs → 404-shape response.
```

If you want to be explicit, return `None` directly:

```python
def get_object(self):
    try:
        return Document.objects.get(pk=self.document_id)
    except Document.DoesNotExist:
        return None
```

Both flows produce the same external behavior.

## The cache: `self._object`

After a successful `has_object_permission` check, the framework caches the result of `get_object()` as `self._object`. Reuse it from event handlers and `get_context_data` rather than re-querying:

```python
@event_handler()
def add_comment(self, body=""):
    # Don't re-fetch; use the cached, permission-verified object.
    Comment.objects.create(document=self._object, body=body)
```

`self._object` is a framework slot allocated in `LiveView.__init__` BEFORE the `_framework_attrs` snapshot, so it's NOT serialized into msgpack user-private state. After WS reconnect / state-restore, `self._object` is `None` and `get_object()` runs fresh — handles the "object reassigned while user was disconnected" case automatically.

## Cache invalidation

If a handler mutates ownership-determining state (e.g., reassigning the FK that determines access), call `self._invalidate_object_cache()` so the next event re-fetches:

```python
@event_handler()
def reassign_owner(self, owner_id: int = 0):
    self._object.owner_id = owner_id
    self._object.save()
    self._invalidate_object_cache()  # next event re-runs get_object()
```

Without this, a cached `self._object` would let the formerly-authorized user retain access until the WS reconnects.

Note: `_invalidate_object_cache()` only affects FUTURE events. The render that includes the mutation (the response that ships immediately after the handler returns) still sees the OLD `self._object` because the mutation happened mid-handler. If you need the next render to reflect the new ownership, set `self._object = self._object` after `_invalidate_object_cache()` to force a fresh fetch — or just refresh the FK directly without invalidation.

## Wire-protocol error frames

| Path | Wire shape | Effect |
|---|---|---|
| Mount-time denial | WS close code 4403 + `{"type": "error", "error": "Permission denied"}` | Browser drops the connection; client treats as full reload |
| Per-event denial | `{"type": "error", "error": "Access denied for this object.", "code": "permission_denied"}` | WS stays open; client can revert optimistic UI updates and let the user navigate elsewhere |

Use the structured `code` field on the per-event frame to distinguish permission denial from other error types in your client-side handlers.

## Defense in depth: manager-level filtering

A custom `for_user()` queryset method is a complementary pattern. Use it alongside the lifecycle hooks for layered defense:

```python
class DocumentManager(models.Manager):
    def for_user(self, user):
        if user.is_superuser:
            return self.all()
        return self.filter(owner=user)

class Document(models.Model):
    objects = DocumentManager()
    owner = models.ForeignKey(User, ...)

class DocumentDetailView(LiveView):
    permission_required = "documents.access"

    def mount(self, request, document_id=None, **kwargs):
        self.document_id = document_id

    def get_object(self):
        # If user doesn't own it: DoesNotExist → framework treats as
        # 404-shape → no existence leak.
        return Document.objects.for_user(self.request.user).get(pk=self.document_id)

    def has_object_permission(self, request, obj):
        # Belt-and-suspenders: even if get_object returned the obj,
        # explicitly verify access here. Defends against bugs in the
        # manager's filter.
        return obj.owner_id == request.user.id
```

The manager filter and `has_object_permission` are independent; either alone would be sufficient, but together they catch each other's bugs.

## Migration: hand-rolled IDOR checks → lifecycle hooks

Many existing detail views check object access in `get_context_data`. This is the bug class the v0.9.5 lifecycle closes — `get_context_data` runs during render, AFTER `mount()` has set up the WS session. By the time you raise `PermissionDenied`, the session is established and event handlers can fire against the foreign object.

```python
# BEFORE (vulnerable):
class DocumentDetailView(LiveView):
    permission_required = "documents.access"

    def mount(self, request, document_id=None, **kwargs):
        self.document_id = document_id

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        doc = Document.objects.get(pk=self.document_id)
        if not can_access_document(self.request.user, doc):
            raise PermissionDenied()  # Too late — mount already happened
        ctx["document"] = doc
        return ctx

    @event_handler()
    def add_comment(self, body=""):
        # NO PER-EVENT CHECK — vulnerable to mid-session access revocation
        # AND to a user crafting events on a session for an object they
        # never had legitimate access to (mount-time render error doesn't
        # close the WS).
        doc = Document.objects.get(pk=self.document_id)
        Comment.objects.create(document=doc, body=body)
```

```python
# AFTER (per ADR-017):
class DocumentDetailView(LiveView):
    permission_required = "documents.access"

    def mount(self, request, document_id=None, **kwargs):
        self.document_id = document_id

    def get_object(self):
        return Document.objects.get(pk=self.document_id)

    def has_object_permission(self, request, obj):
        return can_access_document(request.user, obj)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["document"] = self._object  # cached by the framework
        return ctx

    @event_handler()
    def add_comment(self, body=""):
        # has_object_permission re-ran for THIS event. Safe to use
        # self._object directly.
        Comment.objects.create(document=self._object, body=body)
```

Run `python manage.py djust_audit --ast` to find views matching the IDOR shape in your codebase. The X008 check flags them with a pointer back to this guide.

## Falsy non-None return values

`get_object()` uses strict-identity comparison: only `None` is treated as "no object". Falsy non-None values (`False`, `0`, `""`, `[]`) ARE valid objects and `has_object_permission` IS called for them. This is rarely useful in practice, but if you have an unusual access model where the "object" is a sentinel like `False`, the framework respects it.

## Custom `check_permissions` (layer 3) interaction

If you override `check_permissions(request)`, it runs at mount BEFORE `has_object_permission`. Use this for non-object-aware logic (e.g., "is the user banned?", "is the system in maintenance mode?"). For per-object logic, use `has_object_permission` — it has access to the object AND it's re-run on every event.

```python
class DocumentDetailView(LiveView):
    permission_required = "documents.access"

    def check_permissions(self, request):
        # Layer 3: arbitrary logic. Cheap deny — short-circuits before
        # we even fetch the object.
        if request.user.is_banned:
            return False
        return True

    def get_object(self):
        return Document.objects.get(pk=self.document_id)

    def has_object_permission(self, request, obj):
        # Layer 4: per-object. Runs only after check_permissions passes.
        return obj.owner_id == request.user.id
```

## Performance

The lifecycle is opt-in: views that don't override `get_object()` see ZERO overhead — `_has_custom_get_object()` short-circuits before any work. For overriding views:

- **Mount**: one `get_object()` call (your typical FK lookup) + one `has_object_permission()` call.
- **Per event**: one cached attribute read (`self._object`) + one `has_object_permission()` call. NO extra DB query when the cache is warm.
- **After `_invalidate_object_cache()`**: next event re-runs `get_object()` (one DB query) + `has_object_permission()`.

Keep `get_object()` minimal — just the FK lookup. Expensive I/O in this method becomes per-mount overhead.

## Reference

- ADR-017 (full design): `docs/adr/017-object-permission-lifecycle.md`
- Foundation: PR #1374, commit `c3498e62` (v0.9.5-1a)
- Per-event re-execution: PR #1378, commit `a534e77d` (v0.9.5-1b)
- System check + this guide: v0.9.5-1c
- Tracking issue: [#1373](https://github.com/djust-org/djust/issues/1373)
