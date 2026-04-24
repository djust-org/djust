---
title: "Server Functions (`@server_function` / `djust.call()`)"
slug: server-functions
section: guides
order: 12.7
level: advanced
description: "Same-origin browser RPC to Python — no re-render, no OpenAPI, minimal envelope"
---

# Server Functions (`@server_function` / `djust.call()`)

**New in v0.7.0.** Call a Python method on a LiveView from client JS and
get the return value back as JSON — without triggering a VDOM re-render.
This is pure RPC: the view isn't diffed, no assigns are pushed, and no
OpenAPI schema is generated. It's the fastest path when the page needs
server data but doesn't want the UI to flicker.

Server functions sit alongside two related primitives — pick the one
that matches your intent:

| Decorator                        | Transport      | Re-render | External callers | Primary use                                     |
| -------------------------------- | -------------- | --------- | ---------------- | ----------------------------------------------- |
| `@event_handler`                 | WebSocket      | Yes       | No               | UI interactions — click, submit, input          |
| `@event_handler(expose_api=True)` | HTTP (ADR-008) | Yes       | Yes (OpenAPI)    | Mobile apps, S2S integrations, AI-agent tools   |
| `@server_function`               | HTTP (v0.7.0)  | **No**    | No               | Typeahead, validation, data fetch w/ no re-render |

---

## Quick start

```python
# views.py
from djust import LiveView
from djust.decorators import server_function

class ProductView(LiveView):
    template_name = "product_search.html"
    api_name = "catalog.product"

    @server_function
    def search(self, q: str = "", **kwargs) -> list[dict]:
        hits = Product.objects.filter(name__icontains=q)[:10]
        return [{"id": p.id, "name": p.name, "price": str(p.price)} for p in hits]
```

```html
<!-- product_search.html -->
<input id="q" type="text" placeholder="Search products…">
<ul id="results"></ul>

<script>
    const q = document.getElementById('q');
    const ul = document.getElementById('results');
    q.addEventListener('input', async (e) => {
        try {
            const hits = await djust.call(
                'catalog.product',       // view slug
                'search',                 // function name
                { q: e.target.value },    // params
            );
            ul.innerHTML = hits.map(h =>
                `<li>${h.name} — $${h.price}</li>`).join('');
        } catch (err) {
            console.error(err.code, err.message);
        }
    });
</script>
```

The user types, the server returns hits, the DOM updates — all without
a WebSocket frame or a VDOM diff. The input loses focus? Never; djust
never touches the input's element.

---

## API reference

### Python — `@server_function`

```python
from djust.decorators import server_function

@server_function
def my_fn(self, arg1: int = 0, **kwargs) -> dict:
    ...
```

- **No arguments today.** (`@server_function` and `@server_function()`
  are both accepted for consistency with Python decorator conventions.)
- The method gets a `_djust_decorators["server_function"]` metadata
  entry — this is what the dispatcher looks up.
- **Dual-decoration raises `TypeError` at import time.** Stacking
  `@event_handler` + `@server_function` on the same method is rejected
  — a function either re-renders the view or returns an RPC result,
  never both.
- **Stacking with `@permission_required`:** put `@server_function`
  OUTERMOST (topmost). Otherwise the metadata is attached to the inner
  wrapper and the dispatcher cannot see it.
- **Both sync and `async def` are supported** via the same
  `_call_possibly_async` helper used by `@event_handler`.

### Client — `djust.call()`

```js
const result = await djust.call(viewSlug, fnName, params = {});
```

- Returns a `Promise` that resolves to `data.result` on 2xx.
- Rejects with an `Error` on non-2xx whose `.message` is the server's
  `error.message`, with additional attached fields:
  - `err.code` — the server-returned `error` kind (see codes below)
  - `err.status` — the HTTP status
  - `err.details` — optional object, populated on `invalid_params`
- CSRF: reads the hidden `[name=csrfmiddlewaretoken]` input first,
  then falls back to the `csrftoken` cookie.
