---
title: HTTP API (auto-generated from @event_handler)
layout: doc
---

# HTTP API (auto-generated from `@event_handler`)

djust exposes any handler marked ``@event_handler(expose_api=True)`` as an HTTP
endpoint. The HTTP transport is a **thin adapter** over the existing handler
pipeline: same validation, same permissions, same rate-limit bucket, same
assigns-diff response. One stack, one truth.

This unlocks four caller classes that can't reach a djust app over WebSocket:

- **Mobile / native clients** that don't hold a socket
- **Server-to-server integrations** and CLI scripts
- **Cron jobs** firing one-shot actions
- **AI agents** that consume OpenAPI-described tools

See [ADR-008](../../adr/008-auto-generated-http-api-from-event-handlers.md) for
the design rationale.

---

## Quickstart

### 1. Wire up the URL patterns

```python
# urls.py
from django.urls import include, path

urlpatterns = [
    path("djust/api/", include("djust.api.urls")),
    # ... your routes
]
```

Or, for a helper that matches djust's other URL factories:

```python
from djust.api import api_patterns

urlpatterns = [
    api_patterns(),  # mounts at /djust/api/ by default
    # ...
]
```

### 2. Opt a handler in

```python
from djust import LiveView
from djust.decorators import event_handler, permission_required

class InventoryView(LiveView):
    api_name = "inventory"  # stable slug — see "Slugs" below

    @event_handler(expose_api=True)
    @permission_required("inventory.change_item")
    def update_quantity(self, item_id: int, quantity: int, **kwargs):
        """Update the stock count for an item."""
        item = self.items[item_id]
        item.quantity = quantity
        item.save()
        return {"item_id": item_id, "new_quantity": quantity}
```

That's it. The handler is now reachable at:

```
POST /djust/api/inventory/update_quantity/
Content-Type: application/json

{"item_id": 42, "quantity": 7}
```

### 3. Response shape

On success:

```json
{
  "result": {"item_id": 42, "new_quantity": 7},
  "assigns": {"items": [{"id": 42, "quantity": 7, ...}, ...]}
}
```

`result` is the handler's return value (or `null` if it returned nothing).
`assigns` is a JSON-safe diff of the public view attributes that changed during
the handler run — the same diff the WebSocket transport sends as a VDOM patch.
Clients that cache view state can apply the diff without a full refetch.

On failure the response body is:

```json
{"error": "<kind>", "message": "<human readable>", "details": {...}}
```

| Status | `error` kind | When |
|--------|-------------|------|
| 400 | `invalid_json` | Request body isn't a JSON object |
| 400 | `invalid_params` | Missing or wrong-type parameters. `details` has `expected`, `provided`, `type_errors`. |
| 401 | `unauthenticated` | No auth class accepted the request |
| 401 | `login_required` | View requires login but the user isn't authenticated |
| 403 | `csrf_failed` | Session auth without a valid CSRF token |
| 403 | `permission_denied` | `@permission_required` / `check_view_auth` denied |
| 404 | `unknown_view` | No view registered under that slug |
| 404 | `unknown_handler` | No handler by that name on the view |
| 404 | `handler_not_exposed` | Handler exists but lacks `expose_api=True` |
| 429 | `rate_limited` | `@rate_limit` bucket exhausted |
| 500 | `handler_error` | Handler raised. **The exception message is logged server-side but never included in the response body.** |
| 500 | `serialize_error` | `api_response()` or the handler's `serialize=` raised, or `serialize="name"` pointed at a missing method. |

---

## Transport-conditional returns

A handler serving both transports often has split needs: the WebSocket caller
only wants state mutation (the VDOM diff renders the UI), but the HTTP caller
wants real data in the response. Serializing a query result on every
WebSocket keystroke is wasteful — DB work and JSON overhead the WS path
throws away.

djust gives you three ways to specify the HTTP response, in resolution order:

1. **Per-handler override** — `@event_handler(expose_api=True, serialize=...)`
2. **View-level convention** — `def api_response(self): ...` on the view
3. **Passthrough** — whatever the handler returned

