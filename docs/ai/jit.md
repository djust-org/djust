# JIT Serialization

Rust-powered serialization (10-100x faster than Python). Requires the private/public variable pattern.

```python
# CORRECT: private -> public in get_context_data()
def _refresh(self):
    self._items = Item.objects.filter(active=True)  # PRIVATE (underscore)

def get_context_data(self, **kwargs):
    self.items = self._items  # PUBLIC <- PRIVATE (JIT triggers here)
    context = super().get_context_data(**kwargs)
    return context
```

```python
# WRONG: direct public assignment in mount
def mount(self, request, **kwargs):
    self.items = Item.objects.all()  # bypasses JIT!

# WRONG: converting to list
def _refresh(self):
    self._items = list(Item.objects.all())  # disables JIT!

# WRONG: manual serialization
def get_context_data(self, **kwargs):
    context = super().get_context_data(**kwargs)
    context['items'] = [{'id': i.id} for i in self._items]  # unnecessary!
    return context
```

Rules:
- Store QuerySets in `self._private` (underscore prefix = private)
- Assign to `self.public` only in `get_context_data()`
- Call `super().get_context_data(**kwargs)` to trigger Rust serialization
- Never convert QuerySets to lists
- Never manually serialize model fields
- JIT auto-detects `select_related` needs from template field access