- Sends `Content-Type: application/json`, `X-CSRFToken: <token>`,
  `X-Requested-With: XMLHttpRequest`, `credentials: same-origin`.

### Endpoint

```
POST /djust/api/call/<view_slug>/<function_name>/
```

CSRF is required unconditionally — there is no auth-class opt-out.
Server functions are intended for same-origin, session-cookie-only
in-browser calls; if you need external callers, use
`@event_handler(expose_api=True)` instead.

### Request body

The body **must** be one of the following exact shapes:

- An **empty body** (no bytes at all).
- A **JSON object with no keys:** `{}`.
- A **JSON object with exactly one key** `"params"` whose value is a
  JSON object: `{"params": {"q": "chair"}}`.

Any other shape — a flat object like `{"q": "chair"}`, or a wrapped
object with sibling keys like `{"params": {...}, "extra": 1}` — returns
`400 invalid_body`. This strict shape is deliberate: otherwise a user
whose own field happened to be named `params` would see every sibling
key silently dropped. The JS client helper always sends the wrapped
form, so this only matters if you're calling the endpoint with `curl`
or from a non-djust client.

### Response

Success (2xx):

```json
{"result": <any-json-serializable-value>}
```

Error (4xx / 5xx):

```json
{"error": "<code>", "message": "<human-readable>", "details": {...}}
```

`details` is present only for some codes (notably `invalid_params`,
which echoes `expected` / `provided` / `type_errors`).

### Error codes

| Code                     | Status | When it fires                                                         |
| ------------------------ | ------ | --------------------------------------------------------------------- |
| `unknown_view`           | 404    | `view_slug` doesn't match any registered `api_name`                   |
| `unknown_function`       | 404    | Method `function_name` doesn't exist on the view                      |
| `not_a_server_function`  | 404    | Method exists but wasn't decorated with `@server_function`            |
| `unauthenticated`        | 401    | `request.user` is anonymous (session cookie missing / expired)        |
| `login_required`         | 401    | View-level auth (`login_required` / view `@permission_required`) denied |
| `csrf_failed`            | 403    | Django CSRF middleware rejected the request                           |
| `permission_denied`      | 403    | Handler-level `@permission_required` denied, OR `PermissionDenied` raised inside the function |
| `invalid_json`           | 400    | Body isn't valid UTF-8 JSON or isn't a top-level object               |
| `invalid_body`           | 400    | Body is valid JSON but doesn't match the `{"params": {...}}` shape   |
| `invalid_params`         | 400    | Missing required params, extra params, or type-coercion failed       |
| `rate_limited`           | 429    | `@rate_limit` token bucket drained                                    |
| `mount_failed`           | 500    | `mount()` / `api_mount()` raised                                      |
| `function_error`         | 500    | The function body raised an unexpected exception (logged, not leaked) |

---

## Security

- **Authenticated by default.** Anonymous callers get 401 immediately —
  there is no way to expose a server function to anonymous users.
- **CSRF always required.** No auth-class CSRF-exempt opt-out like
  ADR-008 offers for S2S callers. Server functions are same-origin,
  period.
- **View-level auth is honored.** `login_required`-on-the-class and
  `@permission_required`-on-the-class both run via
  `check_view_auth()` before the function is dispatched.
- **Handler-level `@permission_required` is honored** via
  `check_handler_permission()`. Stack it below `@server_function`.
- **Rate-limit bucket is shared with ADR-008 dispatch.** The process-
  level `_rate_buckets` `OrderedDict` is the same structure; the key
  is `(caller, function_name)` where `caller` is `user:<pk>` when
  authenticated. The same LRU cap and eviction rules apply.

---

## Type coercion

Server functions reuse `validate_handler_params` — the same validator
`@event_handler` uses. Coercion rules:

- Strings are coerced to the method's signature-annotated types
  (`int`, `float`, `bool`, `Decimal`, `uuid.UUID`, `datetime.date`,
  `datetime.datetime`) via the same paths as event handlers.
- Unknown params (not in the signature, and no `**kwargs`) → 400
  `invalid_params` with `details.provided`.
