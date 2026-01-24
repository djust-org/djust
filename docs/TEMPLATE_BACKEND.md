# DjustTemplateBackend: Django Template Backend

**Status:** Production-ready feature (implemented 2025-11-19)

## Overview

`DjustTemplateBackend` is a Django template backend that enables **any Django view** to use djust's high-performance Rust template rendering engine, **without requiring LiveView**.

This provides the best of both worlds:
- ✅ **10-100x faster rendering** (Rust vs Python)
- ✅ **Standard Django views** (`TemplateView`, `render()`, etc.)
- ✅ **No client.js injection** (smaller page sizes, better caching)
- ✅ **Drop-in replacement** for Django's default backend

## Quick Start

### 1. Configure Django Settings

Add `DjustTemplateBackend` to your `TEMPLATES` setting:

```python
# settings.py
TEMPLATES = [
    # Djust backend (Rust rendering, 10-100x faster)
    {
        'BACKEND': 'djust.template_backend.DjustTemplateBackend',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
    # Optional: Fallback Django backend for admin/contrib apps
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]
```

### 2. Use Standard Django Views

That's it! All your existing Django views now use Rust rendering:

```python
from django.views.generic import TemplateView

class HomeView(TemplateView):
    template_name = 'home.html'  # Rendered with Rust! 🚀

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Welcome'
        return context
```

Or with function-based views:

```python
from django.shortcuts import render

def my_view(request):
    return render(request, 'template.html', {
        'data': 'Rendered with Rust!',
    })
```

## Architecture

### How It Works

```
┌─────────────────────────────────────┐
│  Django View (TemplateView, etc.)  │
│  context = {'title': 'Hello'}      │
└─────────────────────────────────────┘
           ↓
┌─────────────────────────────────────┐
│  DjustTemplateBackend               │
│  - Loads template from filesystem   │
│  - Applies context processors       │
└─────────────────────────────────────┘
           ↓
┌─────────────────────────────────────┐
│  Rust Template Engine               │
│  render_template(template, context) │
│  - 10-100x faster than Django       │
│  - Automatic template caching       │
│  - Sub-millisecond compilation      │
└─────────────────────────────────────┘
           ↓
┌─────────────────────────────────────┐
│  HTML Response (no client.js)       │
│  - ~70% smaller than LiveView       │
│  - Browser cacheable                │
└─────────────────────────────────────┘
```

### vs. LiveView

| Feature | DjustTemplateBackend | LiveView |
|---------|---------------------|----------|
| **Rendering** | Rust (10-100x faster) | Rust (10-100x faster) |
| **Client.js** | ❌ No (smaller pages) | ✅ Yes (~63KB) |
| **WebSocket** | ❌ No | ✅ Yes |
| **Interactivity** | ❌ Static only | ✅ Real-time updates |
| **Use Case** | Static content pages | Interactive features |
| **Page Size** | ~26KB (example) | ~91KB (example) |
| **Django Views** | ✅ TemplateView, render() | ❌ Must use LiveView |

**When to use each:**
- **DjustTemplateBackend**: Blog posts, marketing pages, documentation, about pages
- **LiveView**: Dashboards, real-time forms, interactive UIs, live data

## Features

### Supported

✅ **Django Template Syntax**
- Variables: `{{ variable }}`
- Filters: `{{ variable|filter }}` (including `urlencode`)
- Tags: `{% if %}`, `{% for %}`, `{% extends %}`, `{% block %}`, `{% url %}`, `{% include %}`
- Comparison operators: `>`, `<`, `>=`, `<=` in `{% if %}` tags
- Comments: `{# comment #}`

✅ **Template Loading**
- `DIRS` configuration
- `APP_DIRS` support
- Template inheritance (`{% extends %}`)
- Template includes (`{% include %}`)

✅ **Auto-serialization**
- Django `datetime`, `date`, `time` objects
- `Decimal` values
- `UUID` objects
- `FieldFile` objects (file URLs)

✅ **Context Processors**
- Standard Django context processors
- Custom context processors
- `request` object in context

✅ **Django Integration**
- Works with `TemplateView`
- Works with `render()` shortcut
- Works with generic views
- CSRF token support

### Limitations

⚠️ **Not all Django features supported yet:**
- Some custom template tags not implemented
- Some template filters missing (see workarounds below)

See djust documentation for complete list of supported features.

## Performance

### Benchmarks

Real-world performance improvements from the marketing site:

| Page | Django Templates | DjustTemplateBackend | Speedup |
|------|-----------------|---------------------|---------|
| Home | ~25ms | **~0.8ms** | **31x faster** |
| Features | ~30ms | **~1.0ms** | **30x faster** |
| Complex | ~50ms | **~1.5ms** | **33x faster** |

**Benefits:**
- Sub-millisecond rendering
- Automatic template caching
- No Python interpreter overhead
- Minimal memory allocation

### Page Size Comparison

| Approach | Size | Client.js | Cacheable |
|----------|------|-----------|-----------|
| LiveView | 91KB | ✅ Inline | ❌ No |
| DjustTemplateBackend | 26KB | ❌ None | ✅ Yes |

**~70% size reduction** on static pages!

## Migration Guide

### From Django Templates

**Before:**
```python
# settings.py
TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        ...
    },
]
```

