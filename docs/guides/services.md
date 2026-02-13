# Working with External Services

How to integrate AWS, REST APIs, Redis, and other external services with djust LiveViews.

---

## The Problem

djust LiveViews serialize their state to JSON between requests. This means everything stored as a public instance variable must be JSON-serializable. Service instances -- AWS clients, HTTP sessions, database connections, Redis clients -- are **not** serializable.

If you try to store a service instance in state:

```python
# WRONG: boto3 client is not JSON-serializable
class S3BrowserView(LiveView):
    template_name = "s3_browser.html"

    def mount(self, request, **kwargs):
        self.s3_client = boto3.client("s3")  # Will fail on next render
        self.buckets = []
```

You will see a `TypeError` during rendering:

```
TypeError: Object of type S3.Client is not JSON serializable
```

Or, if you have the `djust.V006` system check enabled, it will catch this at startup.

---

## Pattern 1: Helper Method (Recommended)

Create a private helper method that instantiates the service on demand. Since private methods (prefixed with `_`) are not serialized, this avoids the problem entirely.

```python
import boto3
from djust import LiveView, state
from djust.decorators import event_handler


class S3BrowserView(LiveView):
    template_name = "s3_browser.html"

    buckets = state(default=[])
    selected_bucket = state(default="")
    objects = state(default=[])

    def _get_s3_client(self):
        """Create a fresh S3 client for each request."""
        return boto3.client("s3")

    def mount(self, request, **kwargs):
        client = self._get_s3_client()
        response = client.list_buckets()
        self.buckets = [b["Name"] for b in response["Buckets"]]

    @event_handler()
    def select_bucket(self, bucket_name: str = "", **kwargs):
        self.selected_bucket = bucket_name
        client = self._get_s3_client()
        response = client.list_objects_v2(Bucket=bucket_name, MaxKeys=50)
        self.objects = [
            {"key": obj["Key"], "size": obj["Size"]}
            for obj in response.get("Contents", [])
        ]
```

**Why this works**: The `boto3.client()` call happens inside the method, creates a fresh client, and the client reference is never stored on `self` as a public attribute. Only the serializable results (`buckets`, `objects`) are stored in state.

---

## Pattern 2: Unmanaged Models

When you need to display data from an external API in a structured way, consider using Django models with `managed = False`. These are regular Django model instances that djust can serialize, but they are not backed by a database table.

```python
# models.py
from django.db import models


class ExternalProduct(models.Model):
    """Represents a product from the external catalog API."""
    name = models.CharField(max_length=200)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    sku = models.CharField(max_length=50)
    in_stock = models.BooleanField(default=True)

    class Meta:
        managed = False  # No database table created


# views.py
import httpx
from djust import LiveView, state
from djust.decorators import event_handler


class ProductCatalogView(LiveView):
    template_name = "catalog.html"

    search_query = state(default="")

    def _fetch_products(self, query=""):
        """Fetch products from external API and return as model instances."""
        response = httpx.get(
            "https://api.example.com/products",
            params={"q": query},
        )
        data = response.json()
        return [
            ExternalProduct(
                id=item["id"],
                name=item["name"],
                price=item["price"],
                sku=item["sku"],
                in_stock=item["available"],
            )
            for item in data["results"]
        ]

    def mount(self, request, **kwargs):
        self._products = self._fetch_products()

    @event_handler()
    def search(self, value: str = "", **kwargs):
        self.search_query = value
        self._products = self._fetch_products(query=value)

    def get_context_data(self, **kwargs):
        self.products = self._products  # JIT serialization
        return super().get_context_data(**kwargs)
```

This pattern combines the [JIT serialization pattern](../JIT_SERIALIZATION_PATTERN.md) with unmanaged models for a clean separation.

---

## Pattern 3: Dependency Injection

Pass services via `mount()` kwargs from your URL configuration. This is useful when you want to test views with mock services.

```python
# urls.py
from django.urls import path
from djust.routing import live_session
from myapp.views import DashboardView
from myapp.services import get_metrics_client


urlpatterns = [
    path(
        "dashboard/",
        live_session(DashboardView, metrics_client=get_metrics_client),
    ),
]


# views.py
from djust import LiveView, state
from djust.decorators import event_handler


class DashboardView(LiveView):
    template_name = "dashboard.html"

    metrics = state(default=[])

    def mount(self, request, metrics_client=None, **kwargs):
        self._metrics_client = metrics_client  # Private: not serialized
        if self._metrics_client:
            self.metrics = self._metrics_client.get_recent(limit=20)

    @event_handler()
    def refresh_metrics(self, **kwargs):
        if self._metrics_client:
            self.metrics = self._metrics_client.get_recent(limit=20)
```

