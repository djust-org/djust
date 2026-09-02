# DjustTemplateBackend: Django Template Backend

**Status:** Production-ready feature (implemented 2025-11-19)

## Overview

`DjustTemplateBackend` is a Django template backend that enables **any Django view** to use djust's high-performance Rust template rendering engine, **without requiring LiveView**.

This provides the best of both worlds:
- ✅ **Faster rendering** (Rust vs Python) — measured at roughly 7-11x on variable- and filter-heavy templates, and no faster on static markup; see the README's Performance section
- ✅ **Standard Django views** (`TemplateView`, `render()`, etc.)
- ✅ **No client.js injection** (smaller page sizes, better caching)
- ✅ **Drop-in replacement** for Django's default backend

## Quick Start

### 1. Configure Django Settings

Add `DjustTemplateBackend` to your `TEMPLATES` setting:

```python
# settings.py
TEMPLATES = [
    # Djust backend (Rust rendering; ~7-11x faster on filter-heavy templates)
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
│  - ~7-11x faster (filter-heavy)     │
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
| **Rendering** | Rust (~7-11x faster) | Rust (~7-11x faster) |
| **Client.js** | ❌ No (smaller pages) | ✅ Yes (~58 KB gz) |
| **WebSocket** | ❌ No | ✅ Yes |
| **Interactivity** | ❌ Static only | ✅ Real-time updates |
| **Use Case** | Static content pages | Interactive features |
| **Page Size** | ~26KB (example) | ~91KB (example) |
| **Django Views** | ✅ TemplateView, render() | ❌ Must use LiveView |

**When to use each:**
- **DjustTemplateBackend**: Blog posts, marketing pages, documentation, about pages
- **LiveView**: Dashboards, real-time forms, interactive UIs, live data

### The engine without LiveView

Both columns render through the same Rust crate, `djust_templates`. The parts of it that only LiveView uses sit behind a cargo feature named `liveview`, on by default (#2519). Built with `--no-default-features` the engine has:

- No `<!--dj-if-->` boundary markers around `{% if %}` blocks. They are the VDOM differ's keyed boundaries and mean nothing without a differ.
- No parsed-VNode loop cache. That cache is what `render_with_diff` splices into the diff baseline; without the feature `djust_vdom` leaves the dependency graph.
- No Rust UI components. A `<RustButton />`-style tag still parses, but rendering one raises a template error naming the feature; `djust_components` leaves the graph.

It is still a PyO3 crate. The tag and filter registries that serve `{% url %}` and `@register.filter` callables are Python-backed, and a plain Django project needs them more than LiveView does; cutting that dependency is a separate, larger change. The loop-cache manifest and its placeholder string are also left in place, since they are inert unless a LiveView installs a cache guard.

The feature is a compile-time boundary, not the reason backend output is clean. Whether markers are emitted is decided per render by a switch on the context. Every plain entry (the backend, `SimpleLiveView.render_template`, and the `render_template` / `render_template_with_dirs` functions) turns that switch off, so a default build renders the same bytes on the backend path as a `--no-default-features` build; the LiveView path keeps it on. CI builds and tests the engine both ways (`make check-no-default-features`).

## Features

### Supported

✅ **Django Template Syntax**
- Variables: `{{ variable }}`
- Filters: `{{ variable|filter }}` (including `urlencode`)
- Tags: `{% if %}`, `{% elif %}`, `{% else %}`, `{% for %}`, `{% extends %}`, `{% block %}`, `{% url %}`, `{% include %}`
- Comparison operators: `>`, `<`, `>=`, `<=` in `{% if %}` tags
- Identity operators: `is`, `is not` in `{% if %}` tags (e.g. `{% if x is None %}`)
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

✅ **Auto-Escaping (XSS Protection)**
- All `{{ variable }}` output is HTML-escaped by default
- `mark_safe()` values auto-detected and rendered unescaped
- `|safe` filter for explicit opt-out in templates
- Component `.render()` output preserved (SafeString detection)

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

### Conformance

The backend is scored against Django's own template test suite: `tests/template_tests` from the `django/django` checkout at the tag matching the installed Django (5.2.16 for the baseline below). An in-process `Engine` subclass routes every engine the suite builds through `DjustTemplateBackend`. Nothing in Django's checkout is edited, and the `TEMPLATES`-configured backend stays Django's own. The engine is reached through the plain-backend path only, not the LiveView path.

- **44.03%** of the Django template tests that reach the engine pass (461 of 1047) <!-- django-suite-claim -->
- Over the whole `template_tests` label the figure is 59.75% (870 of 1456, 14 skipped). That is not the headline: 409 of those tests never reach any engine (`test_parser`, `test_context`, `test_smartif`, ...) and measure Django against itself, so no engine work can move them.

Two result kinds are counted separately because they are different work:

- **ERROR**: the test could not run to an assertion. An unsupported tag, an attribute the backend cannot express, or a crash. Top classes in the baseline: `autoescape` (79), `blocktrans` (34), `ifchanged` (24), `trans` (19), `cache` (15), `querystring` (14).
- **FAIL**: the test ran and the output was wrong. The largest class (111) is a `TemplateSyntaxError` that Django raises at parse time and djust does not. The rest are output mismatches such as `string_if_invalid` not honoured (#2518) and `{% if x|default_if_none:y %}` evaluating false when `x` is undefined (#2528). The `<!--dj-if-->` marker that leaked into plain-backend output was fixed in #2519; it accounted for five of the six cases it broke, and the sixth is the #2528 shape.

Seven `template_tests` cases segfault the interpreter (a `DjustTemplate` or a type object placed in the context, the #2516 reference-cycle class). The runner isolates each one: a crash records the in-flight test as `ERROR: process crashed`, skips it and every finished test, and relaunches, so nothing after a crash is lost.

**Running it**

```bash
make django-template-suite   # clones django/django at the installed tag into .django-src/
```

Outputs land in `.django-src/last-run.txt` (one line per test plus the summary) and `.django-src/last-run.json`; both are gitignored. `scripts/run-django-template-suite.py run --gate-off` runs the same harness with the adapter not installed, Django against itself, and must report 100% or the harness is broken. `scripts/run-django-template-suite.py compare --json .django-src/last-run.json` is the ratchet: it exits non-zero when a run's percentage drops below the committed baseline.

The number above moves only through `scripts/django-template-suite-baseline.json`, regenerated with `--write-baseline` in the PR that improves it, never edited by hand. A test pins this page's figure to that file. The `django-template-suite` CI job runs on every PR but is non-gating (`continue-on-error`, informational `compare`) until #2522 promotes it to a blocking ratchet.

Known limits of the harness:

- A crash with no test in flight (in a class or module `setUpClass`) cannot be attributed to a test. The run stops with a message naming how many tests had finished; isolate the module with `--label`.
- Django's runner turns `RuntimeWarning` and `ResourceWarning` into errors. A future warning raised by djust would surface as ERRORs. That is a finding about the backend, not a harness bug.

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

**Last Updated:** 2026-02-08
**Status:** Production-ready
**Minimum djust version:** 0.1.0 (with Rust extension compiled)
