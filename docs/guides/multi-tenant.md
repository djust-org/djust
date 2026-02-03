# Multi-Tenant Support

djust provides first-class support for building multi-tenant SaaS applications. This guide covers tenant resolution, isolation, and best practices.

## Quick Start

```python
from djust import LiveView
from djust.tenants import TenantMixin

class DashboardView(TenantMixin, LiveView):
    template_name = 'dashboard.html'
    
    def mount(self, request, **kwargs):
        # self.tenant is automatically populated from the request
        self.items = Item.objects.filter(tenant_id=self.tenant.id)
```

Template:
```html
<h1>{{ tenant.name }} Dashboard</h1>
<p>Theme: {{ tenant.settings.theme }}</p>

<ul>
{% for item in items %}
    <li>{{ item.name }}</li>
{% endfor %}
</ul>
```

## Configuration

Add tenant settings to your `settings.py`:

```python
DJUST_CONFIG = {
    # Tenant resolution strategy
    'TENANT_RESOLVER': 'subdomain',  # Options: 'subdomain', 'path', 'header', 'session', 'custom'
    
    # Behavior options
    'TENANT_REQUIRED': True,  # Raise 404 if no tenant found
    'TENANT_DEFAULT': None,  # Default tenant ID if none resolved
    'TENANT_CONTEXT_NAME': 'tenant',  # Name in template context
    
    # Tenant-scoped presence backend
    'PRESENCE_BACKEND': 'tenant_redis',
    'PRESENCE_REDIS_URL': 'redis://localhost:6379/0',
}
```

## Tenant Resolution Strategies

### Subdomain Resolution

Extracts tenant from subdomain: `acme.example.com`

```python
DJUST_CONFIG = {
    'TENANT_RESOLVER': 'subdomain',
    'TENANT_SUBDOMAIN_EXCLUDE': ['www', 'api', 'admin'],  # Ignored subdomains
    'TENANT_MAIN_DOMAIN': 'example.com',  # Optional: explicit main domain
}
```

### Path Resolution

Extracts tenant from URL path: `example.com/acme/dashboard`

```python
DJUST_CONFIG = {
    'TENANT_RESOLVER': 'path',
    'TENANT_PATH_POSITION': 1,  # Position in path (1 = first segment)
    'TENANT_PATH_EXCLUDE': ['admin', 'api', 'static', 'media'],
}
```

### Header Resolution

Extracts tenant from HTTP header: `X-Tenant-ID: acme`

```python
DJUST_CONFIG = {
    'TENANT_RESOLVER': 'header',
    'TENANT_HEADER': 'X-Tenant-ID',  # Header name
}
```

### Session Resolution

Extracts tenant from session, JWT, or user attribute:

```python
DJUST_CONFIG = {
    'TENANT_RESOLVER': 'session',
    'TENANT_SESSION_KEY': 'tenant_id',  # Session key
    'TENANT_JWT_CLAIM': 'tenant_id',  # JWT claim name
}
```

### Custom Resolution

Use your own resolution logic:

```python
# settings.py
DJUST_CONFIG = {
    'TENANT_RESOLVER': 'custom',
    'TENANT_CUSTOM_RESOLVER': 'myapp.tenants.resolve_tenant',
}

# myapp/tenants.py
from djust.tenants import TenantInfo

def resolve_tenant(request):
    # Your custom logic here
    tenant_model = get_tenant_from_somewhere(request)
    if tenant_model:
        return TenantInfo(
            tenant_id=str(tenant_model.id),
            name=tenant_model.name,
            settings=tenant_model.settings,
            raw=tenant_model,  # Store original model
        )
    return None
```

### Chained Resolution

Try multiple strategies in order:

```python
DJUST_CONFIG = {
    'TENANT_RESOLVER': ['header', 'subdomain', 'session'],
}
```

## TenantMixin

### Basic Usage

```python
from djust import LiveView
from djust.tenants import TenantMixin

class MyView(TenantMixin, LiveView):
    template_name = 'my_view.html'
    
    def mount(self, request, **kwargs):
        # self.tenant is a TenantInfo object
        self.data = MyModel.objects.filter(tenant_id=self.tenant.id)
    
    def create_item(self, name):
        # Manually scope to tenant
        Item.objects.create(
            name=name,
            tenant_id=self.tenant.id
        )
```

### TenantInfo Object

The `self.tenant` property returns a `TenantInfo` object:

```python
tenant.id          # Unique identifier (string)
tenant.name        # Human-readable name
tenant.settings    # Dict of tenant settings
tenant.metadata    # Additional metadata
tenant.raw         # Original object (e.g., model instance)

# Helper methods
tenant.get_setting('theme', default='light')
```

