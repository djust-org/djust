---
title: "Scaffolding Generator"
slug: scaffolding
section: guides
order: 15
level: beginner
description: "Generate a complete CRUD LiveView from a model name and field definitions using djust_gen_live."
---

# Scaffolding Generator

Generate a complete CRUD LiveView from a model name and field definitions.

For a new Django project, start with [Installation](../getting-started/installation.md#create-a-new-project). The commands below run inside an existing project with djust installed.

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

## Project and app scaffolding

Beyond per-model CRUD generation, the `djust` CLI ships three commands for
bootstrapping whole projects and apps:

```bash
# Use the latest published CLI, even if an older tool is installed
uvx djust@latest new myapp

# Pre-canned feature combos
uvx djust@latest new myapp --with-auth --with-db --with-presence --with-streaming

# Generate models, admin, migrations and views from a JSON schema
uvx djust@latest new myapp --from-schema schema.json

# Add djust to an existing Django project (run beside manage.py)
uvx djust@latest init

# Legacy entrypoints, mirroring Django's own names
python -m djust startproject myproject
python -m djust startapp myapp
```

### `djust new`

`djust new <name>` creates a full Django project layout pre-configured for
djust:

| What you get | Default | Toggled by |
|---|---|---|
| Django project + initial app | always | — |
| Template backend and `LIVEVIEW_ALLOWED_MODULES` | always | — |
| WebSocket routing wired into `asgi.py` | always | — |
| Auth + login/logout LiveViews | off | `--with-auth` |
| Django models, admin, and database-backed views | off | `--with-db` |
| `PresenceMixin` example | off | `--with-presence` |
| Stream-friendly base templates | off | `--with-streaming` |
| Models generated from a JSON schema | off | `--from-schema schema.json` |
| Create `.venv`, install dependencies, migrate, and check | on | Skip with `--no-setup` |

`--from-schema` reads a small JSON file describing models and fields, then
generates models, admin, migrations, LiveViews and templates in one step.
Handy for spikes.

Setup installs only into the new project's `.venv`, even when another
environment is active, and stops at the first failed step. Afterwards, start
the server with:

```bash
cd myapp
make dev
```

The generated `Makefile` runs the project's `.venv` interpreter, so no
activation is needed. With `--no-setup`, `djust new` prints the setup commands
to run before `make dev`.

### `djust init`

`djust init` adds djust to the Django project in the current directory: a
marked settings block, a djust `asgi.py` (only when the existing one is
Django's default), the `djust`, `channels`, and `uvicorn[standard]` packages,
and a final `manage.py check`.

| Option | Effect |
|---|---|
| `--dry-run` | Print the diff and the install command without changing anything |
| `--no-install` | Edit files only; skip installing packages and the check |
| `--force` | Edit files even if they have uncommitted changes in git |
| `--settings MODULE` | Settings module to edit when `manage.py` does not name it |

Exit status is `0` on success, `1` when `init` refused before writing, and
`2` when files were written but a step needs attention (a customized
`asgi.py`, a failed install, or a failed check). The
[installation guide](../getting-started/installation.md#add-djust-to-an-existing-project)
describes each change.

### `startproject` and `startapp`

`python -m djust startproject` is a deprecated alias for `djust new`.
Use `djust new` for a complete project. `python -m djust startapp myapp`
generates an app with a starter view and template inside an existing project;
you still need to register the app and include its URLs.

## AI agent discovery

Generated projects include an `AGENTS.md` pointing at the canonical
[application conventions](../../ai/conventions.md) and the focused API
references. Add project-specific rules there. Note that `djust new` does not
rewrite an existing project directory; for an existing application, add the
pointer to whatever agent entry file it already has. Agents do not
automatically read a dependency's docs.