None of them fire on the WebSocket path — zero overhead there.

### Convention — zero per-handler wiring

Define one `api_response()` method on the view. Every
`@event_handler(expose_api=True)` handler falls through to it unless it
sets its own `serialize=` override:

```python
class ClaimListView(LiveView):
    api_name = "claims"

    def api_response(self):
        # Runs only on the HTTP path. The WS path never calls this.
        return [c.as_dict() for c in self._filtered_claims()]

    @event_handler(expose_api=True)
    def search(self, value: str = "", **kwargs):
        self.search_query = value  # state mutation only

    @event_handler(expose_api=True)
    def filter_by_status(self, status: str = "", **kwargs):
        self.status = status

    @event_handler(expose_api=True)
    def clear(self, **kwargs):
        self.search_query = ""
        self.status = ""
```

Three handlers, one serializer, zero per-handler decorator args. Adding a
fourth handler that returns the same shape is one line:
`@event_handler(expose_api=True)`.

`api_response()` may accept one optional positional arg (the handler's return
value) if you want to incorporate it:

```python
def api_response(self, handler_return):
    return {"echo": handler_return, "hits": self._filtered_claims()}
```

### Per-handler override with `serialize=`

When a single handler needs a different response shape, set `serialize=`
to skip the convention for that handler only:

```python
class ClaimListView(LiveView):
    def api_response(self):
        return [c.as_dict() for c in self._filtered_claims()]

    @event_handler(expose_api=True, serialize="serialize_saved_claim")
    def save_claim(self, id: int = 0, **kwargs):
        c = Claim.objects.get(pk=id)
        c.status = "saved"
        c.save()
        return id

    def serialize_saved_claim(self, result):
        # result is whatever save_claim returned (the id)
        return Claim.objects.get(pk=result).as_dict()
```

`serialize=` accepts:

- **A method-name string** — `serialize="serialize_saved_claim"`. Resolved
  against the view at dispatch time. Best for reusable serializers shared
  across handlers. Method arity is flexible: `(self)` or `(self, result)`.
- **A callable** — `serialize=lambda self: [...]` or
  `serialize=lambda self, result: {...}`. Arity is auto-detected: zero-arg
  callables are called as `fn()`, one-arg as `fn(view)`, two-or-more-arg
  as `fn(view, handler_return_value)`.
- **`None`** (default) — falls through to `api_response()` or the handler's
  return value.

Async serializers and async `api_response()` methods are both supported —
the dispatch view awaits them.

### Validation and errors

- Decoration-time: `@event_handler(serialize=...)` without `expose_api=True`
  raises `TypeError` at import time. The serializer only runs on HTTP, so
  setting it without exposing the handler over HTTP is almost certainly a bug.
- Dispatch-time: `serialize="name"` pointing at a missing method → 500
  `serialize_error`.
- Dispatch-time: `api_response()` or the serializer raising → 500
  `serialize_error`. Exception details are logged server-side only, never
  leaked in the response body.

### Sharing `api_response` via mixins

`api_response()` is just a regular Python method, so it resolves through
the normal MRO. Define it once in a mixin and every subclass inherits it:

```python
class ClaimsApiMixin:
    def api_response(self):
        return [c.as_dict() for c in self._filtered_claims()]


class ClaimListView(ClaimsApiMixin, LiveView):
    ...


class ClaimArchiveView(ClaimsApiMixin, LiveView):
    ...
```

### Escape hatch: `self._api_request`

The dispatch view sets `self._api_request = True` on the view instance
**before `mount()` runs** (and therefore also before any event handler), so
`mount` can itself branch on transport if needed. Code paths that need to
branch on transport without using the decorator or convention method can
inspect the flag directly. Prefer `api_response()` or `serialize=` for the
common case — the flag is an escape hatch for advanced scenarios.

> **Note on `@classmethod` / `@staticmethod` serializers.** Instance methods
> are the supported path for `serialize="name"` — arity is detected on the
> bound method. `@staticmethod` works transparently (treated as a plain
> callable). `@classmethod` is an edge case: it binds `cls`, not the
> instance, so it can't read view state directly — pass the underlying
> function explicitly (`serialize=MyView.cls_method.__func__`) if you
> need one.

