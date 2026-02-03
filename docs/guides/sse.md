# Server-Sent Events (SSE)

SSE provides a simpler alternative to WebSocket for server-to-client streaming. It's built on standard HTTP and doesn't require Django Channels.

## When to Use SSE vs WebSocket

| Feature | SSE | WebSocket |
|---------|-----|-----------|
| Direction | Server → Client only | Bidirectional |
| Complexity | Simple (HTTP-based) | More complex |
| Django Channels | Not required | Required |
| Reconnection | Built-in | Manual |
| Browser support | All modern browsers | All modern browsers |
| Through proxies | Works easily | May need configuration |

**Use SSE when:**
- Server pushes updates to client (dashboards, notifications)
- Client rarely sends data (or uses regular HTTP for actions)
- You want simple deployment (no Channels/ASGI required)
- You need automatic reconnection with event resumption

**Use WebSocket when:**
- Bidirectional real-time communication needed
- Low-latency client-to-server messages
- Gaming, collaborative editing, chat
- You're already using Django Channels

## Quick Start

### Server-Side

```python
# views.py
from djust.sse import SSEView
import asyncio

class DashboardSSE(SSEView):
    """Stream dashboard updates every 5 seconds."""

    async def mount(self, request, last_event_id, *args, **kwargs):
        # Initialize state, check permissions, etc.
        self.user = request.user

    async def stream(self, request):
        while True:
            # Get fresh data
            stats = await self.get_dashboard_stats()

            # Yield event (auto-generates ID for resumption)
            yield self.event('stats', stats)

            await asyncio.sleep(5)

    async def get_dashboard_stats(self):
        # Your data fetching logic
        return {
            'users_online': 42,
            'requests_per_sec': 150,
            'timestamp': time.time(),
        }
```

```python
# urls.py
from django.urls import path
from .views import DashboardSSE

urlpatterns = [
    path('api/dashboard/stream/', DashboardSSE.as_view(), name='dashboard-sse'),
]
```

### Client-Side

**Declarative (recommended):**

```html
<div dj-sse="/api/dashboard/stream/"
     dj-sse-event="stats"
     dj-sse-target="#stats-display"
     dj-sse-mode="replace">
</div>

<div id="stats-display">
    <!-- Updated by SSE events -->
</div>
```

**Programmatic:**

```javascript
// Create SSE connection
const sse = new DjustSSE('/api/dashboard/stream/');

// Listen for events
sse.on('stats', (data) => {
    document.getElementById('users').textContent = data.users_online;
    document.getElementById('rps').textContent = data.requests_per_sec;
});

// Handle connection states
sse.on('connected', () => console.log('Connected!'));
sse.on('disconnected', () => console.log('Disconnected'));
sse.on('reconnecting', ({attempt}) => console.log(`Reconnecting... (${attempt})`));

// Connect
sse.connect();

// Later: disconnect
sse.disconnect();
```

## SSEView Reference

### Class Attributes

```python
class MySSE(SSEView):
    # Auto-generate event IDs (for Last-Event-ID resumption)
    auto_id = True  # default

    # Retry interval in ms (sent to client)
    sse_retry_ms = 3000  # default

    # Keepalive interval in seconds
    sse_keepalive_interval = 15  # default

    # CORS settings
    sse_cors = False  # default
    sse_cors_origins = None  # list of allowed origins, or None for '*'
```

### Methods

#### `mount(request, last_event_id, *args, **kwargs)`

Called before streaming begins. Use for initialization.

```python
async def mount(self, request, last_event_id, *args, **kwargs):
    # Check permissions
    if not request.user.is_authenticated:
        raise PermissionDenied()

    # Initialize state
    self.user_id = request.user.id

    # Handle reconnection
    if last_event_id:
        self.resume_from = int(last_event_id)
```

#### `stream(request)` → AsyncGenerator

Main streaming method. Yield SSE event strings.

```python
async def stream(self, request):
    # Yield events
    yield self.event('start', {'status': 'streaming'})

    async for item in self.data_source():
        yield self.event('item', item)

    yield self.event('complete', {'status': 'done'})
```

#### `event(event_type, data, id=None)` → str

