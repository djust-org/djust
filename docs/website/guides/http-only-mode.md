---
title: "HTTP-Only Mode"
slug: http-only-mode
section: guides
order: 25
level: intermediate
description: "Run djust without WebSockets: HTTP polling for environments where WS is blocked."
---

# HTTP-Only Mode (Without WebSocket)

djust can run without WebSockets. With `use_websocket: False`, the client uses the SSE transport: the server pushes updates over an `EventSource` stream, and the client sends events as HTTP POST requests. Only in a browser without `EventSource` does it fall back to POSTing events to the page URL. This is useful for:

- Testing without WebSocket infrastructure
- Environments where WebSocket connections are blocked
- Simpler deployment scenarios
- Debugging and development

## Configuration

### Option 1: Django Settings (Global)

Add to your `settings.py`:

```python
LIVEVIEW_CONFIG = {
    'use_websocket': False,  # Disable WebSocket, use HTTP-only mode
}
```

### Option 2: Programmatic Configuration

```python
from djust.config import config

# Disable WebSocket
config.set('use_websocket', False)
```

### Mount the SSE endpoint

The SSE transport needs its URL patterns. Add them to your root URLconf:

```python
from django.urls import include, path
from djust.sse import sse_urlpatterns

urlpatterns = [
    # ...
    path("djust/", include(sse_urlpatterns)),
]
```

Without these routes the stream at `/djust/sse/<session>/` returns 404, the client marks the transport disabled and fires `dj-disconnected`, and events do nothing.

## How It Works

### With WebSocket (Default)

```
Browser                    Server
   |---WebSocket Connect--->|
   |<--Connected------------|
   |---Event: click-------->|
   |<--Patches--------------|
   |---Event: change------->|
   |<--Patches--------------|
```

### With `use_websocket: False` (SSE transport)

```
Browser                                   Server
   |---GET /djust/sse/<session>/ (EventSource)-->|
   |<==stream: patches ==========================|
   |---POST /djust/sse/<session>/message/ ------>| (event + params)
   |<==stream: patches ==========================|
```

### Fallback: browsers without `EventSource`

```
Browser                    Server
   |---POST <page URL> ---->| (event + params)
   |<--JSON patches---------|
```

## Behavior Differences

| Feature | WebSocket Mode | SSE / HTTP Mode |
|---------|---------------|-----------|
| Connection | Persistent | SSE stream + one POST per event |
| Latency | Lower (~10ms) | Higher (~50-100ms) |
| Server Load | Lower | Higher (new request per event) |
| Fallback | To SSE, if mounted | To page POST, if no `EventSource` |
| Deployment | Requires WebSocket support | Standard HTTP only |

## Example: HTTP-Only LiveView

```python
# views.py
from djust import LiveView
from djust.decorators import event_handler

class CounterView(LiveView):
    template_name = 'counter.html'

    def mount(self, request, **kwargs):
        self.count = 0

    @event_handler
    def increment(self, **kwargs):
        self.count += 1

    @event_handler
    def decrement(self, **kwargs):
        self.count -= 1
```

```html
<!-- counter.html -->
<!DOCTYPE html>
<html>
<head>
    <title>Counter (HTTP Mode)</title>
</head>
<body>
    <div dj-root>
        <h1>Counter: {{ count }}</h1>
        <button dj-click="increment">+</button>
        <button dj-click="decrement">-</button>
    </div>
</body>
</html>
```

## Testing HTTP Mode

1. **Set the configuration**:
<!-- Normal markdown: the fence sits inside a numbered list, so it is
     indented. The checker does not dedent before parsing. -->
<!-- doc-snippet-check: skip -->
   ```python
   # settings.py
   LIVEVIEW_CONFIG = {
       'use_websocket': False,
   }
   ```

2. **Run the server**:
   ```bash
   python manage.py runserver
   ```

3. **Check browser console** (with `DEBUG=True` and `globalThis.djustDebug = true`):
   - Should see: `[LiveView] WebSocket disabled, using SSE transport directly`
   - In a browser without `EventSource`: `[LiveView] HTTP-only mode (use_websocket: false)`
   - No WebSocket connection attempts

4. **Test interactions**:
   - Click events trigger HTTP POST requests to `/djust/sse/<session>/message/`
   - Check the Network tab in DevTools: one open `EventSource` stream, plus one POST per event

## Performance Considerations

### WebSocket Mode (Recommended for Production)
- ✅ Lower latency
- ✅ Persistent connection
- ✅ Better for frequent updates
- ✅ Lower server load

### HTTP Mode (Good for Development/Testing)
- ✅ Simpler infrastructure
- ✅ No WebSocket configuration needed
- ✅ Easier debugging (see requests in Network tab)
- ⚠️  Higher latency
- ⚠️  More server load

## Automatic Fallback

If the WebSocket exhausts its reconnect attempts and the SSE endpoint is mounted (`include(sse_urlpatterns)`), djust switches to the SSE transport automatically. Without SSE mounted there is no automatic fallback.

## Troubleshooting

### Events not working in HTTP mode

Check:
1. `sse_urlpatterns` is mounted (see [Mount the SSE endpoint](#mount-the-sse-endpoint)); the stream at `/djust/sse/<session>/` must not 404
2. Event handlers are decorated with `@event_handler` (the default `event_security = "strict"` rejects undecorated methods)
3. Browser console for errors
4. Django middleware allows POST requests

### WebSocket won't disable

Verify:
1. Configuration is loaded: `from djust.config import config; print(config.get('use_websocket'))`
2. Clear browser cache
3. Hard reload the page (Cmd+Shift+R / Ctrl+Shift+R)
