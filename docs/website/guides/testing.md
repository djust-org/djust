---
title: Testing LiveViews
layout: doc
---

# Testing LiveViews

djust ships `LiveViewTestClient` — a synchronous test client that lets you
exercise LiveView behavior end-to-end without standing up a real WebSocket
consumer. Inspired by Phoenix's `LiveViewTest`.

```python
from djust.testing import LiveViewTestClient

def test_counter_increments():
    client = LiveViewTestClient(CounterView).mount()
    client.send_event("increment")
    client.assert_state(count=1)
```

The client instantiates the view, calls `mount()`, and invokes handlers
directly — the same code path the WebSocket consumer runs, minus the
transport layer.

## Core methods

- `mount(via_websocket=True, **params)` — instantiate the view and run
  `mount()`. Returns `self`. The default simulates the WebSocket/SSE mount
  (the view gets the identity attributes a live connection sets); pass
  `via_websocket=False` to mount it the way the HTTP GET path does. The
  default mount runs the view's `connected()` hook after `mount()` and
  `handle_params()`, as a live mount does; an exception from it propagates,
  after the view's `disconnected()` has run. `via_websocket=False` does not run
  it, as the HTTP render does not.
- `send_event(name, raise_on_missing=True, **params)` — call a handler by
  name. Returns a dict with `state_before`, `state_after`, `duration_ms`,
  `success`, `error`. An unknown handler raises `NoHandlerFoundError` unless
  you pass `raise_on_missing=False`. The transports' event-security check runs
  first, as it does over WebSocket: a method without `@event_handler`, a
  server-push method or a private name
  raises `EventRejectedError` (a `NoHandlerFoundError`), or comes back as
  `success=False` with `code="event_rejected"` when you pass
  `raise_on_missing=False`. An exception raised *inside* the handler
  is not re-raised: it comes back as `success=False` with the message in
  `error`, so assert on `result["success"]`.
- A handler's `@rate_limit` applies, through the same per-caller store the
  transports use: an event past the budget does not run and comes back as
  `success=False` with `code="rate_limited"`. The budget is per caller (the
  user, else the anonymous session, else the client IP), so separate clients
  for the same user share it, and it carries over between tests in one
  process: call `djust.rate_limit.reset_handler_buckets()` in a fixture if
  your tests send a rate-limited handler more events than its `burst`. The
  client does not count refusals toward the connection's abuse disconnect
  (close code 4429) or apply the connection's global per-message limit.
  Its refusal has the HTTP fallback's shape (`code="rate_limited"`, error
  `"Rate limit exceeded"`) whatever `on_exceed` says. Over a WebSocket a
  refused event gets an error frame, `"Rate limit exceeded, event dropped"`,
  in both modes; only the default `on_exceed="disconnect"` also counts it
  toward the 4429 close, which `on_exceed="drop"` never does.
  `LiveViewSmokeTest` resets the budget for each fuzz payload.
- `assert_state(**expected)` — exact-match assertions on public view attrs.
- `assert_state_contains(**expected)` — checks `expected in actual`: a
  substring for strings, one element for lists, a key for dicts. It is not a
  subset match (`items=["a"]` fails against `["a", "b"]`).
- `get_state()` — return the view's state: its declared `state()` fields and
  the public attributes its own code sets or defines. Names the framework
  defines (`request`, `template`, `login_required`, ...) are left out, even
  when your code sets them (`page_title`, a `FormMixin` attribute): assert on
  those through `render()` or the attribute itself
  (`client.view_instance.page_title`).
- `render(engine="rust")` — render the view template and return the HTML.

## Phoenix-parity assertions (v0.5.1)

Eight additional methods cover the rest of the production test surface.

### `assert_push_event(event_name, params=None)`

Verify that some handler since `mount()` queued a matching client-bound push
event. The test client doesn't clear the queue between `send_event` calls, so a
push from an earlier event also satisfies the assertion.

