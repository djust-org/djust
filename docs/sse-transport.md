# SSE Fallback Transport

djust normally uses a WebSocket connection for real-time server-client
communication. In some corporate and enterprise environments WebSocket
connections are blocked by HTTP proxies, firewalls, or API gateways. When this
happens, djust automatically falls back to **Server-Sent Events (SSE)**.

## How it works

SSE is a unidirectional push protocol (HTTP/1.1). djust uses two complementary
endpoints to achieve full bidirectional communication:

| Direction        | Mechanism              | URL pattern                          |
|------------------|------------------------|--------------------------------------|
| Server → Client  | `EventSource` (SSE)    | `GET /djust/sse/<session_id>/`       |
| Client → Server  | HTTP POST              | `POST /djust/sse/<session_id>/event/`|

The client generates a random UUID session ID, opens an SSE stream with `?view=`
pointing at the LiveView class, and sends user events as JSON POST bodies to the
event endpoint.

## Transport negotiation

Transport selection is fully automatic:

1. **WebSocket** — tried first on every page load.
2. **SSE** — activated automatically when WebSocket fails after all reconnect
   attempts (default: 5). Requires the SSE URL patterns to be registered (see
   [Setup](#setup) below).
3. **HTTP POST** — existing per-event fallback used when neither WebSocket nor
   SSE is available.

You can force SSE by setting `window.DJUST_USE_WEBSOCKET = false` before the
djust JS bundle loads. If `LiveViewSSE` is present on the page, SSE will be
used instead of the plain HTTP fallback.

## Setup

### 1. Register URL patterns

Add the SSE endpoints to your Django `urlpatterns`:

```python
# urls.py
from django.urls import path, include
from djust.sse import sse_urlpatterns

urlpatterns = [
    ...
    path("djust/", include(sse_urlpatterns)),
]
```

This registers two routes:

- `GET  /djust/sse/<session_id>/`
- `POST /djust/sse/<session_id>/event/`

### 2. Include the SSE JS bundle

The SSE transport is implemented in `03b-sse.js`. Include it alongside the
main djust bundle in your base template, **after** `djust.js`:

```html
{% load static %}
<script src="{% static 'djust/djust.js' %}"></script>
<script src="{% static 'djust/src/03b-sse.js' %}"></script>
```

Alternatively, if you build a custom bundle, add `03b-sse.js` to your build
pipeline. It must appear **after** `03-websocket.js` and **before**
`14-init.js`.

### 3. That's it

No view-level changes are needed. The transport switch is transparent to your
LiveView code.

## Feature limitations

The SSE transport supports the core LiveView feature set. A small number of
advanced features are **not available** over SSE:

| Feature                       | WebSocket | SSE        |
|-------------------------------|-----------|------------|
| Core event → patch cycle      | ✅        | ✅         |
| `start_async()` background work | ✅      | ✅         |
| `push_event()` server push    | ✅        | ✅         |
| `live_patch` / `live_redirect`| ✅        | ✅         |
| Accessibility announcements   | ✅        | ✅         |
| `@cache` decorator            | ✅        | ✅         |
| Binary file uploads           | ✅        | ❌ (text-only transport) |
| Presence tracking / cursors   | ✅        | ❌ (requires bidirectional channel) |
| Actor-based state management  | ✅        | ❌         |
| MessagePack binary encoding   | ✅        | ❌         |
| Embedded child view scoping   | ✅        | ❌ (sends full-page updates) |
| Hot-reload                    | ✅        | ❌         |

For file uploads over SSE, implement a separate standard Django file upload
endpoint and call it directly from your template.

## Multi-process deployments

SSE sessions are stored **in-process** in a Python dict. In multi-worker
deployments (e.g. `gunicorn -w 4`) the SSE stream GET and the event POST
requests **must be routed to the same worker process**.

Configure your load balancer with session affinity (sticky sessions) based on
the session UUID in the URL path. Example nginx configuration:

```nginx
upstream djust_app {
    # Sticky by the /djust/sse/<uuid>/ path segment
    hash $request_uri consistent;
    server 127.0.0.1:8000;
    server 127.0.0.1:8001;
}
```

Or use a single-worker process for SSE and multiple workers for HTTP, routing
`/djust/sse/` to the single-worker backend.

In single-worker / single-process deployments (Daphne, uvicorn default) no
special configuration is needed.

## Security

- **Session ID as CSRF token**: The SSE session ID is a 128-bit UUID generated
  by `crypto.randomUUID()`. It is unguessable and acts as an implicit CSRF
  token for the event POST endpoint. Django's CSRF middleware is exempted on the
  event endpoint for this reason.
- **Module allowlist**: The `LIVEVIEW_ALLOWED_MODULES` setting applies to SSE
  mounts in the same way as WebSocket mounts.
- **Auth**: The standard `check_view_auth` auth flow runs at mount time, using
  the real HTTP request with session and user context.
- **`@event_handler` decorator**: The same `@event_handler` allowlist is
  enforced for SSE events.
- **Rate limiting**: Per-connection and per-handler rate limits are applied
  using the same `ConnectionRateLimiter` as the WebSocket transport.

## Debugging

Set `window.djustDebug = true` in your browser console to see SSE transport
messages:

```javascript
window.djustDebug = true;
```

You will see logs like:

```
[SSE] Connecting, view: myapp.views.DashboardView
[SSE] Connection acknowledged, session: 550e8400-...
[SSE] View mounted: myapp.views.DashboardView
[SSE] Received: patch {...}
```

To force the SSE transport without waiting for WebSocket to fail:

```javascript
window.DJUST_USE_WEBSOCKET = false;
// then reload, or:
window.djust._switchToSSETransport();
```