**After:**
```python
# settings.py
TEMPLATES = [
    {
        'BACKEND': 'djust.template_backend.DjustTemplateBackend',
        'DIRS': [],
        'APP_DIRS': True,
        ...
    },
    # Keep Django backend as fallback
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        ...
    },
]
```

**No view code changes required!** ✅

### From StaticMarketingView (Hacky Override)

If you previously used a hacky `StaticMarketingView` that overrode `_inject_client_script()`:

**Before (Hacky):**
```python
class StaticMarketingView(LiveView):
    def _inject_client_script(self, html: str) -> str:
        return html  # Skip injection

class HomeView(StaticMarketingView):
    template_name = 'home.html'
```

**After (Proper):**
```python
# settings.py - Configure DjustTemplateBackend

# views.py - Use standard Django view
from django.views.generic import TemplateView

class HomeView(TemplateView):
    template_name = 'home.html'
```

**Benefits:**
- Cleaner code
- Works with ANY Django view
- No inheritance hacks
- Reusable across projects

## Workarounds

### Missing Template Tags/Filters

If you encounter unsupported template tags/filters, use these workarounds:

#### String Escaping for JavaScript

**❌ Not supported:**
```django
<script>var data = "{{ data|escapejs }}";</script>
```

**✅ Workaround:**
```python
import json

class MyView(TemplateView):
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Use json.dumps and strip quotes
        context['data_escaped'] = json.dumps(data)[1:-1]
        return context
```

```django
<script>var data = "{{ data_escaped }}";</script>
```

## Use Cases

### Perfect For:

✅ **Marketing/Landing Pages**
- Fast rendering, no JavaScript bloat
- Better SEO (smaller pages, faster load)
- Browser caching works properly

✅ **Blog/Documentation**
- Static content rendered fast
- No WebSocket overhead
- Clean, cacheable HTML

✅ **Admin Dashboards** (read-only views)
- Fast data display
- Use LiveView for interactive parts

✅ **API Documentation Pages**
- Fast rendering of code examples
- No client-side JavaScript needed

### Not Suitable For:

❌ **Real-time Interactive UIs**
→ Use `LiveView` instead (WebSocket, VDOM diffing)

❌ **Forms with Live Validation**
→ Use `LiveView` with `FormMixin`

❌ **Live Data Dashboards**
→ Use `LiveView` for real-time updates

## Combining with LiveView

You can use both in the same project:

```python
# Static pages use TemplateView + DjustTemplateBackend
class AboutView(TemplateView):
    template_name = 'about.html'  # Rust rendered, no JS

class BlogPostView(TemplateView):
    template_name = 'blog/post.html'  # Rust rendered, no JS

# Interactive pages use LiveView
from djust import LiveView

class DashboardView(LiveView):
    template_name = 'dashboard.html'  # Rust + WebSocket + VDOM

class FormView(LiveView):
    template_name = 'form.html'  # Live validation
```

**Result:** Fast static pages + powerful interactive features where needed!

## API Reference

### DjustTemplateBackend

```python
class DjustTemplateBackend(BaseEngine):
    """Django template backend using djust's Rust engine."""

    def __init__(self, params: Dict[str, Any]):
        """
        Initialize backend.

        Args:
            params: Template configuration from settings.TEMPLATES
        """

    def from_string(self, template_code: str) -> DjustTemplate:
        """Create template from string."""

    def get_template(self, template_name: str) -> DjustTemplate:
        """Load template from filesystem."""
```

### DjustTemplate

```python
class DjustTemplate:
    """Wrapper for Rust-rendered template."""

    def render(
        self,
        context: Optional[Dict] = None,
        request: Optional[HttpRequest] = None
    ) -> SafeString:
        """
        Render template with context.

        Args:
            context: Template context variables
            request: Django request object (for CSRF, context processors)

        Returns:
            Rendered HTML as SafeString
        """
```

## Troubleshooting

### Template Not Found

**Error:** `TemplateDoesNotExist: template.html`

**Solution:** Check `DIRS` and `APP_DIRS` in settings:

```python
TEMPLATES = [
    {
        'BACKEND': 'djust.template_backend.DjustTemplateBackend',
        'DIRS': [BASE_DIR / 'templates'],  # Add template directories
        'APP_DIRS': True,  # Search app templates
        ...
    },
]
```

### Unsupported Template Tag

**Error:** `Error rendering template: Unsupported tag '{% tag_name %}'`

**Solution:** Use Django backend for that specific template, or use a workaround (see above).

### Context Processor Not Working

**Error:** Variable not available in template

**Solution:** Ensure context processors are configured:

```python
'OPTIONS': {
    'context_processors': [
        'django.template.context_processors.request',  # Adds 'request'
        'myapp.context_processors.custom',  # Custom processor
    ],
},
```

## Future Enhancements

Planned improvements:

- [x] ~~Support for `{% url %}` tag~~ (Added in 0.1.6)
- [x] ~~Additional Django template filters~~ (`urlencode` added in 0.1.6)
- [ ] Custom template tag registration
- [ ] Better error messages with line numbers
- [ ] Template debugging tools
- [ ] `escapejs` filter for JavaScript string escaping

## Contributing

Found a bug or want to add a feature? See `CONTRIBUTING.md`.

## License

MIT License - same as djust framework.

---

**Last Updated:** 2026-01-24
**Status:** Production-ready
**Minimum djust version:** 0.1.0 (with Rust extension compiled)