Create an SSE event.

```python
# With auto-generated ID
yield self.event('update', {'count': 42})

# With manual ID
yield self.event('notification', notif, id=str(notif.id))
```

#### `data_event(data, id=None)` → str

Create an event without a type (just data).

```python
yield self.data_event({'raw': 'data'})
```

#### `keepalive()` → str

Send a keepalive comment.

```python
yield self.keepalive()
```

### DOM Update Helpers

For updating client DOM directly:

```python
# Send DOM patches (uses djust patch system)
yield self.push_patch([
    {'op': 'replace', 'path': '#count', 'value': '42'}
])

# Update HTML at selector
yield self.push_html('#notifications', '<li>New message</li>', mode='prepend')

# Update text
yield self.push_text('#status', 'Processing...', mode='replace')

# Navigate client
yield self.push_redirect('/dashboard/')

# Reload page
yield self.push_reload()
```

## SSEMixin

Add SSE to existing views:

```python
from djust.sse import SSEMixin
from django.views import View

class DashboardView(SSEMixin, View):
    def get(self, request):
        # Regular HTTP response
        return render(request, 'dashboard.html')


class DashboardStreamView(SSEMixin, View):
    async def get(self, request):
        # SSE stream endpoint
        return self.sse_response(self.stream_updates(request))

    async def stream_updates(self, request):
        while True:
            data = await self.get_data()
            yield self.event('update', data)
            await asyncio.sleep(5)
```

## Client API

### DjustSSE Class

```javascript
const sse = new DjustSSE(url, options);
```

**Options:**

```javascript
{
    autoReconnect: true,      // Auto-reconnect on disconnect
    maxRetries: 5,            // Max reconnection attempts
    retryDelay: 3000,         // Initial retry delay (ms)
    withCredentials: false,   // Send cookies cross-origin
}
```

**Methods:**

```javascript
sse.connect()           // Start connection
sse.disconnect()        // Close connection
sse.destroy()           // Close and prevent reconnection
sse.on(event, callback) // Add event listener
sse.off(event, callback) // Remove event listener
sse.getStats()          // Get connection statistics
```

**Events:**

```javascript
sse.on('connected', () => {})
sse.on('disconnected', ({reason}) => {})
sse.on('reconnecting', ({attempt}) => {})
sse.on('reconnected', ({reconnections}) => {})
sse.on('error', ({event}) => {})

// Custom events from server
sse.on('stats', (data) => {})
sse.on('notification', (data) => {})

// Wildcard (all events)
sse.on('*', (eventType, data, originalEvent) => {})
```

### Declarative Attributes

```html
<div dj-sse="/api/stream/"
     dj-sse-event="update"
     dj-sse-target="#output"
     dj-sse-mode="append"
     dj-sse-reconnect="true"
     dj-sse-credentials="false">
</div>
```

| Attribute | Description | Default |
|-----------|-------------|---------|
| `dj-sse` | SSE endpoint URL | (required) |
| `dj-sse-event` | Filter to specific event type | (all events) |
| `dj-sse-target` | CSS selector for update target | (element itself) |
| `dj-sse-mode` | Update mode: replace/append/prepend | replace |
| `dj-sse-reconnect` | Auto-reconnect on disconnect | true |
| `dj-sse-credentials` | Send cookies cross-origin | false |

**CSS Classes (auto-applied):**

```css
.sse-connecting { /* Connection in progress */ }
.sse-connected { /* Connected */ }
.sse-disconnected { /* Disconnected */ }
```

## Reconnection & Resumption

SSE has built-in reconnection. When reconnecting, the browser sends the last received event ID.

### Server-Side Resumption

```python
class NotificationSSE(SSEView):
    async def mount(self, request, last_event_id, *args, **kwargs):
        self.user = request.user
        self.last_id = int(last_event_id) if last_event_id else None

    async def stream(self, request):
        # Send missed events on reconnection
        if self.last_id:
            missed = await self.get_missed_notifications(self.last_id)
            for notif in missed:
                yield self.event('notification', notif.to_dict(), id=str(notif.id))

        # Stream new notifications
        async for notif in self.listen_notifications():
            yield self.event('notification', notif.to_dict(), id=str(notif.id))

    async def get_missed_notifications(self, since_id):
        from asgiref.sync import sync_to_async
        return await sync_to_async(list)(
            Notification.objects.filter(
                user=self.user,
                id__gt=since_id
            ).order_by('id')
        )
```

