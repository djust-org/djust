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

- `mount(**params)` — instantiate the view and run `mount()`. Returns `self`.
- `send_event(name, **params)` — call a handler by name. Returns a dict with
  `state_before`, `state_after`, `duration_ms`, `success`, `error`.
- `assert_state(**expected)` — exact-match assertions on public view attrs.
- `assert_state_contains(**expected)` — substring match (strings) or subset
  match (dicts/lists).
- `get_state()` — return a snapshot of public attrs.
- `render(engine="rust")` — render the view template and return the HTML.

## Phoenix-parity assertions (v0.5.1)

Seven additional methods cover the rest of the production test surface.

### `assert_push_event(event_name, params=None)`

Verify the last `send_event` queued a client-bound push event.

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
        self.report = build(...)
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
rooted on it.

```python
class LoginView(LiveView):
    @event_handler
    def submit(self, email="", password="", **kwargs):
        if authenticate(email, password):
            self.live_redirect("/dashboard/")

login_client = LiveViewTestClient(LoginView).mount()
login_client.send_event("submit", email="a@b.com", password="x")
dashboard_client = login_client.follow_redirect()
dashboard_client.assert_state(user_email="a@b.com")
```

### `assert_stream_insert(stream_name, item=None)`

Verify a stream operation was queued.

```python
class ChatView(LiveView):
    def mount(self, request, **kwargs):
        self.stream("messages", [])

    @event_handler
    def post(self, text="", **kwargs):
        self.stream_insert("messages", {"id": next_id(), "text": text})

client = LiveViewTestClient(ChatView).mount()
client.send_event("post", text="hello")
client.assert_stream_insert("messages", {"text": "hello"})
```

Dict items are matched by subset; other types by equality.

### `trigger_info(message)`

Synthetically deliver a `handle_info` message (the hook `pg_notify` uses).
Tests pubsub / database-notification handlers without real backend wiring.

```python
class OrdersView(LiveView):
    def mount(self, request, **kwargs):
        self.listen("orders")
        self.count = 0

    def handle_info(self, message):
        if message.get("type") == "db_notify":
            self.count += 1

client = LiveViewTestClient(OrdersView).mount()
client.trigger_info({"type": "db_notify", "channel": "orders", "payload": {"event": "save"}})
client.assert_state(count=1)
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

For end-to-end browser tests, use Playwright against `examples/demo_project`
(or your own Django app running djust).