```python
class SaveView(LiveView):
    @event_handler
    def save(self, **kwargs):
        self.push_event("flash", {"type": "success", "message": "saved"})

client = LiveViewTestClient(SaveView).mount()
client.send_event("save")
client.assert_push_event("flash", {"type": "success"})
# Payload match is SUBSET — extra keys in the stored payload are OK.
```

### `assert_patch(path=None, params=None)` / `assert_redirect(path=None, params=None)`

Verify the handler queued a navigation.

```python
class FilterView(LiveView):
    @event_handler
    def filter(self, **kwargs):
        self.live_patch(params={"page": 2, "category": "books"}, path="/items/")

client = LiveViewTestClient(FilterView).mount()
client.send_event("filter")
client.assert_patch("/items/", {"page": 2})  # subset match on params
```

`assert_redirect` has the same signature and matches `live_redirect` calls.

### `render_async()`

Drain pending `start_async` / `assign_async` tasks synchronously so tests
can assert on their results.

```python
class ReportView(LiveView):
    @event_handler
    def generate(self, **kwargs):
        self.generating = True
        self.start_async(self._build_report)

    def _build_report(self):
        try:
            self.report = build(...)
        except Exception as exc:
            # Always guard async callbacks — a crash that leaves `generating`
            # True will leave the client stuck in a loading state.
            self.error = str(exc)
        finally:
            self.generating = False

client = LiveViewTestClient(ReportView).mount()
client.send_event("generate")
client.assert_state(generating=True)      # pre-drain
client.render_async()
client.assert_state(generating=False)     # post-drain
assert client.view_instance.report is not None
```

`render_async()` runs exactly one batch of pending tasks; callbacks that
queue more work require a second call. Matches production consumer semantics.

### `follow_redirect()`

After a `live_redirect`, mount the destination view and return a new client
rooted on it. The destination is mounted with the redirect's `params` plus the
URL kwargs of the resolved path, and with the same `user` the original client
was built with. Nothing else carries over: logging a user in inside a handler
does not log the test client in.

```python
class LoginView(LiveView):
    @event_handler
    def submit(self, email="", password="", **kwargs):
        if check_credentials(email, password):
            self.live_redirect("/dashboard/", params={"welcome": "1"})

class DashboardView(LiveView):  # routed at /dashboard/
    def mount(self, request, welcome="", **kwargs):
        self.welcome = welcome

    def get_context_data(self, **kwargs):
        return {**super().get_context_data(**kwargs), "welcome": self.welcome}

login_client = LiveViewTestClient(LoginView).mount()
login_client.send_event("submit", email="a@b.com", password="x")
dashboard_client = login_client.follow_redirect()
dashboard_client.assert_state(welcome="1")
```

To test a destination that depends on `request.user`, build the first client
with `LiveViewTestClient(LoginView, user=some_user)`; `follow_redirect()`
passes that user on.

### `assert_stream_insert(stream_name, item=None)`

Verify a stream operation was queued.

```python
class ChatView(LiveView):
    def mount(self, request, **kwargs):
        self.stream("messages", [])

    @event_handler
    def post(self, text="", **kwargs):
        from uuid import uuid4
        self.stream_insert("messages", {"id": uuid4().hex, "text": text})

client = LiveViewTestClient(ChatView).mount()
client.send_event("post", text="hello")
client.assert_stream_insert("messages", {"text": "hello"})
```

Dict items are matched by subset; other types by equality.

### `trigger_info(message)`

Synthetically deliver a `handle_info` message (the hook `pg_notify` uses).
Tests pubsub / database-notification handlers without real backend wiring.
The test client doesn't need a real subscription.

```python
class OrdersView(LiveView):
    count = state(0)

    def mount(self, request, **kwargs):
        self.listen("orders")

    def handle_info(self, message):
        if message.get("type") == "db_notify":
            self.count += 1

client = LiveViewTestClient(OrdersView).mount()
client.trigger_info({"type": "db_notify", "channel": "orders", "payload": {"event": "save"}})
client.assert_state(count=1)
```

### `assert_http_ws_djid_parity(**mount_kwargs)`

