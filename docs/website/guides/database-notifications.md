---
title: Database Change Notifications
slug: database-notifications
level: advanced
order: 11
---

# Database Change Notifications

Subscribe LiveViews to PostgreSQL `LISTEN/NOTIFY` so database changes push
real-time updates to connected users — without writing any explicit
pub/sub wiring.

Shipped in **v0.5.0** (`djust.db.notify_on_save`, `djust.db.send_pg_notify`,
`NotificationMixin.listen`).

## The 30-second version

```python
from django.db import models
from djust import LiveView
from djust.db import notify_on_save

@notify_on_save                         # default channel: "shop_order"
class Order(models.Model):
    status = models.CharField(max_length=20)

class OrderDashboard(LiveView):
    template_name = "dashboard.html"

    def mount(self, request, **kwargs):
        self.orders = list(Order.objects.filter(status="pending"))
        self.listen("shop_order")       # subscribe to the NOTIFY channel

    def handle_info(self, message):
        if message["type"] == "db_notify":
            self.orders = list(Order.objects.filter(status="pending"))
```

Create / update / delete an `Order` from anywhere — a Django admin, a
Celery task, a management command, even a `psql` shell — and every user
viewing `OrderDashboard` sees fresh data within a few milliseconds.

## How it works

```
┌─────────────┐   post_save    ┌────────────────┐  NOTIFY   ┌─────────────┐
│ Model.save()├───────────────►│send_pg_notify()├──────────►│  Postgres   │
└─────────────┘  (signal)      └────────────────┘  SQL      │  LISTEN ch. │
                                                            └──────┬──────┘
                                                                   │
                                                                   ▼
                         group_send      ┌─────────────────────────────────┐
┌─────────────┐◄────── ───────────────── │   PostgresNotifyListener        │
│ LiveView's  │     "djust_db_notify_X"  │  (async task, dedicated conn)   │
│ handle_info │                          └─────────────────────────────────┘
└─────────────┘
```

1. `@notify_on_save` wires Django `post_save` / `post_delete` signals so
   every `save()` and `delete()` emits `NOTIFY <channel>, '<json>'`.
2. A **process-wide** `PostgresNotifyListener` runs one dedicated
   `psycopg.AsyncConnection` and does `async for notify in
   conn.notifies():`. On each NOTIFY it calls
   `channel_layer.group_send("djust_db_notify_<channel>", ...)`.
3. `self.listen(channel)` in `mount()` joins the view's WebSocket
   consumer to that Channels group.
4. The consumer's `db_notify` handler calls `handle_info(message)` and
   re-renders — VDOM patches stream down to the browser.

The only code you write is the decorator, `self.listen()`, and
`handle_info()`.

## `@notify_on_save`

```python
from djust.db import notify_on_save

# Default channel: "{app_label}_{model_name}"
@notify_on_save
class Order(models.Model): ...

# Explicit channel (keyword)
@notify_on_save(channel="orders")
class Order(models.Model): ...

# Explicit channel (positional shorthand)
@notify_on_save("orders")
class Order(models.Model): ...
```

**Payload shape:**

```json
{"pk": 42, "event": "save", "model": "shop.Order"}
```

Minimal by design. Postgres caps NOTIFY payloads at **8000 bytes**;
receivers re-fetch full state via the ORM when they need it.

**Channel name rules:** `^[a-z_][a-z0-9_]{0,62}$`. Uppercase, hyphens,
dots, and quotes are rejected at decorator-registration time. This is
security-critical — Postgres `NOTIFY` doesn't accept bind parameters for
the channel name, so the regex is the only defense against SQL
injection.

## `self.listen(channel)`

```python
def mount(self, request, **kwargs):
    self.listen("orders")
    self.listen("users")       # subscribe to multiple channels
    self.listen("orders")      # duplicate subscriptions are idempotent
```

Raises `ValueError` for bad channel names.

Raises `djust.db.DatabaseNotificationNotSupported` when the configured DB
backend isn't PostgreSQL or `psycopg` isn't installed. The decorator
`@notify_on_save` itself degrades gracefully — it becomes a no-op with a
debug log — so the same model code works in sqlite test suites.

## `handle_info(message)`

```python
def handle_info(self, message):
    if message["type"] == "db_notify":
        channel = message["channel"]        # e.g. "orders"
        payload = message["payload"]        # {"pk": 42, "event": "save", ...}
        if channel == "orders":
            self.orders = list(Order.objects.filter(status="pending"))
        elif channel == "users":
            self.user_count = User.objects.count()
```

Default implementation is a no-op. Override to react.

