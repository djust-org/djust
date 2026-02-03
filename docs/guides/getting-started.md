# Getting Started with djust

This guide walks you through creating your first djust LiveView application.

## Prerequisites

- Python 3.9+
- Django 4.0+
- Basic knowledge of Django views and templates

## Installation

```bash
pip install djust channels daphne
```

## Quick Start Options

### Option 1: Start a New Project

Create a complete djust project with `startproject`:

```bash
# Create new project
python -m django djust_startproject myproject

cd myproject
pip install -r requirements.txt
python manage.py migrate
daphne myproject.asgi:application
```

Visit http://localhost:8000 to see your first LiveView!

### Option 2: Add to Existing Project

1. **Install djust:**
   ```bash
   pip install djust channels daphne
   ```

2. **Update settings.py:**
   ```python
   INSTALLED_APPS = [
       'daphne',  # Before django.contrib.staticfiles
       # ... your apps
       'channels',
       'djust',
   ]
   
   ASGI_APPLICATION = 'myproject.asgi.application'
   
   CHANNEL_LAYERS = {
       'default': {
           'BACKEND': 'channels.layers.InMemoryChannelLayer',
       },
   }
   ```

3. **Create asgi.py:**
   ```python
   import os
   os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
   
   from djust.asgi import get_application
   application = get_application()
   ```

4. **Create a LiveView app:**
   ```bash
   # Use djust's app template for full boilerplate
   django-admin startapp --template=/path/to/djust/app_template myapp
   
   # Or create a simple app manually
   python manage.py startapp myapp
   ```

### Option 3: Add Individual LiveViews

Add a LiveView to any existing Django app:

```bash
python manage.py startliveview counter --app myapp
python manage.py startliveview contact_form --app myapp --with-form
python manage.py startliveview chart --app myapp --with-hooks
```

## Your First LiveView

### 1. Create the View

```python
# myapp/views.py
from djust import LiveView, event_handler

class CounterView(LiveView):
    template_name = 'myapp/counter.html'
    
    def mount(self, **kwargs):
        """Initialize state when the view loads."""
        self.count = 0
    
    @event_handler
    def increment(self):
        """Called when user clicks increment button."""
        self.count += 1
    
    @event_handler
    def decrement(self):
        self.count -= 1
```

### 2. Create the Template

```html
<!-- myapp/templates/myapp/counter.html -->
<div>
    <h1>Counter: {{ count }}</h1>
    
    <button dj-click="decrement">-</button>
    <button dj-click="increment">+</button>
</div>
```

### 3. Add URL

```python
# myapp/urls.py
from django.urls import path
from .views import CounterView

urlpatterns = [
    path('counter/', CounterView.as_view(), name='counter'),
]
```

### 4. Run the Server

```bash
# For full WebSocket support (recommended)
daphne myproject.asgi:application

# Or for development (limited WebSocket in some environments)
python manage.py runserver
```

Visit http://localhost:8000/myapp/counter/

## Understanding LiveView

### How It Works

1. **Initial Load**: Django renders the template server-side (like normal)
2. **WebSocket Connection**: JavaScript connects to the server
3. **User Interaction**: Clicks/inputs are sent via WebSocket
4. **Server Processing**: Your Python handlers run, state updates
5. **DOM Patching**: Only changed parts of the page update

### Key Concepts

| Concept | Description |
|---------|-------------|
| `mount()` | Called once when view loads. Initialize state here. |
| `@event_handler` | Decorator marks methods callable from templates |
| `dj-click` | Template attribute to trigger handlers on click |
| `dj-model` | Two-way binding for input values |
| `dj-loading` | Show loading states during requests |
| `dj-hook` | Connect elements to JavaScript for client-side logic |

### Lifecycle

```
Browser Request
      │
      ▼
  mount(**kwargs)     ← Initialize state
      │
      ▼
  render template     ← {{ count }} etc.
      │
      ▼
  WebSocket connect
      │
      ▼
  User clicks         ← dj-click="increment"
      │
      ▼
  increment()         ← Handler runs
      │
      ▼
  re-render           ← Diff computed
      │
      ▼
  DOM patch           ← Only changes applied
```

## Common Patterns

### Forms with Validation

```python
from djust import LiveView, event_handler
from djust.forms import FormMixin
from django import forms

class ContactForm(forms.Form):
    email = forms.EmailField()
    message = forms.CharField(widget=forms.Textarea)

class ContactView(FormMixin, LiveView):
    template_name = 'myapp/contact.html'
    form_class = ContactForm
    
    def form_valid(self, form):
        # Process valid form
        send_email(form.cleaned_data)
        self.success_message = "Sent!"
```

```html
<form dj-submit="submit_form">
    <input 
        type="email" 
        dj-model="form_data.email"
        dj-change="validate_field"
        data-field_name="email"
    >
    {% if field_errors.email %}
        <span class="error">{{ field_errors.email }}</span>
    {% endif %}
    
    <button type="submit">Send</button>
</form>
```

### Live Search

```python
class SearchView(LiveView):
    template_name = 'myapp/search.html'
    
    def mount(self, **kwargs):
        self.query = ''
        self.results = []
    
    @event_handler
    def search(self, query: str = ''):
        self.query = query
        self.results = Item.objects.filter(name__icontains=query)[:10]
```

```html
<input 
    type="search" 
    dj-model="query"
    dj-model.debounce.300
    dj-input="search"
    data-query="{{ query }}"
>

{% for item in results %}
    <div>{{ item.name }}</div>
{% endfor %}
```

### JavaScript Hooks

For charts, maps, animations — anything needing client-side JS:

```html
<div 
    dj-hook="ChartHook"
    data-values="{{ chart_data|safe }}"
    data-type="bar"
>
    Loading chart...
</div>
```

```javascript
// static/myapp/hooks.js
const ChartHook = {
    mounted(el) {
        const data = JSON.parse(el.dataset.values);
        this.chart = new Chart(el, { data });
    },
    updated(el) {
        const data = JSON.parse(el.dataset.values);
        this.chart.update(data);
    },
    destroyed(el) {
        this.chart.destroy();
    }
};

window.djust.registerHook('ChartHook', ChartHook);
```

## Testing

djust provides `LiveViewTestClient` for testing without a browser:

```python
from django.test import TestCase
from djust.testing import LiveViewTestClient

class TestCounter(TestCase):
    def test_increment(self):
        client = LiveViewTestClient(CounterView)
        client.mount()
        
        client.send_event('increment')
        client.assert_state(count=1)
        
        client.send_event('increment')
        client.assert_state(count=2)
```

## Next Steps

- [Event Handlers](../EVENT_HANDLERS.md) - Advanced handler patterns
- [Forms Guide](forms.md) - Complete form validation
- [Hooks Guide](hooks.md) - JavaScript integration
- [Testing Guide](testing.md) - Full testing coverage
- [Navigation](navigation.md) - Live redirects and patches
- [Streaming](streaming.md) - Real-time collections
