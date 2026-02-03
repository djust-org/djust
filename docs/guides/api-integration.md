# API Integration Guide

djust provides comprehensive integration with GraphQL, REST APIs, and Django REST Framework (DRF), enabling seamless real-time data synchronization in your LiveViews.

## Quick Start

```python
# REST API integration
from djust import LiveView
from djust.integrations import RESTMixin

class ProductView(RESTMixin, LiveView):
    template_name = "products.html"
    api_base = "/api/v1"
    
    async def mount(self, request, **kwargs):
        self.products = await self.api_get("/products/")
```

```python
# GraphQL integration
from djust import LiveView
from djust.integrations import GraphQLMixin

class DashboardView(GraphQLMixin, LiveView):
    template_name = "dashboard.html"
    graphql_endpoint = "ws://localhost:8000/graphql/"
    subscriptions = ['orderUpdated']
    
    def on_subscription(self, name, data):
        if name == 'orderUpdated':
            self.orders.append(data['orderUpdated'])
```

## Installation

The API integration mixins require `httpx` for HTTP requests and optionally `websockets` for GraphQL subscriptions:

```bash
pip install httpx websockets
```

## REST API Integration

### Basic Usage

The `RESTMixin` provides methods for interacting with REST APIs:

```python
from djust import LiveView
from djust.integrations import RESTMixin
from djust.decorators import event_handler

class ProductView(RESTMixin, LiveView):
    template_name = "products.html"
    api_base = "/api/v1"
    
    async def mount(self, request, **kwargs):
        self.products = await self.api_get("/products/")
        self.loading = False
        self.error = None
    
    @event_handler
    async def refresh(self):
        """Refresh the product list."""
        self.loading = True
        self.products = await self.api_get("/products/")
        self.loading = False
    
    @event_handler
    async def add_product(self, name: str, price: float):
        """Create a new product."""
        try:
            new_product = await self.api_post("/products/", {
                "name": name,
                "price": price,
            })
            self.products.append(new_product)
        except Exception as e:
            self.error = str(e)
    
    @event_handler
    async def delete_product(self, product_id: int):
        """Delete a product."""
        await self.api_delete(f"/products/{product_id}/")
        self.products = [p for p in self.products if p['id'] != product_id]
```

### Configuration Options

| Attribute | Type | Default | Description |
|-----------|------|---------|-------------|
| `api_base` | str | `""` | Base URL for API requests |
| `api_headers` | dict | `{}` | Default headers for all requests |
| `api_timeout` | float | `30.0` | Request timeout in seconds |
| `polling_interval` | int | `0` | Polling interval in ms (0 = disabled) |
| `polling_endpoints` | list | `[]` | Endpoints to poll |
| `optimistic_updates` | bool | `True` | Enable optimistic UI updates |

### Dynamic Headers (Authentication)

Override `get_api_headers()` for dynamic authentication:

```python
class AuthenticatedView(RESTMixin, LiveView):
    api_base = "https://api.example.com"
    
    def get_api_headers(self):
        """Return headers including user's auth token."""
        return {
            "Authorization": f"Bearer {self.request.user.api_token}",
            "X-Tenant-ID": str(self.request.user.tenant_id),
        }
```

### HTTP Methods

```python
# GET request
data = await self.api_get("/endpoint/", params={"page": 1})

# POST request
result = await self.api_post("/endpoint/", {"field": "value"})

# PUT request (full update)
result = await self.api_put("/endpoint/1/", {"field": "new_value"})

# PATCH request (partial update)
result = await self.api_patch("/endpoint/1/", {"field": "new_value"})

# DELETE request
await self.api_delete("/endpoint/1/")
```

### Error Handling

Handle API errors gracefully:

```python
from djust.integrations import RESTMixin, APIError

class ProductView(RESTMixin, LiveView):
    async def on_api_error(self, error: APIError):
        """Called when any API request fails."""
        self.error_message = error.message
        
        # Push error notification to client
        self.push_event("toast", {
            "type": "error",
            "message": f"API Error ({error.status_code}): {error.message}"
        })
    
    @event_handler
    async def fetch_data(self):
        try:
            self.data = await self.api_get("/data/")
        except APIError as e:
            if e.status_code == 404:
                self.data = []  # Handle not found gracefully
            else:
                raise  # Re-raise other errors
```

### Polling

Automatically refresh data at intervals:

```python
class StockView(RESTMixin, LiveView):
    template_name = "stocks.html"
    api_base = "/api/v1"
    polling_interval = 5000  # Poll every 5 seconds
    polling_endpoints = ["/stocks/", "/prices/"]
    
    async def mount(self, request, **kwargs):
        self.stocks = []
        self.prices = {}
        await self.start_polling()
    
    async def on_poll(self, endpoint: str, data):
        """Called when polling returns new data."""
        if endpoint == "/stocks/":
            self.stocks = data
        elif endpoint == "/prices/":
            self.prices = data
    
    async def unmount(self):
        """Clean up when view disconnects."""
        await self.stop_polling()
```

### Optimistic Updates

Update the UI immediately, then sync with the server:

```python
@event_handler
async def add_item(self, name: str):
    # Create optimistic item
    temp_item = {"id": "temp", "name": name, "status": "pending"}
    
    await self.api_optimistic(
        "/items/",
        "POST",
        {"name": name},
        optimistic_value=[*self.items, temp_item],
        rollback_value=self.items.copy(),
        target_attr="items",
    )
    
    # On success, items will contain the real server response
    # On failure, items will be rolled back automatically
```

## GraphQL Integration

### Basic Usage

```python
from djust import LiveView
from djust.integrations import GraphQLMixin

class ProductView(GraphQLMixin, LiveView):
    template_name = "products.html"
    graphql_endpoint = "http://localhost:8000/graphql/"
    
    async def mount(self, request, **kwargs):
        result = await self.graphql_query('''
            query {
                products {
                    id
                    name
                    price
                }
            }
        ''')
        self.products = result.get('products', [])
```

### Queries with Variables

```python
async def get_product(self, product_id: str):
    result = await self.graphql_query('''
        query GetProduct($id: ID!) {
            product(id: $id) {
                id
                name
                price
                description
                stock
            }
        }
    ''', variables={'id': product_id})
    
    return result.get('product')
```

### Mutations

```python
@event_handler
async def create_product(self, name: str, price: float):
    result = await self.graphql_mutate('''
        mutation CreateProduct($name: String!, $price: Float!) {
            createProduct(input: {name: $name, price: $price}) {
                product {
                    id
                    name
                    price
                }
                errors {
                    field
                    message
                }
            }
        }
    ''', variables={'name': name, 'price': price})
    
    if result.get('createProduct', {}).get('product'):
        self.products.append(result['createProduct']['product'])
```

### Subscriptions

Real-time updates via GraphQL subscriptions:

```python
class DashboardView(GraphQLMixin, LiveView):
    template_name = "dashboard.html"
    
    graphql_endpoint = "ws://localhost:8000/graphql/"
    subscriptions = ['orderUpdated', 'inventoryChanged']
    subscription_queries = {
        'orderUpdated': '''
            subscription {
                orderUpdated {
                    id
                    status
                    total
                    customer {
                        name
                    }
                }
            }
        ''',
        'inventoryChanged': '''
            subscription {
                inventoryChanged {
                    productId
                    quantity
                }
            }
        ''',
    }
    auto_subscribe = True  # Start subscriptions on mount
    
    async def mount(self, request, **kwargs):
        self.orders = []
        self.inventory = {}
        # Subscriptions auto-start because auto_subscribe=True
    
    async def on_subscription(self, name: str, data: dict):
        """Called when subscription data is received."""
        if name == 'orderUpdated':
            order = data.get('orderUpdated')
            self.orders = [
                order if o['id'] == order['id'] else o
                for o in self.orders
            ]
            # Or append new orders
        
        elif name == 'inventoryChanged':
            item = data.get('inventoryChanged')
            self.inventory[item['productId']] = item['quantity']
    
    async def on_subscription_error(self, name: str, error: Exception):
        """Called when a subscription error occurs."""
        self.push_event("error", {
            "message": f"Subscription {name} failed: {error}"
        })
    
    async def unmount(self):
        """Clean up subscriptions."""
        await self.stop_subscriptions()
```

### Manual Subscription Control

```python
class DynamicSubscriptionView(GraphQLMixin, LiveView):
    auto_subscribe = False  # Don't auto-start
    
    @event_handler
    async def watch_order(self, order_id: str):
        """Start watching a specific order."""
        await self.subscribe(
            f'order_{order_id}',
            query='''
                subscription WatchOrder($id: ID!) {
                    orderUpdated(id: $id) {
                        id
                        status
                    }
                }
            ''',
            variables={'id': order_id},
        )
    
    @event_handler
    async def stop_watching(self, order_id: str):
        """Stop watching an order."""
        await self.unsubscribe(f'order_{order_id}')
```