Re-rendering happens automatically after `handle_info` returns — the
consumer calls `render_with_diff` and pushes VDOM patches. Setting
`self._skip_render = True` inside `handle_info` suppresses the render
(useful when the notification doesn't affect visible state).

## Firing NOTIFYs from other places

Anywhere you can reach a Django DB connection, you can broadcast:

```python
# Celery task
from celery import shared_task
from djust.db import send_pg_notify

@shared_task
def nightly_report_ready(report_id):
    send_pg_notify("reports", {"pk": report_id, "event": "generated"})
```

```python
# Management command
from django.core.management.base import BaseCommand
from djust.db import send_pg_notify

class Command(BaseCommand):
    def handle(self, *args, **opts):
        send_pg_notify("system", {"event": "maintenance_done"})
```

```sql
-- psql / DB trigger
NOTIFY orders, '{"pk": 42, "event": "save"}';
```

`send_pg_notify` is a no-op on non-Postgres backends (debug-logged) —
your tests on SQLite don't need conditional imports.

## Common patterns

### Reactive dashboards

Every connected admin sees order status changes in real time — no
polling, no manual broadcasting.

```python
@notify_on_save(channel="orders")
class Order(models.Model):
    status = models.CharField(max_length=20)

class OrderDashboard(LoginRequiredMixin, LiveView):
    template_name = "admin/orders.html"

    def mount(self, request, **kwargs):
        self.listen("orders")
        self._refresh()

    def handle_info(self, message):
        self._refresh()

    def _refresh(self):
        self.by_status = {
            status: Order.objects.filter(status=status).count()
            for status in ("pending", "paid", "shipped")
        }
```

### Collaborative editing

```python
@notify_on_save(channel="document")
class Document(models.Model):
    body = models.TextField()

class DocumentView(LiveView):
    def mount(self, request, doc_id, **kwargs):
        self._doc_id = doc_id
        self.listen("document")
        self.doc = Document.objects.get(pk=doc_id)

    def handle_info(self, message):
        if message["payload"].get("pk") == self._doc_id:
            self.doc.refresh_from_db()
```

### Admin → user broadcast

A support agent changes an order's status in the Django admin; the
customer's page updates instantly with no extra plumbing.

```python
@notify_on_save(channel="orders")
class Order(models.Model): ...

class CustomerOrderView(LiveView):
    def mount(self, request, order_id, **kwargs):
        self._order_id = order_id
        self.listen("orders")
        self.order = Order.objects.get(pk=order_id)

    def handle_info(self, message):
        if message["payload"].get("pk") == self._order_id:
            self.order.refresh_from_db()
```

## Limitations & gotchas

### Missed notifications across disconnects

PostgreSQL discards queued NOTIFYs when the `LISTEN` connection drops.
If the listener's TCP connection to the database fails, the listener
automatically reconnects (1-second backoff) and re-issues LISTEN for
every channel — but **notifications emitted during the drop window are
lost**.

Recovery is automatic for the WebSocket side: `mount()` re-runs on WS
reconnect, which re-fetches state from the DB. For server-side drops in
the listener connection, any state that changed while the listener was
down gets picked up on the next NOTIFY after reconnect, but intervening
changes are silent.

**Mitigation:** if your dashboard must never miss an update, combine
NOTIFY with a periodic `handle_tick()` that refreshes from the DB. NOTIFY
handles "instant" updates; `handle_tick()` serves as a catch-up for any
missed events.

### 8000-byte payload cap

Postgres's `NOTIFY` payload limit is 8000 bytes. Keep payloads minimal.
Full row snapshots often exceed that; re-fetching via the ORM is the
intended pattern.

### PostgreSQL only

`LISTEN/NOTIFY` is Postgres-specific. SQLite, MySQL, and Oracle are not
supported. `@notify_on_save` is a silent no-op on non-Postgres backends
so you can develop on SQLite without conditional imports, but
`self.listen()` raises `DatabaseNotificationNotSupported`.

### One listener per process

Each Django process that runs WebSocket consumers starts its own
`PostgresNotifyListener`. This scales linearly with workers — fine for
typical deployments (2-8 Channels workers), but if you run hundreds of
processes you'll hit Postgres's `max_connections` ceiling. The listener
uses a **dedicated** connection (not the Django connection pool), so
pgbouncer transaction pooling doesn't interfere.

### Connection pooler caveats

`LISTEN/NOTIFY` requires session-level connections. If you're behind
pgbouncer in `transaction` mode, point the listener directly at Postgres
(bypass the pooler) or use `session` mode for the djust listener
connection. The listener reads DSN fields from `settings.DATABASES
["default"]`; override `HOST` / `PORT` there if you need a direct
route.

### Security: untrusted NOTIFY sources

If Postgres is shared with other applications, anyone with DB access can
emit `NOTIFY orders, '{"pk": 999999, "event": "delete"}'`. Always
validate payloads in `handle_info` — don't blindly trust `pk` values or
perform destructive actions based on channel messages. Treat NOTIFYs as
"something changed, re-fetch via ORM" hints rather than authoritative
state.

## Testing

Unit tests can mock the listener:

```python
from unittest.mock import patch, AsyncMock

with patch("djust.db.notifications.PostgresNotifyListener") as mock:
    mock.instance.return_value.ensure_listening = AsyncMock()
    view.mount(request)
    assert view._listen_channels == {"orders"}
```

Integration tests that want a live round trip can use the pattern from
`tests/integration/test_pg_notify_roundtrip.py`:

```python
import pytest
from django.conf import settings

pytestmark = pytest.mark.skipif(
    not settings.DATABASES["default"]["ENGINE"].endswith("postgresql"),
    reason="needs postgresql",
)
```

## Related APIs

- `djust.push_to_view` — lower-level broadcast that skips the DB and goes
  straight through Channels. Use when you have direct access to the
  process state.
- `djust.presence` — user-presence tracking via Channels groups.
- `LiveView.handle_tick()` — periodic polling as a NOTIFY fallback.