---

## OpenAPI schema

The auto-generated OpenAPI 3.1 document is served at:

```
GET /djust/api/openapi.json
```

Point any OpenAPI-aware consumer at it — Swagger UI, Redoc, Postman, an LLM
agent, a codegen tool. The schema walks every `@event_handler(expose_api=True)`
handler and maps its type hints:

| Python | OpenAPI |
|--------|---------|
| `int` | `{"type": "integer"}` |
| `float` | `{"type": "number"}` |
| `bool` | `{"type": "boolean"}` |
| `str` | `{"type": "string"}` |
| `UUID` | `{"type": "string", "format": "uuid"}` |
| `Decimal` | `{"type": "string", "format": "decimal"}` |
| `datetime` | `{"type": "string", "format": "date-time"}` |
| `list[T]` | `{"type": "array", "items": <T>}` |
| `Optional[T]` | `<T>` with `"nullable": true` |

A handler's docstring's first line becomes the operation `summary`; the full
docstring becomes the operation `description`.

---

## Authentication

### Default: `SessionAuth`

With no configuration, dispatch uses Django session authentication. A request
must have an authenticated user on the session **and** a valid CSRF token.
Behavior is identical to a standard POST view guarded by
`@login_required` + `@csrf_protect`.

### Custom auth classes

Set `api_auth_classes` on the view to plug in token / header / API-key auth:

```python
import hashlib
import hmac


class TokenAuth:
    """Illustrative header-token auth — NOT production-ready."""

    csrf_exempt = True  # Token auth doesn't need CSRF

    def authenticate(self, request):
        token = request.headers.get("Authorization", "").removeprefix("Bearer ")
        if not token:
            return None
        # Production: store only the sha256 digest of the token, and compare
        # with hmac.compare_digest() to prevent timing attacks. A plain
        # `ApiToken.objects.get(token=token)` compares plaintext via SQL '='
        # and leaks timing information. Look tokens up by a prefix index and
        # verify with constant-time compare.
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        try:
            record = ApiToken.objects.get(token_prefix=digest[:8], active=True)
        except ApiToken.DoesNotExist:
            return None
        if not hmac.compare_digest(record.token_hash, digest):
            return None
        return record.user


class MyView(LiveView):
    api_auth_classes = [TokenAuth, SessionAuth]  # tried in order

    @event_handler(expose_api=True)
    def ping(self, **kwargs):
        return {"ok": True}
```

Auth classes are tried in order; the first one whose `authenticate(request)`
returns a non-`None` user wins. CSRF is enforced only if the winning auth class
has `csrf_exempt = False`.

> **Security note**: djust ships `SessionAuth` in v0.5.1. A first-party token
> auth implementation is tracked under a separate ADR — see the roadmap.

---

## Slugs

The URL slug is derived from the view in this order:

1. Explicit: `api_name = "inventory"` class attribute (recommended — stable)
2. Fallback: `<app_label>.<ClassNameLower>` from the module path

**Always set `api_name` explicitly for any view with an exposed handler.** The
derived slug changes if you move or rename the class; `api_name` is a stable
public contract.

---

## Rate limiting

`@rate_limit(rate=10, burst=5)` applies to both transports with the **same
rate and burst settings**, but each transport maintains its own bucket storage:

