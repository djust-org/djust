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

A templates-only project pays only for the template engine. Since #2559 the
`djust` package resolves its public names lazily, so configuring
`DjustTemplateBackend` imports about 25 `djust` modules and none of the LiveView
stack (`channels`, the WebSocket consumer, presence) — those load the first time
something imports `djust.LiveView`. `channels` and `msgpack` are still installed
by `pip install djust` (the extras decision is #2560); they are simply not
imported.

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
- Filters: `{{ variable|filter }}` — every Django built-in filter (see the generated lists below)
- Tags: `{% if %}`, `{% for %}`, `{% extends %}`, `{% block %}`, `{% include %}`, `{% url %}`, ... (see the generated lists below)
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
- Several Django built-in tags and most of Django's `{% load %}` library tags (`i18n`, `l10n`, `tz`, `cache`) are not implemented; the generated lists below are the authority
- Django's `{% load %}` library *filters* (`unlocalize`, `language_name`, `utc`, …) are not implemented natively; they resolve only through the filter bridge, which needs a `DjangoTemplates` engine in `TEMPLATES` next to djust (the fallback engine in the Quick Start). The bridged `tz` filters resolve but currently render empty output (#2541)
- A project's own `{% load %}` tag libraries ARE loaded (#2547, see "Loading a project's template libraries" below); a raw `@register.tag` that consumes a body is the one shape that is refused

### Loading a project's template libraries

`{% load app_tags %}` imports the library the way Django does and bridges it into the Rust engine (#2547): the name resolves through Django's own library map — every installed app's `templatetags/` package (`get_installed_libraries()`) plus the backend's `OPTIONS['libraries']` — the module is imported with `django.template.library.import_library`, and every entry is registered. `{% load a b %}` and `{% load x y from lib %}` behave as on Django; an unknown name raises Django's exact `TemplateSyntaxError` at parse time.

- **Filters** go through the same bridge as before (`is_safe` / `needs_autoescape` honoured; an `is_safe=True` filter keeps a safe input safe and never makes a hostile one safe).
- **Tags** — `simple_tag`, `simple_block_tag`, `inclusion_tag`, and raw `@register.tag` functions that build a node from their own token — are rendered by the library's OWN compile function and Django node, so arguments, keyword arguments, `takes_context`, `as var`, escaping and inclusion-template rendering are Django's, byte for byte. A `takes_context` tag sees the template context as a plain dict.
- **Refused, per tag:** a raw `@register.tag` whose compile function reads past its own token (`parser.parse((...))`, `next_token`, `skip_past`) has no token stream to consume here. The `{% load %}` succeeds and the rest of the library works; the moment a template uses that one tag the parser raises `TemplateSyntaxError: '<tag>' from library '<lib>' is a raw @register.tag that consumes a block … port it to @register.simple_block_tag, or wait for the raw-body registration kind (#2558)`. Port it to `simple_block_tag`.
- **Not bridged, deliberately:** Django's own libraries (`i18n`, `l10n`, `tz`, `cache`, `static`) and djust's own `djust.templatetags.*`. They resolve — `{% load static %}` keeps parsing — but their tags come from the engine's native handlers, each its own row in the generated lists below.
- **`OPTIONS['builtins']`** (dotted library paths) are bridged when the backend is constructed, so their tags and filters need no `{% load %}` — Django's meaning. Django's three default builtins are the Rust natives and are skipped.
- **Scoping differs from Django.** Django scopes a loaded library to the template that loaded it; djust's registries are process-global, so once any render loads a library every later template on either engine path (plain backend or LiveView) sees its tags, `{% load %}` or not. A missing library is still refused. A test suite that clears the Rust tag registries must call `djust.template_libraries.reassert()` afterwards (the framework's own `djust.test_isolation` does): the Rust template cache is keyed by source, so a template parsed while the tags were registered is served from cache and never re-runs its `{% load %}`.
- **Known gaps:** `context.use_l10n` / `autoescape` are not carried into a bridged tag's `Context` (#2586); the scoreboard's `test_no_render_side_effect` reads `template.nodelist`, a Django-internal attribute the backend does not have — a deliberate, documented ERROR (#2587).

### Supported and unsupported tags and filters

<!-- generated:template-backend-lists -->
_This block is generated by `scripts/generate-template-backend-lists.py` from the engine's own registries. Do not edit it by hand; run `make template-backend-lists`._

Reference: Django 5.2.16 — `django.template.defaultfilters`, `defaulttags` and `loader_tags` (the engine's `default_builtins`), plus the `i18n`, `l10n`, `tz`, `static`, `cache` libraries.

**Built-in filters — 57 of 57 supported (native):** `add`, `addslashes`, `capfirst`, `center`, `cut`, `date`, `default`, `default_if_none`, `dictsort`, `dictsortreversed`, `divisibleby`, `escape`, `escapejs`, `escapeseq`, `filesizeformat`, `first`, `floatformat`, `force_escape`, `get_digit`, `iriencode`, `join`, `json_script`, `last`, `length`, `linebreaks`, `linebreaksbr`, `linenumbers`, `ljust`, `lower`, `make_list`, `phone2numeric`, `pluralize`, `pprint`, `random`, `rjust`, `safe`, `safeseq`, `slice`, `slugify`, `stringformat`, `striptags`, `time`, `timesince`, `timeuntil`, `title`, `truncatechars`, `truncatechars_html`, `truncatewords`, `truncatewords_html`, `unordered_list`, `upper`, `urlencode`, `urlize`, `urlizetrunc`, `wordcount`, `wordwrap`, `yesno`

**Built-in filters — unsupported (0):** none

**Built-in tags — 18 of 25 supported:**
- native Rust (16): `block`, `comment`, `csrf_token`, `cycle`, `extends`, `firstof`, `for`, `if`, `include`, `load`, `now`, `spaceless`, `templatetag`, `verbatim`, `widthratio`, `with`
- via Python handler (2): `regroup`, `url`

**Built-in tags — unsupported (7):** `autoescape`, `debug`, `filter`, `ifchanged`, `lorem`, `querystring`, `resetcycle`

**Library tags (`{% load … %}`) — supported (1):** `static`

**Library tags — unsupported (17):** i18n `blocktrans`, `blocktranslate`, `get_available_languages`, `get_current_language`, `get_current_language_bidi`, `get_language_info`, `get_language_info_list`, `language`, `trans`, `translate`; l10n `localize`; tz `get_current_timezone`, `localtime`, `timezone`; static `get_media_prefix`, `get_static_prefix`; cache `cache`

**Library filters — native (0):** none

**Library filters — bridged from a configured `DjangoTemplates` engine (9):** i18n `language_bidi`, `language_name`, `language_name_local`, `language_name_translated`; l10n `localize`, `unlocalize`; tz `localtime`, `timezone`, `utc`

Bridged filters are Django's own callables, forwarded to the Rust engine by the filter bridge (#1121) from the `template_libraries` of a `django.template.backends.django.DjangoTemplates` engine in `TEMPLATES` — the fallback engine the recommended configuration above includes. On a djust-only `TEMPLATES` there is nothing to forward and each raises `Unknown filter`.

**Library filters — unsupported (0):** none

**djust extensions (not Django tags, not scored):** `dj_flash`, `djust_client_config`, `djust_markdown`, `djust_offline_indicator`, `djust_pwa_head`, `djust_pwa_manifest`, `djust_sw_register`, `live_render`
<!-- /generated:template-backend-lists -->

### Conformance

The backend is scored against Django's own template test suite: `tests/template_tests` from the `django/django` checkout at the tag matching the installed Django (5.2.16 for the baseline below). An in-process `Engine` subclass routes every engine the suite builds through `DjustTemplateBackend`. Nothing in Django's checkout is edited, and the `TEMPLATES`-configured backend stays Django's own. The engine is reached through the plain-backend path only, not the LiveView path.

- **47.09%** of the Django template tests that reach the engine pass (493 of 1047) <!-- django-suite-claim -->
- Over the whole `template_tests` label the figure is 62.08% (907 of 1461, 9 skipped). That is not the headline: 414 of those tests never reach any engine (`test_parser`, `test_context`, `test_smartif`, ...) and measure Django against itself, so no engine work can move them.

Two result kinds are counted separately because they are different work:

- **ERROR**: the test could not run to an assertion. An unsupported tag, an attribute the backend cannot express, or a crash. Top classes in the baseline (ERROR lines naming the tag): `autoescape` (77), `blocktrans` (27), `ifchanged` (25), `trans` (15), `querystring` (14), `cache` (13). Since #2549 an unsupported tag is refused at `get_template`/`from_string` as `DjustTemplateSyntaxError`, so these lines read `DjustTemplateSyntaxError: Template error: Unsupported template tag …` rather than `Exception: Error rendering template …`. Every Django built-in or library tag in those classes appears in the generated unsupported list above; `scripts/generate-template-backend-lists.py --cross-check .django-src/last-run.txt` reconciles the two. Four generated-unsupported names never appear on the scoreboard because no `template_tests` case reaches them as a tag error — `get_current_timezone`, `localize`, `localtime`, `timezone` — and for those the generator is the authority (a test pins that this is the whole never-exercised set).
- **FAIL**: the test ran and the output was wrong. The largest class was a `TemplateSyntaxError` that Django raises at parse time and djust did not (111 cells); #2549 moved the parse to construction and that class is now 75 cells, every one an engine-grammar gap where djust parses what Django refuses (`{% if %}` operator grammar, `{% url %}` argument parsing, `{{ a b }}`-style variable syntax, `{% include %}` arguments — #2576, #2577, #2578, #2579, #2580) rather than a timing question. A further 32 cells now raise the right type at the right time and fail only on Django's verbatim message text (#2581). The rest are output mismatches such as `string_if_invalid` not honoured (#2518) and `{% if x|default_if_none:y %}` evaluating false when `x` is undefined (#2528). The `<!--dj-if-->` marker that leaked into plain-backend output was fixed in #2519; it accounted for five of the six cases it broke, and the sixth is the #2528 shape.

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

The generated lists above are the authority on what the backend supports. A project's own `{% load %}` libraries need nothing: they are bridged on load (see "Loading a project's template libraries"). For an unsupported Django built-in or library tag, either register a handler for it with `djust.template_tags.register` or route that template to the `DjangoTemplates` backend kept as a fallback in `TEMPLATES`.

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

**Error:** `Unsupported template tag '{% tag_name %}'. Register a handler via djust._rust.register_tag_handler(), or use Django's template engine instead.`

**Solution:** Use the Django backend for that specific template, or register a handler (see Workarounds above).

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

## Contributing

Found a bug or want to add a feature? See `CONTRIBUTING.md`.

## License

MIT License - same as djust framework.

---

**Last Updated:** 2026-02-08
**Status:** Production-ready
**Minimum djust version:** 0.1.0 (with Rust extension compiled)
