# Scaffolding Generator

Generate a complete CRUD LiveView from a model name and field definitions.

## Quick Start

```bash
python manage.py djust_gen_live blog Post title:string body:text published:boolean
```

This creates:

| File | Description |
|------|-------------|
| `blog/views.py` | `PostListView` with mount, search, show, create, update, delete handlers |
| `blog/urls.py` | URL routing using `live_session()` |
| `blog/templates/blog/post_list.html` | List + detail panel with `dj-*` directives |
| `blog/tests.py` | Basic test scaffold |

## Usage

```
python manage.py djust_gen_live <app_name> <ModelName> [field:type ...] [options]
```

### Arguments

| Argument | Description |
|----------|-------------|
| `app_name` | Django app name (directory must exist) |
| `model_name` | PascalCase model name (e.g. `Post`, `BlogPost`) |
| `fields` | Field definitions as `name:type` pairs |

### Options

| Option | Description |
|--------|-------------|
| `--dry-run` | Preview files without writing |
| `--force` | Overwrite existing files |
| `--no-tests` | Skip generating test file |
| `--api` | Generate JSON API (`render_json`) instead of HTML |

## Supported Field Types

| Type | Django Model Field | Form Input |
|------|-------------------|------------|
| `string` | `CharField` | `<input type="text">` |
| `text` | `TextField` | `<textarea>` |
| `integer` | `IntegerField` | `<input type="number">` |
| `float` | `FloatField` | `<input type="number" step="any">` |
| `decimal` | `DecimalField` | `<input type="number" step="0.01">` |
| `boolean` | `BooleanField` | `<input type="checkbox">` |
| `date` | `DateField` | `<input type="date">` |
| `datetime` | `DateTimeField` | `<input type="datetime-local">` |
| `email` | `EmailField` | `<input type="email">` |
| `url` | `URLField` | `<input type="url">` |
| `slug` | `SlugField` | `<input type="text">` |
| `fk:Model` | `ForeignKey` | `<input type="number">` (ID) |

## Examples

### Basic CRUD

```bash
python manage.py djust_gen_live blog Post title:string body:text
```

### With Foreign Key

```bash
python manage.py djust_gen_live blog Post title:string body:text author:fk:User
```

### Preview Without Writing

```bash
python manage.py djust_gen_live blog Post title:string --dry-run
```

### JSON API Mode

```bash
python manage.py djust_gen_live blog Post title:string body:text --api
```

### Overwrite Existing

```bash
python manage.py djust_gen_live blog Post title:string --force
```

## Generated Code Patterns

### Views

The generated view uses standard djust patterns:

- `mount()` initializes state
- `_compute()` re-queries the database
- `@event_handler()` decorates all event handlers
- Search uses `Q` objects for OR logic across text fields
- CRUD operations: `create`, `show`, `update`, `delete`

### URLs

Routes use `live_session()` for proper WebSocket support:

```python
from djust.routing import live_session

urlpatterns = [
    *live_session("/blog", [
        path("post/", PostListView.as_view(), name="post_list"),
    ]),
]
```

### Templates

Generated templates use djust directives:

- `dj-root` / `dj-view` for LiveView binding
- `dj-input` for real-time search
- `dj-click` / `dj-submit` for event handlers
- `dj-value-*` for passing parameters
- `dj-confirm` for delete confirmation
- `dj-loading` for loading states

## After Generation

1. Add your app to `INSTALLED_APPS`
2. Add `'yourapp.views'` to `LIVEVIEW_ALLOWED_MODULES`
3. Include `yourapp.urls` in your root URL conf
4. Create the model in `yourapp/models.py`
5. Run `python manage.py makemigrations && python manage.py migrate`

---

## Project & app scaffolding (`djust new`, `startproject`, `startapp`)

Beyond per-model CRUD generation, the `djust` CLI ships with three
commands for bootstrapping projects and apps end-to-end (added in
v0.3.0):

```bash
# Modern entrypoint (recommended) — feature flags select what to wire
python -m djust new myapp

# Pre-canned feature combos via flags
python -m djust new myapp --with-auth --with-db --with-presence --with-streaming

# Generate from a YAML schema describing your models
python -m djust new myapp --from-schema schema.yml

# Legacy entrypoints (still supported)
python -m djust startproject myproject
python -m djust startapp myapp
```

### `djust new`

`djust new <name>` creates a full Django project layout pre-configured
for djust:

| What you get | Default | Toggled by |
|---|---|---|
| Django project + initial app | always | — |
| `LIVEVIEW_CONFIG` settings stub | always | — |
| WebSocket routing wired into `asgi.py` | always | — |
| Auth + login/logout LiveViews | off | `--with-auth` |
| Postgres `LISTEN/NOTIFY` wiring | off | `--with-db` |
| `PresenceMixin` example | off | `--with-presence` |
| Stream-friendly base templates | off | `--with-streaming` |
| Models generated from a schema file | off | `--from-schema schema.yml` |

The `--from-schema` mode reads a small YAML file describing models +
fields and generates models, admin, migrations, LiveViews, and
templates in one step — handy for spike projects.

### `startproject` / `startapp`

The legacy commands mirror Django's vanilla `django-admin startproject`
/ `startapp` but add djust's defaults (`LIVEVIEW_CONFIG`,
`LIVEVIEW_ALLOWED_MODULES`, `LiveSessionMiddleware`, the WS routing
include). Reach for these when you want explicit Django parity over
the curated `djust new` experience.