Asserts that the HTTP-GET render path and the WebSocket-mount render path assign
the same `dj-id` baseline for a view. In production the initial HTTP `GET`
renders the DOM the browser holds and establishes a VDOM baseline, while a
*separate* instance handles the WebSocket mount and establishes its own
baseline; the first WS event ships patches keyed by `dj-id`. If the two
baselines diverge, those patches miss `dj-id` resolution and fall back to path
traversal — a `getNodeByPath → null` patch failure.

```python
def test_dashboard_djid_parity():
    LiveViewTestClient(ProjectDashboardView).assert_http_ws_djid_parity(slug="acme")
```

Reach for this when a view renders differently on first paint vs. reconnect, or
when you suspect a `1/N patches failed` console warning traces to an HTTP-vs-WS
`dj-id` mismatch. The harness builds two independent instances and compares the
ordered `dj-id` sequence in the `dj-root` subtree; it returns that sequence so
you can make further assertions.

## Real sessions and transports

`LiveViewTestClient` calls the view directly. To test what a browser sees, go
through a transport: the page GET, then the HTTP fallback POST, a WebSocket or
an SSE stream. An explicit view (`exposure_policy = "explicit"`, the 2.0
default) binds its state to a saved server session when it mounts, and reads
that session and its user again on every turn. A test with a stub user, a bare
consumer or a `RequestFactory` request without a saved session is refused at
mount ("State binding requires bounded nonempty identifiers"), or every event
is denied. `djust.testing` gives a test what a browser has:

- `open_live_page(url, *, user=None, client=None, **extra) -> LivePage` does the
  page GET with a `django.test.Client`, through your project's middleware and
  URLconf. `user` is logged in first (`client.force_login`). `extra` holds
  request extras such as `HTTP_HOST` (a subdomain tenant) or
  `HTTP_X_TENANT_ID` (a header tenant), and every transport opened from the
  page sends them too. Each call is a new page load and so a new tab; pass
  `client=page.client` to open a second tab in the same browser session.