### Custom Retry Interval

```python
class SlowSSE(SSEView):
    sse_retry_ms = 10000  # Reconnect after 10 seconds
```

## Examples

### Progress Indicator

```python
class TaskProgressSSE(SSEView):
    async def stream(self, request):
        task_id = request.GET.get('task_id')

        while True:
            progress = await self.get_task_progress(task_id)

            yield self.event('progress', {
                'percent': progress.percent,
                'status': progress.status,
            })

            if progress.complete:
                yield self.event('complete', {'result': progress.result})
                break

            await asyncio.sleep(0.5)
```

```html
<div dj-sse="/api/tasks/progress/?task_id=123" dj-sse-event="progress">
    <div class="progress-bar">
        <div class="fill" style="width: 0%"></div>
    </div>
</div>

<script>
const el = document.querySelector('[dj-sse]');
el._djustSSE.on('progress', (data) => {
    el.querySelector('.fill').style.width = data.percent + '%';
});
el._djustSSE.on('complete', (data) => {
    alert('Task complete: ' + data.result);
});
</script>
```

### Live Feed

```python
class LiveFeedSSE(SSEView):
    async def stream(self, request):
        last_id = self.get_last_event_id(request)

        # Send missed items on reconnection
        if last_id:
            async for item in self.get_items_since(last_id):
                yield self.push_html('#feed', self.render_item(item), mode='prepend')

        # Stream new items
        async for item in self.listen_for_items():
            yield self.push_html('#feed', self.render_item(item), mode='prepend')

    def render_item(self, item):
        return f'<div class="feed-item" id="item-{item.id}">{item.content}</div>'
```

### Notifications with Actions

Use SSE for receiving, regular HTTP for actions:

```python
# SSE for receiving notifications
class NotificationStreamSSE(SSEView):
    async def stream(self, request):
        async for notif in self.listen(request.user):
            yield self.event('notification', {
                'id': notif.id,
                'html': render_to_string('_notification.html', {'n': notif})
            })

# Regular view for marking as read
class MarkReadView(View):
    def post(self, request, pk):
        Notification.objects.filter(pk=pk, user=request.user).update(read=True)
        return JsonResponse({'ok': True})
```

```html
<div dj-sse="/api/notifications/stream/">
    <ul id="notifications"></ul>
</div>

<script>
const sse = document.querySelector('[dj-sse]')._djustSSE;
sse.on('notification', (data) => {
    document.getElementById('notifications').insertAdjacentHTML('afterbegin', data.html);
});
</script>
```

## Deployment Notes

### Nginx Configuration

SSE works through nginx with minimal configuration:

```nginx
location /api/stream/ {
    proxy_pass http://django;
    proxy_buffering off;
    proxy_cache off;
    proxy_set_header X-Accel-Buffering no;
    proxy_read_timeout 86400;  # Keep connection alive
}
```

### Gunicorn/Uvicorn

SSE works with both sync and async workers:

```bash
# Async (recommended for SSE)
uvicorn myproject.asgi:application --workers 4

# Sync (works but ties up workers)
gunicorn myproject.wsgi:application --workers 8 --timeout 86400
```

### Connection Limits

Each SSE connection holds an HTTP connection open. Plan capacity accordingly:
- Use async workers (uvicorn, daphne) for many concurrent connections
- Consider connection limits per user
- Implement proper disconnect handling

## Troubleshooting

### Events Not Received

1. Check Content-Type is `text/event-stream`
2. Check for proxy buffering (add `X-Accel-Buffering: no`)
3. Verify events end with `\n\n`

### Reconnection Not Working

1. Ensure events have IDs (use `auto_id=True` or set manually)
2. Check `Last-Event-ID` header handling in mount()
3. Verify server returns missed events on reconnection

### Memory Issues

1. Clean up connections on client navigation
2. Implement connection timeouts
3. Use async generators to avoid buffering large data
