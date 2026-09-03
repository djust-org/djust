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
by `pip install djust`; they are simply not imported.

**Extras decision (#2560, 1.2.0).** There is no `djust[templates]` extra and no
change to what `pip install djust` pulls in 1.x. An extra can only *add*
dependencies, so a "templates-only" extra would be a lie, and moving `channels`
and `msgpack` behind `djust[live]` would make a bare install lose the WebSocket
stack — a breaking change that belongs at a 2.0 boundary with a deprecation
cycle. What 1.2.0 ships instead is the additive half: importing the backend no
longer imports the LiveView stack (#2559), and the `C016` check tells you when
your `TEMPLATES` shape needs a `DjangoTemplates` fallback (#2562). The 2.0
migration path, when it comes: `djust[live]` carries `channels[daphne]`,
`msgpack`, and the presence backends; 1.x emits a deprecation warning when the
WebSocket consumer is imported without the extra declared.

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
- Several Django built-in tags and the `cache` library are not implemented; the generated lists below are the authority
- Django's `i18n`, `l10n` and `tz` libraries are bridged on `{% load %}` (#2558): their tags and filters resolve on any `TEMPLATES` shape once the template loads the library — see "Internationalization" below for what that covers and the residues it does not. The three `tz` *filters* (`localtime`, `timezone`, `utc`) are the exception — they need a datetime object the Rust engine cannot carry (#2216) and are refused loudly at load rather than rendering blank (#2541)
- `{% debug %}` renders `""` unless `settings.DEBUG` (Django's own gate), and what it dumps has already been through djust's serialization floor and sidecar proxies — protected model fields never reach it. On the plain backend a model shows as its serialized dict rather than its repr (#2590)
- `{% querystring %}` reads `request.GET` from the render: the plain backend carries a `RequestContext`'s request or the `request=` argument, and the LiveView WebSocket path carries the mount-time request; the LiveView GET page-shell wires no request into the render (pre-existing, #2589), so pass an explicit `QueryDict` there. `{% querystring … as var %}` is refused until #2591
- A project's own `{% load %}` tag libraries ARE loaded (#2547, see "Loading a project's template libraries" below); a raw `@register.tag` that consumes a body is the one shape that is refused
- `{% url %}` raises `NoReverseMatch` on a failed reverse, as Django does, and `{% url … as var %}` stores `''` in the variable instead of raising (#2563). Both hold on the plain backend and on the LiveView path, for a quoted name and for a variable name, and the message is Django's (`Reverse for 'x' with no arguments not found. 1 pattern(s) tried: […]`). There is no fail-soft switch: a blank `href` is exactly the broken link the exception exists to surface, and `as var` is the escape hatch. Three differences remain:
  - a `{% url 'quoted-name' … %}` is resolved by a pre-pass BEFORE the template is parsed, so one inside a never-taken `{% if %}` branch or a `{% comment %}` block still raises;
  - the traceback has fewer Python frames than Django's — the handler's frames (`UrlTagHandler.render` → `reverse` → `_reverse_with_prefix`) survive the Rust boundary, but the Rust engine contributes no `Template.render` / `NodeList.render` / `URLNode.render` frames, so Django's `test_url_reverse_view_name` (which asserts a traceback depth > 5 as its proxy for "the original stack trace was kept") fails on shape alone;
  - `{% url name_var arg %}` where the resolved name is ALSO a context key re-resolves it (#2037 name-position double resolution; Django's `test_url19`): the reverse fails honestly now instead of rendering `''`.

### Loading a project's template libraries

`{% load app_tags %}` imports the library the way Django does and bridges it into the Rust engine (#2547): the name resolves through Django's own library map — every installed app's `templatetags/` package (`get_installed_libraries()`) plus the backend's `OPTIONS['libraries']` — the module is imported with `django.template.library.import_library`, and every entry is registered. `{% load a b %}` and `{% load x y from lib %}` behave as on Django; an unknown name raises Django's exact `TemplateSyntaxError` at parse time.

- **Filters** go through the same bridge as before (`is_safe` / `needs_autoescape` honoured; an `is_safe=True` filter keeps a safe input safe and never makes a hostile one safe).
- **Tags** — `simple_tag`, `simple_block_tag`, `inclusion_tag`, and raw `@register.tag` functions that build a node from their own token — are rendered by the library's OWN compile function and Django node, so arguments, keyword arguments, `takes_context`, `as var`, escaping and inclusion-template rendering are Django's, byte for byte. A `takes_context` tag sees the template context as a plain dict.
- **Refused, per tag:** a raw `@register.tag` whose compile function reads past its own token (`parser.parse((...))`, `next_token`, `skip_past`) has no token stream to consume here. The `{% load %}` succeeds and the rest of the library works; the moment a template uses that one tag the parser raises `TemplateSyntaxError: '<tag>' from library '<lib>' is a raw @register.tag that consumes a block … port it to @register.simple_block_tag, or wait for the raw-body registration kind (#2558)`. Port it to `simple_block_tag`.
- **Django's own libraries:** `i18n`, `l10n` and `tz` are bridged the same way (#2558), except that `{% blocktranslate %}` crosses its body as raw source and `{% language %}` / `{% localize %}` / `{% localtime %}` / `{% timezone %}` are native scope nodes the load arms. `static`, `cache` and djust's own `djust.templatetags.*` are not bridged, deliberately: they resolve — `{% load static %}` keeps parsing — but their tags come from the engine's native handlers, each its own row in the generated lists below.
- **`OPTIONS['builtins']`** (dotted library paths) are bridged when the backend is constructed, so their tags and filters need no `{% load %}` — Django's meaning. Django's three default builtins are the Rust natives and are skipped.
- **Scoping differs from Django.** Django scopes a loaded library to the template that loaded it; djust's registries are process-global, so once any render loads a library every later template on either engine path (plain backend or LiveView) sees its tags, `{% load %}` or not. A missing library is still refused. A test suite that clears the Rust tag registries must call `djust.template_libraries.reassert()` afterwards (the framework's own `djust.test_isolation` does): the Rust template cache is keyed by source, so a template parsed while the tags were registered is served from cache and never re-runs its `{% load %}`.
- **Known gaps:** `context.use_l10n` / `autoescape` are not carried into a bridged tag's `Context` (#2586); the scoreboard's `test_no_render_side_effect` reads `template.nodelist`, a Django-internal attribute the backend does not have — a deliberate, documented ERROR (#2587).

### Internationalization

`{% load i18n %}` (and `l10n` / `tz`) brings Django's own i18n tags into the
Rust engine (#2558). The catalogs, the plural rules and the escaping are
Django's — the bridge runs Django's compile functions and nodes rather than
reimplementing them — so `{% translate %}`, `{% blocktranslate %}`, the
`_("…")` literal, the five `get_*` tags and the four `language_*` filters
render byte-for-byte what Django renders, on the plain backend and inside a
LiveView alike. All 116 cells of Django's own `syntax_tests/i18n` pass.

```django
{% load i18n %}
{% translate "Page not found" %}
{% translate "May" context "month name" as month %}
{% blocktranslate count n=items|length trimmed %}
  {{ n }} item
{% plural %}
  {{ n }} items
{% endblocktranslate %}
{{ _("Password") }}
{% language "de" %}{{ price }} — {% translate "Yes" %}{% endlanguage %}
```

Three things are worth knowing about how it works:

- **The active language is read per render, on the render thread.** Nothing is
  captured at registration, so `translation.override(...)`, a
  `LocaleMiddleware`-set language and a per-user language all work as they do
  on Django — including inside a WebSocket event, where each render re-reads it.
- **`{% blocktranslate %}`'s body crosses to Django as SOURCE, not as
  rendered output.** The body is the msgid: Django's own lexer turns
  `{{ var }}` into `%(var)s` and doubles every literal `%`, and it is Django's
  parser that refuses a `{% block %}` or `{% for %}` inside the block, with its
  own message. That is why the tag needed a fourth registration kind rather
  than the existing block-tag bridge, which pre-renders bodies.
- **`{% language %}`, `{% localize %}`, `{% localtime %}` and `{% timezone %}`
  are native Rust nodes.** They wrap children the Rust engine renders, so they
  switch Python's thread-local (that is where `gettext` and
  `get_current_timezone` read from) and re-push the locale/zone state to Rust
  around the block — which is what makes `{{ n }}` inside
  `{% language "de" %}` format as German. The switch is restored on the way
  out, including when a child raises.

Escaping follows Django's rule that a node's output is final: a catalog string
and a template literal are author content and render raw (`{% translate "<b>" %}`
→ `<b>`), while an interpolated VARIABLE is escaped (`{% translate hostile %}`
and `{% blocktranslate %}{{ hostile }}{% endblocktranslate %}` both escape).
Nothing user-controlled can become a msgid except through
`{% translate var %}`, whose output is escaped whatever the catalog answers.

Residues, all of them named tests rather than silent gaps:

| shape | djust | why |
|---|---|---|
| `{% autoescape off %}{% translate var %}` | refuses the `autoescape` tag | `{% autoescape %}` is not implemented yet (#2556); until it is, the bridge builds its `Context` with `autoescape=True` |
| `{{ dt\|localtime }}`, `\|utc`, `\|timezone:"…"` | raises, naming the filter | a datetime crosses the wire as its ISO string (#2216), so the filter has no datetime object; use `{{ dt\|date:"…" }}`, which converts to the active zone (#2209) |
| `{% localize off %}{{ some_date }}` | ISO, not the raw `DATE_FORMAT` | same date-wire residue (#2221 piece 3); the NUMBER half of `{% localize %}` is exact |
| `{% localtime on %}` under `USE_TZ = False` | no conversion | Django's `on` forces conversion even with `USE_TZ` off; djust's `on` keeps whatever the render env pushed. `off` and the whole `USE_TZ = True` matrix agree |
| `OPTIONS['string_if_invalid']` | not honoured by the plain backend | engine-wide (#2518), not i18n-specific: the raw-block handler reads the value it is given, and the scoreboard's engine (a real Django `Engine`) does carry it |

### Supported and unsupported tags and filters

<!-- generated:template-backend-lists -->
_This block is generated by `scripts/generate-template-backend-lists.py` from the engine's own registries. Do not edit it by hand; run `make template-backend-lists`._

Reference: Django 5.2.16 — `django.template.defaultfilters`, `defaulttags` and `loader_tags` (the engine's `default_builtins`), plus the `i18n`, `l10n`, `tz`, `static`, `cache` libraries.

**Built-in filters — 57 of 57 supported (native):** `add`, `addslashes`, `capfirst`, `center`, `cut`, `date`, `default`, `default_if_none`, `dictsort`, `dictsortreversed`, `divisibleby`, `escape`, `escapejs`, `escapeseq`, `filesizeformat`, `first`, `floatformat`, `force_escape`, `get_digit`, `iriencode`, `join`, `json_script`, `last`, `length`, `linebreaks`, `linebreaksbr`, `linenumbers`, `ljust`, `lower`, `make_list`, `phone2numeric`, `pluralize`, `pprint`, `random`, `rjust`, `safe`, `safeseq`, `slice`, `slugify`, `stringformat`, `striptags`, `time`, `timesince`, `timeuntil`, `title`, `truncatechars`, `truncatechars_html`, `truncatewords`, `truncatewords_html`, `unordered_list`, `upper`, `urlencode`, `urlize`, `urlizetrunc`, `wordcount`, `wordwrap`, `yesno`

**Built-in filters — unsupported (0):** none

**Built-in tags — 24 of 25 supported:**
- native Rust (19): `autoescape`, `block`, `comment`, `csrf_token`, `cycle`, `extends`, `filter`, `firstof`, `for`, `if`, `include`, `load`, `now`, `resetcycle`, `spaceless`, `templatetag`, `verbatim`, `widthratio`, `with`
- via Python handler (5): `debug`, `lorem`, `querystring`, `regroup`, `url`

**Built-in tags — unsupported (1):** `ifchanged`

**Library tags (`{% load … %}`) — supported (15):** i18n `blocktrans`, `blocktranslate`, `get_available_languages`, `get_current_language`, `get_current_language_bidi`, `get_language_info`, `get_language_info_list`, `language`, `trans`, `translate`; l10n `localize`; tz `get_current_timezone`, `localtime`, `timezone`; static `static`

**Library tags — unsupported (3):** static `get_media_prefix`, `get_static_prefix`; cache `cache`

A supported library tag is either a native Rust node or bridged on `{% load %}` (#2547 / #2558): the load imports Django's library and registers its tags with the Rust engine, so the tag is rendered by Django's own compile function and node — `{% blocktranslate %}` crosses its body as raw source; `{% language %}`, `{% localize %}`, `{% localtime %}` and `{% timezone %}` are native scope nodes the load arms.

**Library filters — native (0):** none

**Library filters — bridged on `{% load %}` (6):** i18n `language_bidi`, `language_name`, `language_name_local`, `language_name_translated`; l10n `localize`, `unlocalize`

Bridged filters are Django's own callables, forwarded to the Rust engine by the filter bridge when the template loads their library (#2558) — on any `TEMPLATES` shape, a `DjangoTemplates` engine beside djust or not.

**Library filters — unsupported (3):** tz `localtime`, `timezone`, `utc`

Refused loudly on `{% load %}` (3): tz `localtime`, `timezone`, `utc` — each needs a datetime object on the wire, which the Rust `Value` cannot carry (#2216), so the load registers it as a filter that raises `TemplateSyntaxError` naming the filter and pointing at `date` with the active zone (#2209) — never a silent blank (#2541).

**djust extensions (not Django tags, not scored):** `dj_flash`, `djust_client_config`, `djust_markdown`, `djust_offline_indicator`, `djust_pwa_head`, `djust_pwa_manifest`, `djust_sw_register`, `live_render`
<!-- /generated:template-backend-lists -->

### Conformance

The backend is scored against Django's own template test suite: `tests/template_tests` from the `django/django` checkout at the tag matching the installed Django (5.2.16 for the baseline below). An in-process `Engine` subclass routes every engine the suite builds through `DjustTemplateBackend`. Nothing in Django's checkout is edited, and the `TEMPLATES`-configured backend stays Django's own. The engine is reached through the plain-backend path only, not the LiveView path.

- **72.59%** of the Django template tests that reach the engine pass (760 of 1047) <!-- django-suite-claim -->
- Over the whole `template_tests` label the figure is 80.29% (1169 of 1456, 14 skipped). That is not the headline: 409 of those tests never reach any engine (`test_parser`, `test_context`, `test_smartif`, ...) and measure Django against itself, so no engine work can move them. (The skip count follows the environment: five of the fourteen are `jinja2` tests, and `jinja2` is not in the lockfile.)

Two result kinds are counted separately because they are different work:

- **ERROR**: the test could not run to an assertion. An unsupported tag, an attribute the backend cannot express, or a crash. Top classes in the baseline (ERROR lines naming the tag): `ifchanged` (25), `cache` (13), then `get_static_prefix` and `get_media_prefix` (2 each) — and that is the whole list, 42 of the 127 ERROR cells. `autoescape` (77 cells) left it in #2556; `blocktrans` (27) and `trans` (15) left it in #2558, along with every other `i18n` / `l10n` / `tz` tag error; `querystring` (14), `resetcycle` (7) and `debug` / `filter` / `lorem` (5 each) left it in #2596. Since #2549 an unsupported tag is refused at `get_template`/`from_string` as `DjustTemplateSyntaxError`, so these lines read `DjustTemplateSyntaxError: Template error: Unsupported template tag …` rather than `Exception: Error rendering template …`. Every Django built-in or library tag in those classes appears in the generated unsupported list above; `scripts/generate-template-backend-lists.py --cross-check .django-src/last-run.txt` reconciles the two. Until #2558 four generated-unsupported names never appeared on the scoreboard because no `template_tests` case reached them as a tag error — `get_current_timezone`, `localize`, `localtime`, `timezone`. All four are supported now, so the never-exercised set is empty and every generated-unsupported name is one the suite actually reaches (a test pins that, and a fifth such name appearing must move this sentence).
- **FAIL**: the test ran and the output was wrong. The largest class was a `TemplateSyntaxError` that Django raises at parse time and djust did not (111 cells); #2549 moved the parse to construction and that class is now 65 cells (#2557 took four: the two empty-variable-tag cells `{{ }}` and `{{        }}`, and the two empty-block-tag cells `{% %}` — all refused in `Parser.parse` (`django/template/base.py:483-486` and `:497`), NOT in the lexer, which returns `Token(TokenType.VAR, "")`; the placement is what lets a `{% verbatim %}` body hold `{{ }}` literally), every one an engine-grammar gap where djust parses what Django refuses (`{% if %}` operator grammar, `{% url %}` argument parsing, `{{ a b }}`-style variable syntax, `{% include %}` arguments — #2576, #2577, #2578, #2579, #2580) rather than a timing question. Grepping `… not raised` returns 70, not 65: the other **5 are a different class** — a RENDER-time exception Django propagates and djust swallows (`RuntimeError` ×3, `NoReverseMatch` ×2, in `TemplateTests` / `DebugTemplateTests` `test_compile_tag_error` and `test_super_errors`, and `IncludeTagTests.test_include_fail1`. The two `ZeroDivisionError` cells `test_no_wrapped_exception` are still the same class and still unfixed; #2620 moved them from FAIL to ERROR (`Exception: Error rendering template: division by zero`), so they no longer answer the `not raised` grep) — not a grammar gap, and tracked separately (#2617). A further 20 cells raise the right type at the right time and fail only on Django's verbatim message text (#2581) — the FAIL lines whose assertion reads `"<expected>" not found in <actual>`; #2558's tags are not among them, because they carry Django's own text, produced by Django's own compile functions. The rest are output mismatches such as `string_if_invalid` not honoured (#2518) and `{% if x|default_if_none:y %}` evaluating false when `x` is undefined (#2528). The `<!--dj-if-->` marker that leaked into plain-backend output was fixed in #2519; it accounted for five of the six cases it broke, and the sixth is the #2528 shape.

**No `template_tests` case segfaults the interpreter any more.** Seven did — a `DjustTemplate` or a type object placed in the context, the #2516 reference-cycle class — and all seven stopped when ADR-027's lazy resolution became the default in #2539. Two mechanisms did it, and neither was aimed at the crashes: nothing walks an object's `__dict__` eagerly, so a reference cycle has no unbounded recursion to overflow, and the segment walk carries Django's metaclass guard, so a `list`-subclass class object never reaches `__class_getitem__`. Only one of the seven (`test_subscriptable_class`) reaches OK; the other six still ERROR for unrelated reasons, but they no longer take the process with them.

The isolation machinery stays, because a crash is a thing a future change can reintroduce and the runner is what keeps it from costing the rest of the run: a crash records the in-flight test as `ERROR: process crashed`, skips it and every finished test, and relaunches, so nothing after a crash is lost. `crashes` in the baseline JSON is now `[]`, which is what a regression would have to grow.

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

### Where a template error happened

A template the engine cannot parse is refused where Django refuses it — at
`get_template()` / `from_string()`, not at the first `render()` (#2549) — and
the exception carries Django's `template_debug` dict, so with `DEBUG = True`
the technical-500 page shows the template name, the line number and a source
excerpt with the offending token highlighted (#2557):

```
In template /app/templates/broken.html, error at line 3
Template error: Unsupported template tag '{% badtagname %}'. …

   1 : line one
   2 : line two
   3 : {% badtagname %}     <- highlighted
   4 : line four
```

The dict is the one Django builds in `Template.get_exception_info`, key for
key — `name`, `line`, `during`, `before`, `after`, `source_lines`, `top`,
`bottom`, `total`, `start`, `end`, `message` — so any tool that reads
`exc.template_debug` (Django's own debug view, `ExceptionReporter`, a custom
500 handler) works unchanged. To read it yourself:

```python
from django.template import TemplateSyntaxError

try:
    engine.get_template("broken.html")
except TemplateSyntaxError as exc:
    debug = exc.template_debug          # None if the engine could not locate it
    print(debug["name"], debug["line"], debug["during"])
```

`template_debug` is `None` — never absent — when the engine cannot say where
the failure was. Two cases do that today: `{% cycle %}` / `{% resetcycle %}`
binding errors, which are raised while walking the parsed AST rather than the
token stream, and every error raised during `render()` rather than during the
parse. Django's debug view renders the plain traceback for a `None`, which is
what those cases get. Render-time locations need the per-node origin Django
keeps on `Node.origin` / `Node.token`; that is tracked separately.

One place djust deliberately answers where Django would not: a token whose
span crosses a newline. Django's `tag_re` has no `re.DOTALL`, so a Django
token never spans a line and `get_exception_info`'s locating condition always
fires; djust's lexer has no such bound and the engine accepts a multi-line tag
(`{% if x`⏎`   %}`), plus an unterminated `{%` whose scan reaches the next
`%}` anywhere in the file. Porting the condition unchanged made those fall
through to `line: 0` with the excerpt clamped to the first ten lines — a
confidently wrong location, which is worse than the `None` it replaced. Those
are re-located on the line the token STARTS on, with the highlight clamped to
that line's end: the line `Token.lineno` names, and the one a developer would
point at.

A `name` of `None` — which `from_string()` produces, since it supplies no
origin — renders as `<unknown source>`, Django's own `UNKNOWN_SOURCE`, not a
literal `None`.

Two other things follow from parsing at construction: the *message* text is
djust's own, not Django's (it names the tag and the registration API), and an
error in a branch that never renders — `{% if False %}{% unknown %}{% endif %}`
— is refused, as on Django.

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