## Django REST Framework Integration

### DRFSerializerMixin (Serializers Only)

Use DRF serializers without full ViewSet integration:

```python
from djust import LiveView
from djust.integrations import DRFSerializerMixin
from myapp.serializers import ProductSerializer

class ProductView(DRFSerializerMixin, LiveView):
    template_name = "products.html"
    serializer_class = ProductSerializer
    
    async def mount(self, request, **kwargs):
        products = Product.objects.filter(active=True)
        self.products = self.serialize_many(products)
    
    @event_handler
    async def validate_product(self, **data):
        """Validate product data before saving."""
        errors = self.validate(data)
        if errors:
            self.form_errors = errors
        else:
            self.form_errors = {}
            self.validated_data = self.get_validated_data(data)
```

### DRFMixin (Full Integration)

Complete DRF integration with CRUD operations:

```python
from djust import LiveView
from djust.integrations import DRFMixin
from myapp.serializers import ProductSerializer
from myapp.models import Product

class ProductView(DRFMixin, LiveView):
    template_name = "products.html"
    serializer_class = ProductSerializer
    queryset = Product.objects.all()
    ordering = ['-created_at']
    
    async def mount(self, request, **kwargs):
        self.products = self.get_serialized_list()
        self.current_product = None
        self.form_errors = {}
    
    @event_handler
    async def select_product(self, pk: int):
        """Load a product for editing."""
        self.current_product = self.get_serialized_object(pk)
    
    @event_handler
    async def create_product(self, **data):
        """Create a new product."""
        product = self.create(data)
        if product:
            self.products = self.get_serialized_list()
            self.push_event("toast", {"message": "Product created!"})
    
    @event_handler
    async def update_product(self, pk: int, **data):
        """Update an existing product."""
        product = self.update(pk, data)
        if product:
            self.products = self.get_serialized_list()
            self.current_product = self.serialize(product)
    
    @event_handler
    async def delete_product(self, pk: int):
        """Delete a product."""
        if self.delete(pk):
            self.products = self.get_serialized_list()
            if self.current_product and self.current_product['id'] == pk:
                self.current_product = None
```

### Dynamic Querysets

Filter data based on the current user:

```python
class UserProductView(DRFMixin, LiveView):
    serializer_class = ProductSerializer
    
    def get_queryset(self):
        """Return only products owned by the current user."""
        return Product.objects.filter(
            owner=self.request.user,
            active=True,
        )
    
    def get_serializer_class(self):
        """Use different serializers based on context."""
        if hasattr(self, '_creating') and self._creating:
            return ProductCreateSerializer
        return ProductSerializer
```

### ViewSet Delegation

Use existing DRF ViewSets:

```python
from rest_framework.viewsets import ModelViewSet
from djust.integrations import DRFMixin

class ProductViewSet(ModelViewSet):
    serializer_class = ProductSerializer
    queryset = Product.objects.all()
    permission_classes = [IsAuthenticated]

class ProductView(DRFMixin, LiveView):
    viewset_class = ProductViewSet
    
    async def mount(self, request, **kwargs):
        self.products = await self.viewset_list()
    
    @event_handler
    async def create(self, **data):
        product = await self.viewset_create(data)
        if product:
            self.products.append(product)
    
    @event_handler
    async def update(self, pk: int, **data):
        product = await self.viewset_update(pk, data)
        if product:
            # Update in list
            for i, p in enumerate(self.products):
                if p['id'] == pk:
                    self.products[i] = product
                    break
```

## Real-time API Sync Directive

The `dj-api-sync` directive enables automatic client-side API polling:

```html
<div dj-api-sync="/api/products/" 
     dj-api-interval="5000"
     dj-api-target="products"
     dj-api-on-error="handle_api_error">
    {% for product in products %}
        <div class="product">{{ product.name }}</div>
    {% endfor %}
</div>
```

### Attributes

| Attribute | Description | Default |
|-----------|-------------|---------|
| `dj-api-sync` | API endpoint URL (required) | - |
| `dj-api-interval` | Polling interval in ms | `10000` |
| `dj-api-target` | Server attribute to update | - |
| `dj-api-method` | HTTP method | `GET` |
| `dj-api-headers` | JSON object of headers | `{}` |
| `dj-api-on-error` | Error handler name | - |
| `dj-api-on-data` | Data received handler | - |
| `dj-api-auto` | Auto-start polling | `true` |

