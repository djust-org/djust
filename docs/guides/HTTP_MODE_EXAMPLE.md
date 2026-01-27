# HTTP-Only Mode (Without WebSocket)

djust supports running in HTTP-only mode, which uses standard HTTP POST requests instead of WebSocket for event handling. This is useful for:

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

### With HTTP-Only Mode

```
Browser                    Server
   |                        |
   |---POST /view/ -------->| (event + params)
   |<--JSON patches---------|
   |---POST /view/ -------->| (event + params)
   |<--JSON patches---------|
```

## Behavior Differences

| Feature | WebSocket Mode | HTTP Mode |
|---------|---------------|-----------|
| Connection | Persistent | Per-request |
| Latency | Lower (~10ms) | Higher (~50-100ms) |
| Server Load | Lower | Higher (new connection each time) |
| Fallback | Automatic | N/A |
| Deployment | Requires WebSocket support | Standard HTTP only |

## Example: HTTP-Only LiveView

```python
# views.py
from djust import LiveView

class CounterView(LiveView):
    template_name = 'counter.html'

    def mount(self, request, **kwargs):
        self.count = 0

    def increment(self):
        self.count += 1

    def decrement(self):
        self.count -= 1
```

```html
<!-- counter.html -->
{% load djust %}
<!DOCTYPE html>
<html>
<head>
    <title>Counter (HTTP Mode)</title>
</head>
<body>
    <div data-liveview-root>
        <h1>Counter: {{ count }}</h1>
        <button dj-click="increment">+</button>
        <button dj-click="decrement">-</button>
    </div>
</body>
</html>
```

## Testing HTTP Mode

1. **Set the configuration**:
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

3. **Check browser console**:
   - Should see: `[LiveView] Using HTTP-only mode (WebSocket disabled)`
   - No WebSocket connection attempts

4. **Test interactions**:
   - Click events trigger HTTP POST requests
   - Check Network tab in DevTools to see POST requests
   - Each event creates a new HTTP request

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

Even in WebSocket mode, djust automatically falls back to HTTP if:
- WebSocket connection fails
- Max reconnection attempts exceeded
- WebSocket server unavailable

This ensures your application remains functional even if WebSocket infrastructure fails.

## Troubleshooting

### Events not working in HTTP mode

Check:
1. CSRF token is present in cookies
2. POST endpoint is accessible
3. Browser console for errors
4. Django middleware allows POST requests

### WebSocket won't disable

Verify:
1. Configuration is loaded: `from djust.config import config; print(config.get('use_websocket'))`
2. Clear browser cache
3. Hard reload the page (Cmd+Shift+R / Ctrl+Shift+R)
