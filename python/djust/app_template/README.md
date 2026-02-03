# {{ app_name }} - djust LiveView App

A reactive Django app built with djust LiveViews.

## Quick Start

1. **Add to INSTALLED_APPS** (settings.py):
   ```python
   INSTALLED_APPS = [
       # ...
       'djust',
       '{{ app_name }}',
   ]
   ```

2. **Include URLs** (urls.py):
   ```python
   from django.urls import path, include
   
   urlpatterns = [
       path('{{ app_name }}/', include('{{ app_name }}.urls')),
   ]
   ```

3. **Run the server**:
   ```bash
   # For WebSocket support (recommended):
   daphne myproject.asgi:application
   
   # Or for development (limited WebSocket):
   python manage.py runserver
   ```

4. **Visit the app**:
   - Counter: http://localhost:8000/{{ app_name }}/counter/
   - Contact Form: http://localhost:8000/{{ app_name }}/contact/
   - Hooks Demo: http://localhost:8000/{{ app_name }}/hooks/
   - Live Search: http://localhost:8000/{{ app_name }}/search/

## Files Structure

```
{{ app_name }}/
├── views.py              # LiveView classes
├── urls.py               # URL routing
├── tests.py              # Tests using LiveViewTestClient
├── templates/{{ app_name }}/
│   ├── counter.html      # Counter template (dj-click)
│   ├── contact_form.html # Form template (dj-model, dj-submit)
│   ├── hooks_example.html # JS hooks template (dj-hook)
│   └── search.html       # Search template (dj-model.debounce)
└── static/{{ app_name }}/
    └── hooks.js          # JavaScript hooks
```

## Key Concepts

### LiveViews
Server-rendered views that update via WebSocket:

```python
class CounterView(LiveView):
    template_name = '{{ app_name }}/counter.html'
    
    def mount(self, **kwargs):
        self.count = 0
    
    @event_handler
    def increment(self):
        self.count += 1
```

### Template Directives
- `dj-click="handler"` - Call handler on click
- `dj-model="field"` - Two-way input binding
- `dj-submit="handler"` - Handle form submission
- `dj-loading` - Show/hide during requests
- `dj-hook="HookName"` - Connect to JS hooks

### JavaScript Hooks
For client-side interactivity (charts, maps, animations):

```javascript
const MyHook = {
    mounted(el) { /* element added to DOM */ },
    updated(el) { /* element's data-* changed */ },
    destroyed(el) { /* element removed from DOM */ }
};
```

## Running Tests

```bash
# With Django's test runner
python manage.py test {{ app_name }}

# With pytest (recommended)
pytest {{ app_name }}/tests.py -v
```

## Documentation

- [djust Quickstart](https://github.com/your-repo/djust/docs/guides/QUICKSTART.md)
- [Event Handlers](https://github.com/your-repo/djust/docs/EVENT_HANDLERS.md)
- [Forms Guide](https://github.com/your-repo/djust/docs/guides/forms.md)
- [Hooks Guide](https://github.com/your-repo/djust/docs/guides/hooks.md)