### JavaScript API

```javascript
// Manually refresh
DJ.apiSync.refresh('#my-element');

// Stop all polling
DJ.apiSync.stop();

// Re-initialize
DJ.apiSync.init(document);
```

### CSS Classes

The directive adds these classes to the element:

- `dj-api-loading` - While fetching
- `dj-api-synced` - After successful sync
- `dj-api-error` - On error

```css
.dj-api-loading {
    opacity: 0.7;
}

.dj-api-error {
    border: 1px solid red;
}
```

## Combining Mixins

You can combine multiple API mixins:

```python
from djust import LiveView
from djust.integrations import GraphQLMixin, RESTMixin, DRFMixin

class HybridView(GraphQLMixin, RESTMixin, DRFMixin, LiveView):
    """View that uses GraphQL, REST, and DRF."""
    
    template_name = "hybrid.html"
    
    # GraphQL config
    graphql_endpoint = "ws://localhost:8000/graphql/"
    subscriptions = ['notifications']
    
    # REST config
    api_base = "https://external-api.com/v1"
    
    # DRF config
    serializer_class = ProductSerializer
    queryset = Product.objects.all()
    
    async def mount(self, request, **kwargs):
        # Load local data via DRF
        self.products = self.get_serialized_list()
        
        # Fetch external data via REST
        self.external_data = await self.api_get("/external/")
        
        # GraphQL subscriptions start automatically
```

## Best Practices

### 1. Error Handling

Always handle API errors gracefully:

```python
async def on_api_error(self, error):
    self.loading = False
    self.error = error.message
    
    # Log for debugging
    import logging
    logging.error(f"API error: {error}", exc_info=True)
```

### 2. Loading States

Show loading indicators:

```python
@event_handler
async def load_data(self):
    self.loading = True
    self.error = None
    
    try:
        self.data = await self.api_get("/data/")
    except APIError as e:
        self.error = e.message
    finally:
        self.loading = False
```

```html
<div dj-show="loading">Loading...</div>
<div dj-show="error" class="error">{{ error }}</div>
<div dj-show="not loading and not error">
    {{ data }}
</div>
```

### 3. Cleanup

Always clean up subscriptions and polling:

```python
async def unmount(self):
    await self.stop_subscriptions()  # GraphQL
    await self.stop_polling()        # REST
```

### 4. Caching

Use caching for frequently accessed data:

```python
class CachedView(RESTMixin, LiveView):
    _cache_ttl = 60  # seconds
    
    async def get_cached_data(self, key: str, endpoint: str):
        cache_key = f"api:{key}"
        cached = self._api_cache.get(cache_key)
        
        if cached and time.time() - cached['time'] < self._cache_ttl:
            return cached['data']
        
        data = await self.api_get(endpoint)
        self._api_cache[cache_key] = {'data': data, 'time': time.time()}
        return data
```

### 5. Rate Limiting

Respect API rate limits:

```python
from djust.decorators import rate_limit

class RateLimitedView(RESTMixin, LiveView):
    
    @event_handler
    @rate_limit(calls=10, period=60)  # 10 calls per minute
    async def fetch_data(self):
        self.data = await self.api_get("/expensive-endpoint/")
```

## TypeScript Support

Type definitions are available:

```typescript
// types/djust.d.ts
interface DJApiSync {
    init(container?: Element): void;
    initElement(element: Element): APISyncInstance;
    refresh(selector: string | Element): void;
    stop(): void;
    instances: Map<number, APISyncInstance>;
}

interface APISyncInstance {
    id: number;
    element: Element;
    options: APISyncOptions;
    start(): void;
    stop(): void;
    refresh(): void;
}
```

## Troubleshooting

### CORS Issues

If you see CORS errors, configure your API:

```python
# settings.py
CORS_ALLOWED_ORIGINS = [
    "http://localhost:8000",
]
```

### WebSocket Connection Failed

Check your GraphQL endpoint URL:
- Use `ws://` for local development
- Use `wss://` for production (HTTPS)

### Authentication Errors

Ensure tokens are being sent:

```python
def get_api_headers(self):
    token = self.request.session.get('api_token')
    if not token:
        raise APIError("Not authenticated", status_code=401)
    return {"Authorization": f"Bearer {token}"}
```
