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

### What the page-POST fallback keeps between events

Every POST to the page URL builds a fresh view instance. What it gets back depends on the view's exposure policy.

**How a POST builds the view.** As in 1.x, the view is rebuilt from the state the previous request saved (its public attributes, the `_private` attributes `mount()` created, its `state()` fields), and `mount()` does not run again; then `handle_params()` reads the URL and `handle_restore()` runs. `mount()` runs only when nothing is saved yet, or when something of the view could not be saved (a queryset, the request's user, a component, work `mount()` queued): then `mount()` re-creates it and the saved state is put back over it. Read what must be current on every request in `handle_restore()` or `get_context_data()`. The URL is read after the state is restored, as on the socket, so a value `handle_params()` derives from the URL takes the URL's value on every POST. Saving is server-side only; what reaches the template is still decided by [explicit exposure](../state/explicit-exposure.md).

**A view that states `exposure_policy = "legacy"`** (deprecated) keeps nothing between requests in 2.0: every POST runs `mount()` and `handle_params()` again, applies the event and renders, so anything a handler set on `self` is back at its `mount()` value on the next POST. Until 2.0 such a view's public and private attributes and its components' state were saved in the session and put back instead of running `mount()`; that save, and `handle_restore()` for a legacy view, are gone (see [Migrating to 2.0](migrating-to-2.0.md#5-what-does-not-survive)). An interactive component (`DropdownMenu`) gets a new identity on each mount, so an event addressed to the one the previous answer rendered is refused with 400 `{"error": "Component not found"}`, as on an explicit view.

Whenever the fallback runs `mount()` it then runs `handle_params(params, uri)`, as the GET and the socket mount do, with the POST's own query string (the browser posts to `window.location.href`, so `?scope=mine` is on the request). A page that reads its URL there meets the event with the state the browser shows, and the answer is a diff against that page, not against `mount()`'s defaults.

`start_async()` and `@background` work runs inside the request, before the answer is rendered, because the fallback has one response and no later frame to deliver a result. The handler's loading state is therefore never rendered on this transport: the one answer holds the result.

- **What runs.** Work the handler queued, always. Work `mount()` or `handle_params()` queued (a page that loads its rows after its first render, `assign_async`) runs right before the handler, after every refusal (object permission, handler permission, `@rate_limit`, parameter validation), so a refused request starts none of it. Since `mount()` runs on every POST, so does that work: a slow loader is started again by each POST, and a side effect of `mount()` that must happen once per page load (a counter, a one-time token) belongs outside a page used over the fallback.
- **How it runs.** Each task goes through the same dispatch the socket uses, so `async def` callbacks, `cancel_async()` and `handle_async_result(name, result=..., error=...)` behave as they do there; a failing task is logged and handed to `handle_async_result` rather than failing the request. Tasks run **one after another, in the order they were queued**, so the request's latency is the sum of the tasks (three loaders of half a second each take about a second and a half), where the socket runs them concurrently. Work that queues more work runs in further rounds, up to eight, and anything still queued after that is dropped with a warning.
- **Deadline: `http_fallback_background_timeout`.** By default nothing bounds a task: one that does not return holds the POST open for as long as it runs, and a sync callback runs on the request thread. Set a deadline, in seconds, to bound the POST:

  <!-- Normal markdown: the fence sits inside a list item, so it is
       indented, and the checker does not dedent before parsing. -->
  <!-- doc-snippet-check: skip -->
  ```python
  LIVEVIEW_CONFIG = {
      "http_fallback_background_timeout": 30,  # default None: no deadline
  }
  ```

  (`DJUST_CONFIG` is read too. A value that is not a number from above 0 up to `threading.TIMEOUT_MAX` (about 9.2e9 seconds, the longest a thread can wait; a larger one is rejected, not clamped) means no deadline, and system check [C028](error-codes.md#c028-invalid-http_fallback_background_timeout) reports it. It applies to the page-POST fallback only; the socket and SSE transports run their tasks concurrently and are not held by one.)

  - **Per request, not per task.** It is the total time one POST waits for background work: one budget shared by mount's work, the handler's work and a page's work after a child's event. Per-task deadlines would let N tasks hold the request for N times as long. The clock starts when the first task does, so a slow handler does not spend it.
  - **Why tasks run on a worker thread.** A sync callback goes through `async_to_sync`, which runs it on the request thread, so nothing that waits on it from outside could ever free the request. With the key set, each task runs on a daemon worker thread (in a copy of the request's context, closing the database connections it opened when it ends) and the request waits on it only while the budget lasts. The cost: the callback no longer shares the request's database connection, so under `ATOMIC_REQUESTS` it does not see the handler's uncommitted writes. That is why the key is off by default.
  - **Async callbacks run on a private event loop.** An `async def` callback under a deadline runs on its own event loop in the worker thread, not the server's. Without the key, under ASGI, `async_to_sync` hands it back to the loop the view runs on. So an object bound to the server's loop and created elsewhere (an async redis or `httpx` client built at import, the channel layer) is on the wrong loop in the callback and fails there, but only when the key is on. Create such clients inside the callback, or keep the work on the socket or SSE transport.
  - **Abandoned threads are capped.** Each abandoned sync callback is a live thread holding a database connection until it returns, and any worker in flight can become one. So worker slots are reserved atomically before a worker starts: at most 16 workers, abandoned ones that have not ended plus ones a POST is still waiting for, may exist in the process at once (`_HTTP_BACKGROUND_MAX_ABANDONED` in `djust/mixins/request.py`; a constant, not a setting). Concurrent POSTs cannot overshoot it, which also means at most 16 background tasks run on workers at once per process under a deadline. Past it a new task is not started: a warning is logged and `handle_async_result` gets a `TimeoutError` ("not started: ... background worker threads (abandoned or still running) hold the 16 slots"). A slot is released when its worker finishes, or when an abandoned one ends. A callback that hangs on every POST therefore costs 16 threads and then fails fast, instead of piling up without limit.
  - **At the deadline** the fallback stops waiting and answers from the state at that moment: the running task is abandoned and every task still queued (this round's and any a timeout handler queues) is dropped without starting. A warning names them. `handle_async_result(name, result=None, error=...)` is called for each with a `TimeoutError`, whose message says whether the task "did not finish" or was "not started", so the view can clear a spinner or show a message.
  - **What happens to the abandoned task.** An `async def` callback is cancelled: it gets `CancelledError` at its next `await` and its `finally` blocks run. A sync callback cannot be cancelled (Python cannot stop a thread): it **keeps running** to its end, and its result or exception is discarded, never delivered to `handle_async_result`. It can still write to the view object the answer was rendered from. That object is discarded after the response, so a write that lands after the render is in neither the answer nor the state saved for the next request, but one that lands during the render can race it; anything it did outside the view (database writes, files, other services) is not undone. An `async def` that blocks the event loop with a blocking call cannot be cancelled either, because cancellation needs the loop; the request still stops waiting.
  - **A deadline does not make a watcher safe.** It limits how long a POST waits, not how long the work runs: a watcher loop (`while not stop: sleep(...)`) or a subscription started in `mount()` would be abandoned, and left running on a thread, by every POST. Guard such work so it does not start on a fallback request (for example, `if not hasattr(self, "_websocket_session_id"): return` at its start, since only a live connection sets that attribute), or keep the view on the WebSocket or SSE transport.
- **An embedded child's event** answers with the child's own HTML. The page is mounted only to register its children, so the work its `mount()` queued is discarded, not run; the work the child's handler queued on the child or on its page does run.
- **An event the view does not define** starts none of the page's `mount()`-queued work (it passes no `@rate_limit` check).

A `live_redirect()` or `live_patch()` that a handler queues is returned in the answer's `_navigation` list, in the shape of the WebSocket `navigation` frame, and the client applies it. After a `live_redirect()` nothing is rendered or saved for that request, so a handler can call `logout()` and then redirect. The flip side: a handler that changes state and then calls `live_redirect()` back to the same view's URL does not keep that change over the HTTP fallback, because the state is not saved. A `live_patch()` renders as usual and carries its frame beside the patches.

### What of the view is kept in the session

The view's state is kept in the Django session under djust's own keys (`_djust_auto_...` for the view's state, `_djust_explicit_...` for fields declared `persist="server"`, including the fields of an explicit embedded child). It is bound to the session, the user, the tenant and the route, expires after `DJUST_SERVER_STATE_MAX_AGE` (one hour), and needs a server-side session engine; with signed-cookie sessions nothing is saved. The 1.x `liveview_<path>` keys are never read. The 1.x setting that switched this off, `LIVEVIEW_CONFIG["view_state_in_session"] = False`, is refused at startup: delete it. A view that still states the deprecated `exposure_policy = "legacy"` saves nothing.

What is not saved: a `state(..., persist=False)` field, names on the sensitive-field list, and values that cannot be saved (which make the next request run `mount()` again). Saved state past 512 KB per view is refused whole, never saved in part.

So a form must post what identifies its target. Nothing of the page's open UI state survives between POSTs: a dialog that was open is closed again on the next request, and its submit meets no dialog. Put the target in the form (hidden fields, or `dj-value-*` attributes on the button) and let the handler read it from its parameters, or reopen the dialog from them before it acts. The URL is read again by `handle_params()` on every POST, so state that lives in the query string (a filter, a scope, `?tab=`) needs nothing extra.

### Embedded views over the HTTP fallback

An event that comes from inside a `{% live_render %}` child carries the child's `view_id`. Over the page-POST fallback the request renders the page once to register its children, then runs the event on the child, and answers with an `embedded_update` holding the child's own HTML, the frame the WebSocket and SSE transports send. Authorization and parameter checks are the page's own, applied to the child: the page's view-level and object permission, the child's own view-level auth and object permission (checked when the page registers it), `@permission_required` on the handler, `@event_handler` and the handler's parameter policy.

A child's id has to be the same on the page GET and on the POST that follows, and has to name the same child even when the page changed in between. Over HTTP an auto-assigned id is therefore named for what the child is, `child_<12 hex digits>` from its view path and the arguments of its `{% live_render %}` tag (a digest keyed with `SECRET_KEY`, so the id does not let a client confirm guesses about the arguments; it is the same in every process) (the process-wide counter used over a socket never repeats between requests, and a position in the render would point at a different row once the list changed). Two tags with the same view and arguments get `_2`, `_3` suffixes. A child pinned with `view_id="..."`, and a sticky child, keep their own ids.

So a list of children stays addressable while it changes: a row added in front of it in another tab leaves every row's id as it was, and an event for a row that has gone is refused (`Embedded view not found`) rather than run on a neighbour. The arguments must give the same value on both requests: scalars, model instances (by primary key), and containers of them do. An argument that is some other object counts by its type only, and one that changes between the GET and the POST (a timestamp) makes the child unreachable until the page is reloaded. Pin such a child with `view_id="..."`.

What carries over and what does not:

- A child of a legacy page is re-created on each request, as it is on every parent render over HTTP: its state does not accumulate between events (two `inc` events on a counter child both answer `count=1`). Routing works; persistence does not. (In 1.x a sticky child that opted in with `enable_state_snapshot` was saved under its sticky key; 2.0 removed that, see [Sticky-Child State Persistence](sticky-child-persistence.md).) Make the child explicit and declare the fields it must keep, or use the WebSocket or SSE transport.
- Each event re-renders the page once to register its children: a second `get_context_data` and a mount per child. Fine for a page; worth knowing for a page with many children.
- An id that names no child of the page, an [explicit-exposure](../state/explicit-exposure.md) child, and any child of an explicit-exposure page are refused with `{"error": "Embedded view not found"}`. An explicit child's turn is authorized against the mount binding of a socket session, which a stateless request has not.
- Views mounted beside the page view (lazy and batched views, see [Several LiveViews on One Page](multiple-views.md#transports)) are live over a WebSocket and over the SSE transport, which hosts them as views of its own. The page-POST fallback (a browser without `EventSource`) cannot host them: a lazy view is not hydrated, the client reports it (`djust:error`, `code: "view_unavailable"`), and an event from inside one is refused instead of being posted to the page.

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
from djust.decorators import event_handler, state

class CounterView(LiveView):
    template_name = 'counter.html'

    # Each POST rebuilds the view from its saved state.
    count = state(0)

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