Note that `self._metrics_client` uses the private prefix (`_`), so it is excluded from state serialization.

---

## What's Serializable

These types can be stored as public state variables:

| Type | Example |
|------|---------|
| Primitives | `str`, `int`, `float`, `bool`, `None` |
| Collections | `list`, `dict`, `tuple`, `set` |
| Django models | `Product.objects.get(pk=1)` |
| QuerySets | `Product.objects.filter(active=True)` |
| Dates/times | `datetime.date`, `datetime.datetime` |
| UUIDs | `uuid.UUID` |
| Decimals | `decimal.Decimal` |
| Nested structures | `{"items": [{"name": "Widget", "price": 9.99}]}` |

---

## What's Not Serializable

These types will raise `TypeError` if stored as public state:

| Type | Example | Use Helper Method Instead |
|------|---------|---------------------------|
| Service clients | `boto3.client("s3")` | `self._get_s3_client()` |
| HTTP sessions | `httpx.Client()`, `requests.Session()` | `self._get_http_client()` |
| Database connections | `psycopg2.connect(...)` | Use Django ORM |
| Redis clients | `redis.Redis()` | `self._get_redis()` |
| Open file handles | `open("data.csv")` | Read and close in method |
| Thread/process objects | `threading.Thread(...)` | Use Celery tasks |
| WebSocket connections | `websockets.connect(...)` | Use `push_event` |
| Generators | `(x for x in range(10))` | Convert to `list()` |
| Lambda functions | `lambda x: x + 1` | Use regular methods |

---

## Detection and Debugging

### System Check: V006

djust's system checks can detect service instances stored in state. Run:

```bash
python manage.py check --tag djust
```

If a LiveView stores something that looks like a service instance (class names containing "Service", "Client", "Session", "API", or "Connection"), you will see:

```
(djust.V006) MyView.api_client looks like a service instance stored in state.
    HINT: Service instances are not JSON-serializable. Use a helper method instead.
```

### Runtime Errors

If a non-serializable object makes it past the checks, you will see a `TypeError` at render time:

```
TypeError: Object of type S3.Client is not JSON serializable
```

The stack trace will point to the serialization step in `websocket.py`. The fix is always the same: move the service to a private variable or a helper method.

### Debugging Steps

1. Find the public attribute causing the error (look at the `TypeError` message)
2. Rename it with a `_` prefix to make it private: `self.client` -> `self._client`
3. Create a helper method if you need to re-create the service: `self._get_client()`
4. Store only the serializable results in public state

---

## Common Patterns

### AWS / Boto3

```python
class S3View(LiveView):
    template_name = "s3.html"

    files = state(default=[])

    def _s3(self):
        return boto3.client("s3")

    def mount(self, request, **kwargs):
        resp = self._s3().list_objects_v2(Bucket="my-bucket")
        self.files = [obj["Key"] for obj in resp.get("Contents", [])]

    @event_handler()
    def delete_file(self, key: str = "", **kwargs):
        self._s3().delete_object(Bucket="my-bucket", Key=key)
        self.files = [f for f in self.files if f != key]
```

### External REST APIs

```python
import httpx


class WeatherView(LiveView):
    template_name = "weather.html"

    city = state(default="London")
    forecast = state(default={})

    def _fetch_weather(self, city):
        resp = httpx.get(
            "https://api.weather.example.com/forecast",
            params={"city": city},
        )
        return resp.json()

    def mount(self, request, **kwargs):
        self.forecast = self._fetch_weather(self.city)

    @event_handler()
    def change_city(self, value: str = "", **kwargs):
        self.city = value
        self.forecast = self._fetch_weather(value)
```

### Redis Cache

```python
import redis


class LeaderboardView(LiveView):
    template_name = "leaderboard.html"

    scores = state(default=[])

    def _redis(self):
        return redis.Redis(host="localhost", port=6379, db=0)

    def mount(self, request, **kwargs):
        self._load_scores()

    def _load_scores(self):
        r = self._redis()
        raw = r.zrevrange("leaderboard", 0, 9, withscores=True)
        self.scores = [
            {"name": name.decode(), "score": int(score)}
            for name, score in raw
        ]

    @event_handler()
    def refresh(self, **kwargs):
        self._load_scores()
```

---

## See Also

- [Best Practices](BEST_PRACTICES.md) -- State management conventions
- [JIT Serialization Pattern](../JIT_SERIALIZATION_PATTERN.md) -- Private/public variable pattern
- [Error Codes](error-codes.md) -- V006 and other check details
