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
        """Build QuerySets and keep them on the view."""
        qs = Item.objects.all()
        if self.search:
            qs = qs.filter(name__icontains=self.search)
        self._items = qs

    def get_context_data(self, **kwargs):
        """The template gets what this returns, and nothing else."""
        context = super().get_context_data(**kwargs)
        context["search"] = self.search
        context["items"] = self._items  # a QuerySet; serialized for the template (JIT)
        return context

    @event_handler()
    def do_search(self, value: str = "", **kwargs):
        """Event handlers update state, then call _refresh()."""
        self.search = value
        self._refresh()
```

Key rules:
- `mount()`: set initial state, call `_refresh()`
- `_refresh()`: build QuerySets, keep them on the view (`self._items`)
- `get_context_data()`: call `super()`, then add every name the template reads. An attribute set on `self` does not reach the template by itself, with or without an underscore; a name nothing supplies renders empty, with no error
- Event handlers: update state, call `_refresh()` (framework re-renders automatically)

URL params: declare them, do not parse them in `handle_params()`:

```python
class ListParams(forms.Form):
    page = forms.IntegerField(min_value=1, required=False, initial=1)

class ListView(LiveView):
    params_form = ListParams  # handle_params gets cleaned_data; a bad value -> params_errors
    params_form_fallback = "fields"  # optional: keep the fields that validated (default "initial": every field's initial value)

    def mount(self, request, **kwargs):
        self.page = 1

    def handle_params(self, params, uri):
        self.page = params["page"]

    def get_context_data(self, **kwargs):
        return {**super().get_context_data(**kwargs), "page": self.page}
```

Not-found pages: set `http_status_of_state = {"not_found": 404}` once on a base view (or `LIVEVIEW_CONFIG["http_status_of_state"]` for the whole project; the view's own attribute wins, never merged) and set `self.state = "not_found"`; no per-view `get_http_status()` (V025 warns about one under a project-wide map).
