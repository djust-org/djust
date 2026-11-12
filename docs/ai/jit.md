# JIT Serialization

Rust-powered serialization (unmeasured; the previously quoted multiple was carried over from template-rendering claims). Hand the QuerySet to the template in `get_context_data()`; do not convert it first.

```python
# CORRECT: the QuerySet itself goes into the context
def _refresh(self):
    self._items = Item.objects.filter(active=True)

def get_context_data(self, **kwargs):
    context = super().get_context_data(**kwargs)
    context["items"] = self._items  # serialized here, for the fields the template reads
    return context
```

```python
# WRONG: an undeclared attribute alone, public or not
def mount(self, request, **kwargs):
    self.items = Item.objects.all()  # never reaches the template: {{ items }} renders empty

# WRONG: the 1.x idiom
def get_context_data(self, **kwargs):
    self.items = self._items  # publishes nothing under explicit exposure (the default)
    return super().get_context_data(**kwargs)

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
- Keep QuerySets on the view (`self._items`); the underscore is a naming convention, not what hides them
- Supply them in `get_context_data()`: `context["items"] = self._items` after `super().get_context_data(**kwargs)`
- Never convert QuerySets to lists
- Never manually serialize model fields
- JIT auto-detects `select_related` needs from template field access
- A non-model object for the template (a dataclass, a presenter): `context["cards"] = self.serialize_for_template(self._cards, "cards")`; `self.template_paths()` lists what the template reads
