# Installation

**tl;dr**

```bash
# New project
uvx djust@latest new myproject && cd myproject && make dev

# Existing Django project (run beside manage.py)
uvx djust@latest init
```

Then open **http://127.0.0.1:8000/**.

Create a new project with one command, or add djust to a Django project you
already have with another. Both paths set up the pieces described in
[Configure by hand](#configure-by-hand), and use the same names as
[Your First LiveView](./first-liveview.md).

| Starting point | Follow this path |
| --- | --- |
| New project | [Create a new project](#create-a-new-project) |
| Existing Django project | [Add djust to an existing project](#add-djust-to-an-existing-project) |
| Set up each piece yourself | [Configure by hand](#configure-by-hand) |

## Requirements

- Python 3.10 or newer.
- [uv](https://docs.astral.sh/uv/getting-started/installation/) for environment and package management.
- Django and Channels are installed with djust; you do not need to install
  Django globally first.
- Rust is only needed when building djust from source. Published wheels avoid
  a local Rust build on supported platforms.

Shell examples use macOS/Linux. Windows differences are noted where they apply.

## Create a new project

Run this from the **parent directory** where you want the new project:

```bash
uvx djust@latest new myproject
cd myproject
make dev
```

Open **http://127.0.0.1:8000/**. The starter page has a live list and a
theme switcher: add an item, then change the theme, and check that both
update without a page reload. Pass `--bare` for a one-button placeholder page
instead.

`djust new` creates `myproject/`, makes a `.venv` inside it, installs the
project's requirements into that environment, runs migrations, and finishes
with `manage.py check`. If any step fails, it stops and reports the error
instead of printing next steps. `make dev` runs Uvicorn with the project's
`.venv`, so there is nothing to activate. On Windows, which has no `make`, run
the command `djust new` prints instead:
`.venv\Scripts\python -m uvicorn myproject.asgi:application --reload`.

`@latest` is intentional: it requests the current published release instead
of reusing an older globally installed or cached CLI; see
[uv's tool-version behavior](https://docs.astral.sh/uv/concepts/tools/#tool-versions).
You do not need djust installed globally. If you scaffold projects often,
`uv tool install djust` gives you a plain `djust` command; run
`uv tool upgrade djust` before creating a project so it uses the current release.
Choose a new directory name in a parent folder such as `~/projects`; do not
run it inside another project or its Python package.

### What the scaffolder sets up

```text
myproject/
├── manage.py
├── requirements.txt
├── Makefile
├── AGENTS.md
├── .env
├── .env.example
├── .venv/
├── templates/
└── myproject/
    ├── settings.py
    ├── asgi.py
    ├── urls.py
    ├── views.py
    └── templates/myproject/
        ├── base.html
        └── index.html
```

The generated package contains both project configuration and the starter
app. It includes the template backend, allowed LiveView modules, WebSocket
routing, local static-file serving, and development settings. SQLite is the
initial database; the default starter's list lives in LiveView state.

### Choose features at creation time

```bash
uvx djust@latest new myproject --with-auth --with-db
```

| Flag | Adds |
| --- | --- |
| `--with-auth` | Login/logout views and authentication settings |
| `--with-db` | Django models, admin, and database-backed views |
| `--with-presence` | Online-user presence example |
| `--with-streaming` | Live-feed streaming example |
| `--from-schema schema.json` | Models and views from a JSON schema; implies `--with-db` |
| `--bare` | A placeholder page with one live button instead of the themed demo |
| `--no-setup` | Generate files only; the command prints the setup steps to run yourself |

You can now edit the generated `myproject/views.py`, or follow
[Your First LiveView](./first-liveview.md) to create a separate counter app.
For model-by-model CRUD generation and schema options, see
[Scaffolding Generator](../guides/scaffolding.md).

## Add djust to an existing project

Run this from the directory containing `manage.py`:

```bash
uvx djust@latest init
```

To see the changes first without writing anything, add `--dry-run`.

| Part | What `djust init` does |
| --- | --- |
| `settings.py` | Appends a marked block that adds `channels` and `djust` to `INSTALLED_APPS` (unless already listed, by name or AppConfig path), sets `ASGI_APPLICATION`, and sets an in-memory `CHANNEL_LAYERS` unless you already configure one. `TEMPLATES` is left alone: LiveViews render their templates with djust's engine regardless, and everything else keeps rendering as before. Running `init` again leaves the file unchanged. |
| `asgi.py` | Replaces Django's default file with one that routes LiveView WebSockets and serves static files under Uvicorn. A customized `asgi.py`, or a default one pointing at a different settings module, is left alone; `init` prints the code to merge instead. |
| Packages | Adds `djust`, `channels`, and `uvicorn[standard]`: with `uv add` in a uv project, or by adding missing lines to `requirements.txt` and installing into the project's `.venv`. For Poetry, or a project with neither, it prints the command to run. |
| Check | Runs `manage.py check` once the packages are installed. |

When it finishes, `init` prints the command that starts the server, such as
`uv run uvicorn mysite.asgi:application --reload`. The project has no
LiveViews yet, so continue with [Your First LiveView](./first-liveview.md).

A few things `init` refuses to guess:

- **Uncommitted changes.** In a git repository, `init` will not edit a file
  that has uncommitted changes, so its edit is easy to review. Commit first,
  or pass `--force`.
- **Where to install.** Packages go only into the project's own `.venv`, or
  into an active environment located inside the project. Otherwise `init`
  prints the install command and skips the check.
- **Settings packages.** When settings live in a package such as
  `mysite/settings/` or `config/settings/local.py`, `init` prints the block to
  add to the module you load.
  If `manage.py` does not name the settings module, pass `--settings mysite.settings`.

`init` exits with status `0` when everything is done, `1` when it refused
before writing anything, and `2` when it wrote files but something needs your
attention, such as a customized `asgi.py` or a failed check.

To start from nothing with Django's own layout, create the project with uv
and then run `init`. djust supports Django versions below 6, so pin the range
when adding Django:

```bash
mkdir mysite
cd mysite
uv init --bare
uv add "django>=5.2,<6"
uv run django-admin startproject mysite .
uv run python manage.py migrate
uvx djust@latest init
```

`migrate` creates the session tables LiveViews use. `init` does not run
migrations, because in an existing project that would change whichever
database the environment points at.

## Configure by hand

This is what `djust new` and `djust init` do, one piece at a time. Follow it
to see how the parts fit together, or when your project layout is one
`djust init` does not edit, such as a settings package.

A **project** contains site settings and root URLs. An **app** contains a
feature's views, models, and templates. Here the project is `myproject` and
the app is `myapp`.

### 1. Create an environment and install packages

```bash
mkdir myproject
cd myproject
uv venv --python 3.12
source .venv/bin/activate
uv pip install --python .venv "Django>=5.2,<5.3" djust "uvicorn[standard]"
```

In Windows PowerShell, activate the environment with
`.venv\Scripts\Activate.ps1` instead of `source .venv/bin/activate`.

### 2. Create the project and app

Run these in that same directory. The trailing `.` puts `manage.py` in the
current directory instead of creating another outer project folder.

```bash
python -m django startproject myproject .
python manage.py startapp myapp
```

You now have `manage.py`, `myproject/settings.py`, and `myapp/views.py`.
Continue with the configuration below. For a fuller introduction to Django's
project/app distinction, see the [Django tutorial](https://docs.djangoproject.com/en/5.2/intro/tutorial01/).

### Existing projects

Skip steps 1 and 2. Install the packages with the project's dependency
manager, for example `uv add djust "uvicorn[standard]"`, and use your own
project and app names in place of `myproject` and `myapp` below.

### 3. Configure Django

In `myproject/settings.py`, retain Django's generated apps and middleware.
Add the following entries to `INSTALLED_APPS` if they are not already present:

```python
INSTALLED_APPS += [
    "channels",
    "djust",
    "myapp",
]

ASGI_APPLICATION = "myproject.asgi.application"

LIVEVIEW_ALLOWED_MODULES = ["myapp.views"]

# Suitable for one local development process.
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer",
    },
}
```

Optionally, render your other templates with djust's engine too by
registering its backend **before** the existing Django backend. LiveViews do
not need this step: they render with djust's engine either way, and
`djust init` skips it. If the project uses the Django admin, skip it for now:
with this order the admin's list and edit pages fail
([#2872](https://github.com/djust-org/djust/issues/2872)).

```python
TEMPLATES.insert(0, {
    "NAME": "djust",
    "BACKEND": "djust.template_backend.DjustTemplateBackend",
    "DIRS": [BASE_DIR / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {
        "context_processors": [
            "django.template.context_processors.request",
            "django.contrib.auth.context_processors.auth",
            "django.contrib.messages.context_processors.messages",
        ],
    },
})
```

Keep `django.contrib.staticfiles` installed and `STATIC_URL = "static/"`.
Both are included by Django's `startproject`. For local development, keep
`DEBUG = True` and set `ALLOWED_HOSTS = ["localhost", "127.0.0.1"]`.

### 4. Set up ASGI and WebSockets

Replace `myproject/asgi.py` with the following. Initialize Django before
importing the LiveView consumer so its app registry is ready.

```python
import os
from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myproject.settings")
django_asgi_app = get_asgi_application()

from django.conf import settings
from django.contrib.staticfiles.handlers import ASGIStaticFilesHandler
from django.urls import path
from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import AllowedHostsOriginValidator
from djust.websocket import LiveViewConsumer

application = ProtocolTypeRouter({
    "http": (
        ASGIStaticFilesHandler(django_asgi_app)
        if settings.DEBUG else django_asgi_app
    ),
    "websocket": AllowedHostsOriginValidator(
        AuthMiddlewareStack(
            URLRouter([
                path("ws/live/", LiveViewConsumer.as_asgi()),
            ])
        )
    ),
})
```

The static-file handler serves the djust client and app assets under Uvicorn
in development. The origin validator checks WebSocket origins against
`ALLOWED_HOSTS`. This example keeps the WebSocket route in `asgi.py`; you do
not need a separate `routing.py`.

### 5. Connect the app's URLs

Create `myapp/urls.py`:

```python
urlpatterns = []  # The first LiveView tutorial adds the counter route here.
```

In `myproject/urls.py`, include it alongside the existing admin route:

```python
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("myapp.urls")),
]
```

### 6. Migrate, check, and run

```bash
python manage.py migrate
python manage.py check
python -m uvicorn myproject.asgi:application --reload
```

The manual project has no homepage yet, so a 404 at `/` is expected.
Continue to [Your First LiveView](./first-liveview.md), add its counter route,
and visit **http://127.0.0.1:8000/counter/**.

Use an ASGI server for live updates. Django's stock `runserver` is WSGI;
Daphne can provide an ASGI replacement when explicitly configured, as the
[Channels installation guide](https://channels.readthedocs.io/en/stable/installation.html)
explains. The Uvicorn commands here work without that override.

## Troubleshooting

### A project from an older CLI

If setup reports `admin.E403`, a production `SECRET_KEY` error, or
`No module named uvicorn`, check which CLI created the project. An older
installed CLI can emit outdated files even though its automatic install puts
a newer djust into the project's `.venv`, and older generators could print a
final `Done!` message after a failed step.

Updating the dependency alone does not rewrite those generated files. For a
starter you have not customized, keep the old directory and generate a fresh
one from its parent:

```bash
uvx djust@latest new myproject_fixed
cd myproject_fixed
make dev
```

For an app you have customized, run `uvx djust@latest init` in it, or follow
[Configure by hand](#configure-by-hand) to update its settings and ASGI entry
point.

### Missing Django, Uvicorn, or session tables

If generation left an empty `.venv`, repair it from the directory containing
that project's `manage.py`. There is no need to regenerate the project:

```bash
uv pip install --python .venv -r requirements.txt
.venv/bin/python manage.py makemigrations
.venv/bin/python manage.py migrate
.venv/bin/python manage.py showmigrations sessions
.venv/bin/python manage.py check
```

`showmigrations sessions` must include `[X] 0001_initial`. An error such as
`no such table: django_session` means the server's database has not had the
session migration applied. Run migrations with the same project settings and
database environment variables as the server; `manage.py check` alone does
not create tables. Restart the server after completing setup.

### The development file watcher

If checks report only `djust.C401` about the optional development file
watcher, install it with `uv pip install --python .venv watchdog` to enable hot
view replacement.

## Building from source

Framework contributors need Rust and Maturin in addition to Python:

```bash
git clone https://github.com/djust-org/djust.git
cd djust
uv venv --python 3.12
source .venv/bin/activate
uv pip install maturin
maturin develop
```

Install the [Rust toolchain](https://rustup.rs/) first if needed. Use
`maturin develop --release` for an optimized build.

## Next steps

- [Your First LiveView](./first-liveview.md) — create a counter and verify live updates
- [Scaffolding Generator](../guides/scaffolding.md) — generate projects, apps, and model CRUD
- [Themes](/theming/overview/) — choose your application's presentation
- [Deployment](../guides/deployment.md) — production static files, hosts, and server configuration