- **HTTP** uses a process-level `(caller, handler_name)` token bucket (the
  caller is the authenticated user's PK, or the remote IP when anonymous).
- **WebSocket** uses a per-connection `ConnectionRateLimiter`.

A caller using both transports draws from both buckets independently. If your
handler's rate limit must cap combined HTTP + WS traffic, pick a stricter
`rate=` — or wait for the shared-bucket refactor tracked alongside ADR-008.
The HTTP bucket dict is capped (LRU eviction) to prevent memory exhaustion
from callers rotating identities.

---

## Auditing

`manage.py djust_audit` lists every handler marked `expose_api=True` and flags
any that lack `@permission_required`:

```
--------------------------------------------------
  HTTP API exposed handlers (ADR-008)
--------------------------------------------------
  ✓ POST /djust/api/inventory/update_quantity/  (InventoryView.update_quantity)
  ⚠ POST /djust/api/public/search/  (PublicView.search)

  ⚠  1 exposed handler without @permission_required:
      - PublicView.search (slug: public)
  Review each site. Treat ``expose_api=True`` like @csrf_exempt —
  a public endpoint without explicit permissions is easy to leak.
```

**Treat `expose_api=True` as carefully as `@csrf_exempt`.** Every exposed
handler is a public endpoint. Pair it with `@permission_required`
or a custom auth class that restricts access to the intended callers.

---

## The `mount()` cost

Every HTTP API request instantiates a fresh view and runs `mount()` — the same
entry point the WebSocket consumer uses. If your `mount()` does heavy work
(presence subscribe, database seeding, pubsub setup), that cost is paid on
every API call. Two mitigations:

1. **Keep `mount()` cheap.** Defer heavy work to the handler that needs it.
2. **Override `api_mount(request)` on the view.** If defined, the HTTP
   transport calls it instead of `mount()` — lets you skip WS-specific setup
   for API callers.

```python
class MyView(LiveView):
    def mount(self, request, **kwargs):
        self.subscribe_presence()  # Only needed for live browser sessions
        self._seed()

    def api_mount(self, request):
        self._seed()  # Skip the presence subscription
```

---

## Sub-path deploys (`FORCE_SCRIPT_NAME`, custom prefixes) <small>v0.7.1</small>

If your Django project is mounted under a URL prefix — via
`FORCE_SCRIPT_NAME=/mysite` or by passing a custom prefix to
`api_patterns()` — in-browser callers need to know the new prefix at
runtime. Add the `{% djust_client_config %}` template tag to your
base template's `<head>`:

```django
{% load live_tags %}
<!DOCTYPE html>
<html>
<head>
    {% djust_client_config %}
    <!-- other head content -->
</head>
```

The tag emits `<meta name="djust-api-prefix" content="...">`. The
content is resolved via Django's `reverse()` so it automatically
reflects:

- `FORCE_SCRIPT_NAME` (Django setting — prepends a path segment to
  every generated URL). Asserted by
  `test_tag_emits_meta_under_force_script_name`.
- `api_patterns(prefix="myapi/")` (a custom mount prefix on the
  djust API `include()`). Asserted by
  `test_tag_emits_meta_when_api_mounted_at_custom_prefix`.
- Fallback to `/djust/api/` when the tag is absent. Asserted by
  `test_default_prefix_when_no_meta` on the client side.

**Server-side (outbound) URLs** built with `reverse()` already honor
`FORCE_SCRIPT_NAME` — this section is specifically about the
**browser-side** `djust.call()` client and any other code that
consumes the HTTP API directly. The browser needs to learn the
prefix at runtime because JS has no access to Django's URL config.

An integrator who wants to override the prefix explicitly can set
`window.djust.apiPrefix = '/custom/'` in a script tag that runs
BEFORE `client.js`; that wins over the meta tag
(`test_explicit_global_override_wins`).

---

## What's out of scope (today)

Per [ADR-008](../../adr/008-auto-generated-http-api-from-event-handlers.md) §"Out of scope":

- **Streaming responses** (HTTP/2 SSE) — deferred to a later release.
- **GraphQL / gRPC transports** — REST/JSON is the pragmatic default.
- **Batched multi-call requests** — one handler per request.
- **Per-handler URL customization** (`@event_handler(api_path="/custom/url")`).
- **First-party token auth** — covered by a follow-up ADR.
- **Built-in Swagger UI** — the OpenAPI JSON is enough; users can point any UI at it.

See also:

- [ADR-008 full design rationale](../../adr/008-auto-generated-http-api-from-event-handlers.md)
- [Decorators reference](../../STATE_MANAGEMENT_API.md)
- Manifesto principle #4 — One stack, one truth
