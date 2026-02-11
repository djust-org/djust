# LiveView Lifecycle

Flow: `mount()` -> `_refresh()` -> `get_context_data()` -> render -> event -> handler -> `_refresh()` -> `get_context_data()` -> re-render

```python
from djust import LiveView
from djust.decorators import event_handler

class MyView(LiveView):
    template_name = 'myapp/list.html'

    def mount(self, request, **kwargs):
        """Called once on first load. Initialize state here."""
        self.search = ""
        self._refresh()

    def _refresh(self):
        """Build QuerySets. Store in PRIVATE vars (underscore prefix)."""
        qs = Item.objects.all()
        if self.search:
            qs = qs.filter(name__icontains=self.search)
        self._items = qs  # private

    def get_context_data(self, **kwargs):
        """Assign private -> public. Call super() to trigger Rust JIT."""
        self.items = self._items  # public <- private
        context = super().get_context_data(**kwargs)
        return context

    @event_handler()
    def do_search(self, value: str = "", **kwargs):
        """Event handlers update state, then call _refresh()."""
        self.search = value
        self._refresh()
```

Key rules:
- `mount()`: set initial state, call `_refresh()`
- `_refresh()`: build QuerySets, store in `self._private`
- `get_context_data()`: assign `self.public = self._private`, call `super()`
- Event handlers: update state, call `_refresh()` (framework re-renders automatically)
