---
title: "Service Worker: Instant Shell + Reconnection Bridge"
slug: service-worker
section: guides
order: 12.5
level: advanced
description: "Opt-in SW features for SPA-style instant navigation and resilient WebSocket delivery across brief disconnects."
---

# Service Worker: Instant Shell + Reconnection Bridge

djust ships a small, opt-in service worker (SW) that adds two reliability / perceived-performance features without changing how you write views:

1. **Instant page shell** — on every navigation, the SW serves a cached HTML shell (`<head>`, `<nav>`, `<footer>`) immediately, while the client fetches only the fresh `<main>` contents from the server. The user sees the chrome before the network response returns.
2. **WebSocket reconnection bridge** — when the WebSocket is briefly disconnected, outgoing events are buffered in the SW (in-memory, per connection) instead of being dropped. On reconnect they are replayed in order.

Both features are **opt-in** and **independent**. Neither is active unless you register the SW explicitly.

> **Status**: v0.5.0. In-memory buffer only (IndexedDB deferred to v0.6). Shell/main extraction uses a regex — see [Limitations](#limitations).

---

## When to use it

- **Instant shell** pays off on content-heavy sites where `<head>` + `<nav>` + `<footer>` account for a noticeable chunk of time-to-first-paint. It is a perceived-latency win, not a throughput win.
- **Reconnection bridge** pays off on flaky mobile / office-WiFi connections where the user's click-burst often straddles a brief WS drop. Without it, those clicks are silently lost.

If your users are on LAN with a perfect WebSocket, neither feature is needed.

---

## Setup

### 1. Register the service worker from your init code

The SW file is served at `/static/djust/service-worker.js` — no routing or Django view needed, it's shipped as a static asset.

Somewhere in your page's init JS (e.g. a `<script>` tag in `base.html`, after `client.js`):

```html
<script>
    window.addEventListener('load', function () {
        djust.registerServiceWorker({
            instantShell: true,
            reconnectionBridge: true,
        }).then(function (reg) {
            if (reg) console.log('[djust] SW registered');
        });
    });
</script>
```

All options are individually toggleable:

| Option                | Default                                     | Effect                                          |
| --------------------- | ------------------------------------------- | ----------------------------------------------- |
| `instantShell`        | `false`                                     | Enable the cached-shell navigation fast path.   |
| `reconnectionBridge`  | `false`                                     | Enable outgoing-message buffering during WS disconnect. |
| `vdomCache`           | `false`                                     | (v0.6.0) Cache VDOM HTML per URL; serve instantly on popstate before reconciling against the live mount reply. |
| `stateSnapshot`       | `false`                                     | (v0.6.0) Persist a view's public state on `djust:before-navigate`; restore via `_restore_snapshot()` instead of running `mount()` on back-nav. |
| `swUrl`               | `/static/djust/service-worker.js`           | Override if you serve the SW from a custom path. |
| `scope`               | `/`                                         | Override if the SW should only manage a subtree. |

If `navigator.serviceWorker` is unavailable (old browser, private-mode edge case) **all features gracefully no-op**. `registerServiceWorker(...)` resolves to `null`.

### 2. Add the main-only middleware (required for `instantShell`)

Instant shell needs the server to respond with only the `<main>` element's inner HTML when the client sends `X-Djust-Main-Only: 1`. Enable it by adding the middleware to your Django settings:

```python
# settings.py

MIDDLEWARE = [
    # ... your existing middleware ...
    "djust.middleware.DjustMainOnlyMiddleware",
]
```

The middleware is **mostly ordering-safe** — it only reads the request header and trims `response.content` on the way out. It:

- Passes through non-HTML responses (JSON, binary) unchanged.
- Passes through requests that don't carry the header.
- Updates `Content-Length` after trimming.
- Sets `X-Djust-Main-Only-Response: 1` on transformed responses so clients can distinguish them.
- Leaves streaming responses untouched.

> **⚠ Ordering caveat with `GZipMiddleware`.** If you use Django's `GZipMiddleware`, place `DjustMainOnlyMiddleware` **above it** in the `MIDDLEWARE` list (so it runs first on the outgoing response). `MIDDLEWARE` executes in reverse order on responses, so "above" = "runs later on responses." If the truncation runs AFTER gzip compression, the `Content-Encoding: gzip` header stays but the bytes have been modified in place, producing a broken response the client cannot decode. Rule of thumb: any middleware that modifies `response.content` must run before any middleware that encodes/compresses it.

### 3. Ensure your layout has a `<main>` element

The SW splits HTML into "shell" (everything outside `<main>`) and "main" (inside). If your base template has no `<main>`, the SW silently skips caching (the first-load user just sees the normal response). A minimal example:

```html
<!DOCTYPE html>
<html>
<head>
    <title>{% block title %}My App{% endblock %}</title>
</head>
<body>
    <nav>…</nav>

    <main>
        {% block content %}{% endblock %}
    </main>

    <footer>…</footer>
    <script src="{% static 'djust/client.js' %}"></script>
</body>
</html>
```

---

## How it works

### Instant shell — navigation flow

```
First navigation:
  Browser  --nav GET /page/--> SW --passthrough--> Django
                                                   returns full HTML
  Browser <------- full HTML (unchanged) -------- SW (caches split shell)

Subsequent navigation:
  Browser  --nav GET /page/--> SW
                               |
                               └-- returns cached shell with
                                   <main data-djust-shell-placeholder="1"></main>

  Client JS sees the placeholder, fires:
  Browser  --fetch /page/, X-Djust-Main-Only: 1--> Django
                                                   DjustMainOnlyMiddleware
                                                   trims response to <main> inner
  Browser  <--- fresh <main> inner HTML ---

  Client swaps the placeholder's innerHTML with the fresh content.
```

### Reconnection bridge — message flow

```
WS OPEN:    client.ws.send(message) --> server (normal path)

WS CLOSED:  client.ws.send(message)
              |
              └-- sendMessage() detects readyState != OPEN
                  |
                  └-- postMessage({type: DJUST_BUFFER, connectionId, payload}) --> SW
                                                                                   |
                                                                                   └-- RECONNECT_BUFFER.get(connectionId).push(payload)

WS re-OPEN: client dispatches "djust:ws-open"
              |
              └-- postMessage({type: DJUST_DRAIN, connectionId}) --> SW
              |                                                      |
              |                                                      └-- delete & reply
              |
              └-- SW postMessage({type: DJUST_DRAIN_REPLY, messages: [...]}) --> client
                                                                                 |
                                                                                 └-- replay each via ws.ws.send(raw)
```

Buffered messages are **capped at 50 per connection** (oldest dropped). Buffers are **in-memory** in the SW process — an SW restart (browser shutdown, SW replaced by a newer version) loses them.

---

## Advanced features (v0.6.0)

Three opt-in optimizations layered on top of the v0.5.0 core. All are
off by default — enable per-feature on `registerServiceWorker(...)`
and (for state snapshots) per-view via a class attribute. Each one
gracefully no-ops when its preconditions aren't met, so partial
adoption is always safe.

### VDOM patch cache

When the user clicks `<a dj-link>` or hits the back button, the SW
serves a cached HTML snapshot of the destination URL **immediately**,
then the live WebSocket mount reply reconciles any drift via the
normal VDOM patch path. The user sees content the instant the route
changes; the network round-trip is hidden behind the perceived paint.

Enable in two places — the registration call AND your settings:

```js
djust.registerServiceWorker({ vdomCache: true });
```

```python
# settings.py
DJUST_VDOM_CACHE_ENABLED = True
DJUST_VDOM_CACHE_TTL_SECONDS = 300         # default — entries expire after 5 min
DJUST_VDOM_CACHE_MAX_ENTRIES = 100         # default — LRU evict beyond this cap
```

Three system checks guard the configuration ranges so a typo (e.g.
negative TTL) fails fast at `manage.py check`:

| Check ID  | Severity | Fires when |
|---|---|---|
| `djust.C301` | error | `DJUST_VDOM_CACHE_TTL_SECONDS` is non-positive or > 1 day |
| `djust.C302` | error | `DJUST_VDOM_CACHE_MAX_ENTRIES` is non-positive or > 10 000 |
| `djust.C303` | warning | `DJUST_VDOM_CACHE_ENABLED = True` without the SW registered with `vdomCache: true` |

Cache scope is **per origin + per URL** with the `Vary: Cookie` and
`Vary: Accept-Language` headers honored — different users / locales
don't see each other's snapshots.

### LiveView state snapshots

A view that opts in stamps a JSON-serializable copy of its public
state on the SW each time the user navigates **away**. On a back
navigation, the SW returns that snapshot and the server calls
`_restore_snapshot(state)` instead of `mount()`. Form values, scroll
position, expanded/collapsed sections — all preserved without
embedding state in the URL or refetching it.

```python
class CartView(LiveView):
    enable_state_snapshot = True

    def mount(self, request):
        self.items = list(request.user.cart_items.values('id', 'qty'))

    def _restore_snapshot(self, state: dict) -> None:
        # state is the dict the client captured on `djust:before-navigate`.
        # Trust nothing — re-validate any IDs against the database.
        self.items = state.get('items', [])

    def _should_restore_snapshot(self, request) -> bool:
        # Override to reject stale snapshots. Default returns True for any
        # snapshot < 5 minutes old. Return False to fall back to mount().
        return super()._should_restore_snapshot(request)
```

Snapshots are JSON only (no `pickle`), capped at 256 KB by the SW and
64 KB by the client clamp, and `safe_setattr` blocks dunder /
private attributes during restoration.

System check `djust.C304` warns if a snapshot-opt-in view declares
attributes whose names match PII patterns (`password`, `token`,
`secret`, `api_key`, `pii`) — a guardrail against accidentally
shipping sensitive state through the SW.

### Mount batching

Pages that render multiple `dj-lazy` LiveViews used to fire one
`mount` WebSocket frame per view. As of v0.6.0 the client coalesces
those into a single `mount_batch` frame; the server replies with one
`mount_batch` carrying every rendered view, and per-view failures are
isolated in a `failed[]` array — one bad view no longer kills the
batch.

No code changes are required to opt in — the batching is automatic
when the client and server are both ≥ v0.6.0. To opt out (e.g. for
debugging), set:

```js
window.DJUST_USE_MOUNT_BATCH = false;
```

before `client.js` runs.

---

## Caveats & best practices

- **Shell staleness**. The cached shell is tagged `djust-shell-v1`. When you deploy a template that changes `<head>` or `<nav>`, users will see the old shell until it is refreshed. Clear it by posting `{type: 'DJUST_CLEAR_SHELL'}` to the SW, or bumping the cache name in `service-worker.js`. A future version will wire this into djust's deploy signals.
- **Server actions must be idempotent over replay**. The reconnection bridge replays buffered events best-effort. If a buffered event triggers a server-side side effect (payment, email send), the server currently has no dedup logic (v0.5.0 risk — see [Out of scope](#out-of-scope)). Use `@event_handler` for reads and low-stakes writes; guard high-stakes writes with your own idempotency keys.
- **Don't register the SW on authentication/session URLs**. Scope the registration to `/app/` if you have a login flow that must not be cached.
- **Dev mode**. The SW caches the shell on the first successful navigate. During template development this can cache a broken shell. Either skip `instantShell: true` in dev, or bump `SHELL_CACHE` to invalidate.

---

## Limitations

The shell/main extractor is a **regex** (`/<main\b[^>]*>([\s\S]*?)<\/main>/i`), not a full HTML parser. Known edge cases where it misbehaves:

- **Nested `<main>` tags** (extremely rare, invalid HTML): only the first is matched.
- **`<main>` inside an HTML comment** (`<!-- <main>…</main> -->`): the regex still matches the contents of the comment. Avoid literal `<main>` inside comments in templates.
- **`</main>` token inside a CDATA block within `<main>`**: prematurely closes the match. Avoid inline `<![CDATA[ … </main> … ]]>` — this pattern is valid only inside `<svg>` / `<math>` and is very rare.

A full HTML-parser-based replacement is deferred. For most apps, the regex is correct 100% of the time.

---

## Out of scope for v0.5.0

- **IndexedDB persistence** of the reconnection buffer (survives browser restart). In-memory only today.
- **Server-side replay dedup** via sequence numbers. The SW replays best-effort; write handlers that can tolerate duplicates, or add your own idempotency.
- **Full offline mode** (serve any cached page when the server is unreachable).
- **PWA manifest** / install prompt — use the existing [PWA guide](pwa.md) for that.
- **Push notifications** / Background Sync.

---

## Unregistering the service worker

```html
<script>
    navigator.serviceWorker.getRegistrations().then(function (regs) {
        regs.forEach(function (r) { r.unregister(); });
    });
    caches.keys().then(function (keys) {
        keys.forEach(function (k) {
            if (k.indexOf('djust-shell') === 0) caches.delete(k);
        });
    });
</script>
```

---

## Related

- [PWA guide](pwa.md) — offline caching manifest etc.
- [Reconnection](reconnection.md) — the core WS reconnect behaviour (separate from this bridge).