### Optional Tenant

For views that work with or without a tenant:

```python
class PublicView(TenantMixin, LiveView):
    tenant_required = False  # Don't raise 404 if no tenant
    
    def mount(self, request, **kwargs):
        if self.tenant:
            self.items = Item.objects.filter(tenant_id=self.tenant.id)
        else:
            self.items = Item.objects.none()
```

### Custom Resolution

Override resolution logic per-view:

```python
class SpecialView(TenantMixin, LiveView):
    def resolve_tenant(self, request):
        # Custom logic for this view
        tenant_id = request.GET.get('org')
        if tenant_id:
            return TenantInfo(tenant_id=tenant_id)
        return None
```

## TenantScopedMixin

For views that work with a single model, use `TenantScopedMixin` for automatic scoping:

```python
from djust import LiveView
from djust.tenants import TenantScopedMixin

class ItemListView(TenantScopedMixin, LiveView):
    model = Item
    tenant_field = 'tenant_id'  # Default
    
    def mount(self, request, **kwargs):
        # Automatically filtered by tenant
        self.items = self.get_tenant_queryset()
    
    def add_item(self, name):
        # Automatically sets tenant_id
        new_item = self.create_for_tenant(name=name)
    
    def get_item(self, pk):
        # Only returns if belongs to tenant
        item = self.get_tenant_object(pk)
```

## Tenant-Scoped Presence

Presence tracking is automatically isolated per tenant when using `TenantMixin`:

```python
from djust import LiveView
from djust.tenants import TenantMixin
from djust.presence import PresenceMixin

class CollaborativeView(TenantMixin, PresenceMixin, LiveView):
    presence_key = "document:{doc_id}"
    
    def mount(self, request, **kwargs):
        self.doc_id = kwargs.get('doc_id')
        self.track_presence(meta={'name': request.user.username})
    
    def get_context_data(self):
        ctx = super().get_context_data()
        # Only shows users from the same tenant
        ctx['online_users'] = self.list_presences()
        return ctx
```

The presence key is automatically prefixed with tenant ID:
```
tenant:acme:document:123
```

### Manual Tenant-Scoped Presence

For custom presence needs:

```python
from djust.tenants import TenantPresenceManager

# Get backend for a specific tenant
manager = TenantPresenceManager.for_tenant('acme')

# All operations are scoped to that tenant
manager.join('room:1', 'user1', {'name': 'Alice'})
users = manager.list('room:1')
```

## Template Context

Tenant is automatically added to template context:

```html
<!-- Access tenant info -->
<h1>Welcome to {{ tenant.name }}</h1>

<!-- Tenant settings -->
<body class="theme-{{ tenant.settings.theme|default:'light' }}">

<!-- Conditional rendering -->
{% if tenant.settings.premium %}
    <div class="premium-feature">...</div>
{% endif %}

<!-- Access raw model if stored -->
{% if tenant.raw %}
    <img src="{{ tenant.raw.logo.url }}" alt="{{ tenant.name }}">
{% endif %}
```

### Context Processor

For non-LiveView templates, add the context processor:

```python
# settings.py
TEMPLATES = [{
    'OPTIONS': {
        'context_processors': [
            # ... other processors
            'djust.tenants.context_processor',
        ],
    },
}]
```

## State Isolation

### Tenant-Aware State Backend

Use `TenantAwareRedisBackend` to ensure LiveView state is isolated:

```python
DJUST_CONFIG = {
    'STATE_BACKEND': 'tenant_redis',
    'STATE_REDIS_URL': 'redis://localhost:6379/1',
}
```

Keys are automatically prefixed:
```
djust:tenant:acme:session:abc123
djust:tenant:beta:session:xyz789
```

### In-Memory Backend (Development)

For development, use the memory backend:

```python
DJUST_CONFIG = {
    'PRESENCE_BACKEND': 'memory',  # Auto-scoped per tenant
}
```

## Best Practices

### 1. Always Validate Tenant Access

```python
class SecureView(TenantMixin, LiveView):
    def load_item(self, item_id):
        try:
            # Always filter by tenant
            self.item = Item.objects.get(
                id=item_id,
                tenant_id=self.tenant.id
            )
        except Item.DoesNotExist:
            self.item = None
            self.push_event('error', {'message': 'Item not found'})
```

### 2. Use Database Constraints

```python
# models.py
class Item(models.Model):
    tenant_id = models.CharField(max_length=100, db_index=True)
    name = models.CharField(max_length=200)
    
    class Meta:
        # Ensure uniqueness within tenant
        unique_together = [('tenant_id', 'name')]
        # Add index for common queries
        indexes = [
            models.Index(fields=['tenant_id', 'created_at']),
        ]
```