- Missing required params → 400 `invalid_params` with `details.expected`.
- Failed coercion (e.g. `int("abc")`) → 400 `invalid_params` with
  `details.type_errors`.
- Opt out with `@server_function(coerce_types=False)` to receive
  strings as-is.

---

## Return values

Return values are JSON-serialized via `DjangoJSONEncoder`, so these all
work out of the box:

- Primitives: `int`, `float`, `str`, `bool`, `None`
- Containers: `list`, `tuple`, `dict` (string keys)
- `datetime.date`, `datetime.datetime`, `datetime.time` — ISO 8601
- `decimal.Decimal` — as a string (preserves precision)
- `uuid.UUID` — as a string
- Django `Model` instances implementing `__json__()` (convention —
  DjangoJSONEncoder picks it up)

Anything that isn't serializable → 500 `function_error`. The
`TypeError` is logged server-side with the view slug + function name;
the client sees only the generic message. Serialize explicitly in
your function body if you need finer control:

```python
@server_function
def get_product(self, id: int) -> dict:
    p = Product.objects.get(pk=id)
    return {"id": p.id, "name": p.name, "price": str(p.price)}
```

---

## When to use it

**Good fit:**

- **Typeahead / autocomplete.** User types, server returns hits, you
  update a sibling `<ul>`. Updating the LiveView's `self.hits` and
  letting it re-render would work, but the input may lose focus or
  the caret may jump on slow devices.
- **Inline validation.** "Is this email already registered?" — call
  a server function on blur, show a hint, never touch the form DOM.
- **Fetch-without-re-render.** Pulling fresh data for a chart library
  or third-party widget that owns its own DOM subtree and doesn't
  play nice with VDOM diffing.
- **Client-owned state.** When the canonical state lives in JS
  (e.g. a Monaco editor's buffer), server functions fetch derived
  data without trying to round-trip the client state back into Python.

**Not a good fit:**

- **Mutations that should reflect in the UI.** If the user clicks
  "mark as read" and the "unread" badge should decrement, stay with
  `@event_handler` — you want the re-render.
- **External / AI-agent callers.** No OpenAPI schema is generated. Use
  `@event_handler(expose_api=True)` for those.
- **Unauthenticated public endpoints.** Server functions require a
  session cookie. Use a Django view.

---

## Comparison: `@server_function` vs. neighbours

| Dimension           | `@server_function`     | `@event_handler`      | `@event_handler(expose_api=True)` |
| ------------------- | ---------------------- | --------------------- | --------------------------------- |
| Transport           | HTTP POST              | WebSocket frame       | HTTP POST                         |
| URL                 | `/djust/api/call/<slug>/<fn>/` | (WS)          | `/djust/api/<slug>/<handler>/`   |
| Re-render           | No                     | Yes (VDOM diff)       | Yes (VDOM diff + assigns diff)    |
| Response envelope   | `{"result": ...}`      | Patches over the wire | `{"result": ..., "assigns": {...}}` |
| OpenAPI             | No                     | No                    | Yes                               |
| Anonymous callers   | No (401)               | No (WS auth)          | Configurable via auth class       |
| CSRF                | Required               | WS origin check       | Configurable (auth class)         |
| Batching            | No (one call per fetch) | No                   | No                                |
| `api_response` / `serialize=` hooks | No       | N/A                   | Yes                               |
| Primary use         | In-browser RPC         | Reactive UI           | Mobile / S2S / AI agents          |

---

## Out of scope (future)

Things explicitly **not** in v0.7.0, tracked for later:

- **Batching.** Bundling N calls into one round-trip.
- **SSE streaming responses.** For long-running generators.
- **TypeScript bindings.** Auto-generated `.d.ts` from Python
  signatures.
- **Client-side result cache.** A `djust.call` wrapper that dedupes
  in-flight requests or caches results with a TTL.

If you hit a use case that needs one of these, open an issue — none
are hard to add; they just weren't needed for v0.7.0's initial shape.
