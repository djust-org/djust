# Security Patterns

## Authorization

```python
from django.core.exceptions import PermissionDenied
from djust.decorators import event_handler, permission_required

class SecureView(LiveView):
    def mount(self, request, pk=None, **kwargs):
        # Check auth in mount()
        if not request.user.is_authenticated:
            raise PermissionDenied("Login required")

        # Object-level permissions
        if pk:
            obj = MyModel.objects.get(pk=pk)
            if obj.owner != request.user:
                raise PermissionDenied("Not authorized")
            self._obj = obj

    # Re-verify in handlers (state can change between events)
    @event_handler()
    def delete(self, item_id: int = 0, **kwargs):
        item = MyModel.objects.get(id=item_id)
        if item.owner != self.request.user:
            raise PermissionDenied
        item.delete()

    # Or use permission decorator
    @permission_required("myapp.delete_mymodel")
    @event_handler()
    def admin_delete(self, item_id: int = 0, **kwargs):
        MyModel.objects.filter(id=item_id).delete()
```

## Input Validation

```python
@event_handler()
def update_price(self, value: str = "", **kwargs):
    try:
        price = Decimal(value)
    except (ValueError, InvalidOperation):
        self.error = "Invalid price"
        return
    if price <= 0 or price > 100000:
        self.error = "Price out of range"
        return
    self.price = price
```

Rules:
- Always check auth in `mount()` and re-check in event handlers
- Use type hints for automatic coercion (`item_id: int`)
- Always `{% csrf_token %}` in forms
- Never use `|safe` filter on user-controlled variables
- Use `@permission_required` for Django permission checks
- Use `@rate_limit` to prevent abuse on expensive handlers