### 3. Audit Logging

```python
class AuditedView(TenantMixin, LiveView):
    def perform_action(self, action_data):
        # Log with tenant context
        AuditLog.objects.create(
            tenant_id=self.tenant.id,
            user_id=self.request.user.id,
            action='perform_action',
            data=action_data,
        )
```

### 4. Rate Limiting Per Tenant

```python
from djust.tenants import TenantMixin

class RateLimitedView(TenantMixin, LiveView):
    def check_rate_limit(self):
        cache_key = f"rate_limit:{self.tenant.id}:{self.request.user.id}"
        # Implement rate limiting logic
```

### 5. Tenant-Specific Settings

```python
class ThemedView(TenantMixin, LiveView):
    def get_context_data(self):
        ctx = super().get_context_data()
        
        # Apply tenant-specific settings
        ctx['theme'] = self.tenant.get_setting('theme', 'light')
        ctx['logo_url'] = self.tenant.get_setting('logo_url')
        ctx['features'] = self.tenant.get_setting('enabled_features', [])
        
        return ctx
```

## Testing

### Mock Tenant Resolution

```python
import pytest
from unittest.mock import patch
from djust.tenants import TenantInfo

@pytest.fixture
def tenant():
    return TenantInfo(
        tenant_id='test_tenant',
        name='Test Tenant',
        settings={'theme': 'dark'},
    )

def test_view_with_tenant(tenant):
    with patch('djust.tenants.mixin.resolve_tenant', return_value=tenant):
        # Test your view
        pass
```

### Test Tenant Isolation

```python
from djust.tenants import TenantAwareMemoryBackend

def test_tenant_isolation():
    backend_a = TenantAwareMemoryBackend(tenant_id='tenant_a')
    backend_b = TenantAwareMemoryBackend(tenant_id='tenant_b')
    
    backend_a.join('room:1', 'user1', {'name': 'Alice'})
    backend_b.join('room:1', 'user2', {'name': 'Bob'})
    
    # Each tenant only sees their own users
    assert len(backend_a.list('room:1')) == 1
    assert backend_a.list('room:1')[0]['id'] == 'user1'
    
    assert len(backend_b.list('room:1')) == 1
    assert backend_b.list('room:1')[0]['id'] == 'user2'
```

## Migration from Single-Tenant

1. **Add tenant field to models:**
   ```python
   tenant_id = models.CharField(max_length=100, db_index=True, default='default')
   ```

2. **Update views to use TenantMixin:**
   ```python
   class MyView(TenantMixin, LiveView):  # Add TenantMixin
       ...
   ```

3. **Update queries to filter by tenant:**
   ```python
   # Before
   self.items = Item.objects.all()
   
   # After
   self.items = Item.objects.filter(tenant_id=self.tenant.id)
   ```

4. **Configure resolver:**
   ```python
   DJUST_CONFIG = {
       'TENANT_RESOLVER': 'subdomain',
       'TENANT_DEFAULT': 'default',  # Fallback for existing data
   }
   ```

## API Reference

### TenantInfo

```python
class TenantInfo:
    id: str              # Unique identifier
    name: str            # Human-readable name
    settings: dict       # Tenant settings
    metadata: dict       # Additional metadata
    raw: Any             # Original object
    
    def get_setting(key: str, default=None) -> Any
```

### TenantMixin

```python
class TenantMixin:
    tenant_required: bool = True
    tenant_context_name: str = 'tenant'
    
    @property
    def tenant(self) -> Optional[TenantInfo]
    
    def resolve_tenant(request) -> Optional[TenantInfo]
    def get_presence_key() -> str
    def get_state_key_prefix() -> str
```

### TenantScopedMixin

```python
class TenantScopedMixin(TenantMixin):
    model = None
    tenant_field: str = 'tenant_id'
    
    def get_tenant_queryset(model=None) -> QuerySet
    def create_for_tenant(model=None, **kwargs) -> Model
    def get_tenant_object(pk, model=None) -> Model
```

### Resolvers

```python
from djust.tenants import (
    SubdomainResolver,
    PathResolver,
    HeaderResolver,
    SessionResolver,
    CustomResolver,
    ChainedResolver,
    resolve_tenant,
    get_tenant_resolver,
)
```

### Backends

```python
from djust.tenants import (
    TenantAwareRedisBackend,
    TenantAwareMemoryBackend,
    TenantPresenceManager,
    get_tenant_presence_backend,
)
```