- `LivePage` has the page's `tab` id, `view_path` (its `dj-view`), `session`,
  `html` and `response`, and three ways to reach the view:
  - `post_event(event, params=None, **extra)` sends the HTTP fallback POST
    (`X-Djust-Event`, the page's `X-Djust-Tab`, its CSRF token) and returns
    the response.
  - `await connect(view_path=None, *, params=None, application=None,
    path="/ws/live/", timeout=10.0) -> LiveSocket` connects a
    `WebsocketCommunicator` with the page's cookies and `Origin`, and mounts
    the view on the page's tab. By default it uses a bare `LiveViewConsumer`
    whose scope gets the page's session and user. Pass your project's
    `asgi.application` to go through its own middleware instead.
  - `await connect_sse(view_path=None, *, params=None, timeout=10.0) ->
    LiveSSE` opens djust's SSE stream for the page. Your project does not
    need to route the SSE URLs.
- `LiveSocket` and `LiveSSE` share one interface. `mount` is the mount answer
  (`{"type": "mount", ...}` on success). `await send_event(event, params=None,
  **fields)` returns the frame that answers the event: it carries the event's
  `ref`, or it is an `error`. `await receive(types=None, *, timeout=None)`
  returns the next frame of those types, and `frames` lists every frame
  received. Both are async context managers: leaving the block disconnects
  the socket, or closes the stream and runs its teardown.
- `live_session(user=None, *, store=None)` returns a new saved server
  session, logged in as `user` when it is a saved user.
  `with_live_session(target, user=...)` gives a hand-built
  `WebsocketCommunicator`, ASGI scope or request a saved session and a user.
  It keeps a saved session the target already has. Without a user it keeps
  the target's own, or uses `AnonymousUser`.

These need no djust setting beyond what `djust new` writes: `SessionMiddleware`
and `AuthenticationMiddleware`, and a server-side `SESSION_ENGINE`. The default
database engine works. Sessions are database rows. A socket or SSE test runs
the view on other threads, so with pytest-django mark it
`@pytest.mark.django_db(transaction=True)`. With Django's own runner, use
`TransactionTestCase`: in `TestCase` the mount works, but the event turn cannot
see the session row inside the test's transaction. The fallback POST works
under either runner's plain database access. The async examples below
use pytest-asyncio's `@pytest.mark.asyncio`.

Over the HTTP fallback:

```python
import json

import pytest
from django.contrib.auth import get_user_model

from djust.testing import open_live_page


@pytest.mark.django_db
def test_counter_over_the_fallback():
    user = get_user_model().objects.create_user(username="ada", password="pw")
    page = open_live_page("/counter/", user=user)
    response = page.post_event("increment")
    assert response.status_code == 200
    assert "count=1" in json.loads(response.content)["html"]
```

Over a WebSocket:

```python
import pytest
from asgiref.sync import sync_to_async

from djust.testing import open_live_page


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_counter_over_a_socket():
    page = await sync_to_async(open_live_page)("/counter/")
    async with await page.connect() as socket:
        assert socket.mount["type"] == "mount", socket.mount
        answer = await socket.send_event("increment")
        assert answer["type"] in ("patch", "html_update"), answer
```

Over SSE:

```python
import pytest
from asgiref.sync import sync_to_async

from djust.testing import open_live_page


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_counter_over_sse():
    page = await sync_to_async(open_live_page)("/counter/")
    async with await page.connect_sse() as stream:
        assert stream.mount["type"] == "mount", stream.mount
        answer = await stream.send_event("increment")
        assert answer["type"] in ("patch", "html_update"), answer
```

A few variations:

```python
import pytest
from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator

from djust.testing import LiveSocket, open_live_page, with_live_session
from djust.websocket import LiveViewConsumer


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_variations():
    # A header tenant: the page and every transport opened from it send it.
    page = await sync_to_async(open_live_page)("/counter/", HTTP_X_TENANT_ID="acme")

    # Two tabs of one browser session keep their own state.
    other_tab = await sync_to_async(open_live_page)("/counter/", client=page.client)
    assert other_tab.tab != page.tab

    # Your project's own ASGI stack (origin validator, auth middleware, router).
    from myproject.asgi import application

    async with await page.connect(application=application) as socket:
        await socket.send_event("increment")

    # A hand-built communicator, with no page GET.
    communicator = with_live_session(
        WebsocketCommunicator(
            LiveViewConsumer.as_asgi(),
            "/ws/live/",
            headers=[(b"host", b"testserver"), (b"origin", b"http://testserver")],
        )
    )
    assert (await communicator.connect())[0]
    await communicator.send_json_to({"type": "mount", "view": "myapp.views.Counter", "url": "/"})
    socket = LiveSocket(communicator)
    assert (await socket.receive(("mount", "error")))["type"] == "mount"
    await socket.disconnect()
```

## Putting it together

A typical integration test combines several of these:

```python
def test_create_item_flow():
    client = LiveViewTestClient(InventoryView).mount()
    client.send_event("create", name="widget")
    client.assert_stream_insert("items", {"name": "widget"})
    client.assert_push_event("flash", {"type": "success"})
    client.assert_patch("/items/", {"highlight": "widget"})
```

## When to reach for each tool

- **Unit test a handler** → `send_event` + `assert_state`
- **Test a push-event / flash** → `assert_push_event`
- **Test navigation** → `assert_patch` / `assert_redirect` / `follow_redirect`
- **Test async workflow** → `render_async`
- **Test streaming/append** → `assert_stream_insert`
- **Test pubsub / db_notify reaction** → `trigger_info`
- **Test rendered HTML** → `render()` and assert on the returned string
- **Snapshot test rendered HTML** → inherit `SnapshotTestMixin` (separate)
- **Test a view through a real transport** (fallback POST, WebSocket, SSE)
  with a real session, user, tenant or tab → `open_live_page` and its
  `LivePage`; `with_live_session` for a hand-built communicator or request

For end-to-end browser tests, use Playwright against `examples/demo_project`
(or your own Django app running djust).
