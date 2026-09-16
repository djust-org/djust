# Installation

Start with a new project or add djust to Django code you already have.
The scaffolder creates the Django project for you; the manual path shows each
piece and uses the same names as [Your First LiveView](./first-liveview.md).

| Starting point | Follow this path |
| --- | --- |
| New project, ready to build | [Scaffold a project](#scaffold-a-project-recommended) |
| Learn the Django setup step by step | [Create a Django project manually](#create-a-django-project-manually) |
| Existing Django project | [Add djust to an existing project](#add-djust-to-an-existing-project) |

## Requirements

- Python 3.10 or newer. Python 3.12 is a good starting point for these examples.
- [uv](https://docs.astral.sh/uv/getting-started/installation/) for environment and package management.
- Django and Channels are installed with djust; you do not need to install
  Django globally first. The manual example uses Django 5.2.
- Rust is only needed when building djust from source. Published wheels avoid
  a local Rust build on supported platforms.

Shell examples use macOS/Linux. In Windows PowerShell, activate a virtual
environment with `.venv\Scripts\Activate.ps1` instead of
`source .venv/bin/activate`.

## Scaffold a project (recommended)

Run this from the **parent directory** where you want the new project:

```bash
uvx --from djust djust new myproject
cd myproject
source .venv/bin/activate
python manage.py check
python -m uvicorn myproject.asgi:application --reload
```

`uvx` runs the djust CLI without installing it globally. `djust new` creates
`myproject/`, creates its own `.venv`, installs the generated requirements,
and runs migrations. Choose a new directory name; it does not modify an
existing project.

Open **http://127.0.0.1:8000/**. The starter includes an interactive list:
add an item and check that it appears without a page reload. With the project
virtual environment activated, `make dev` is an alternative to the Uvicorn
command above.

### What the scaffolder sets up

```text
myproject/
├── manage.py
├── requirements.txt
├── Makefile
├── .env
├── .venv/
├── templates/base.html
└── myproject/
    ├── settings.py
    ├── asgi.py
    ├── urls.py
    ├── views.py
    └── templates/myproject/
```

The generated package contains both project configuration and the starter
app. It includes the template backend, allowed LiveView modules, WebSocket
routing, local static-file serving, and development settings. SQLite is the
initial database; the default starter's list lives in LiveView state.

### Choose features at creation time

```bash
uvx --from djust djust new myproject --with-auth --with-db
```

| Flag | Adds |
| --- | --- |
| `--with-auth` | Login/logout views and authentication settings |
| `--with-db` | Django models, admin, and database-backed views |
| `--with-presence` | Online-user presence example |
| `--with-streaming` | Live-feed streaming example |
| `--from-schema schema.json` | Models and views from a JSON schema; implies `--with-db` |
| `--no-setup` | Generate files without creating an environment, installing packages, or migrating |

If automatic setup reports a failure, or you chose `--no-setup`, complete it
from the generated directory:

```bash
uv venv --python 3.12
source .venv/bin/activate
uv pip install -r requirements.txt
python manage.py makemigrations
python manage.py migrate
python manage.py check
python -m uvicorn myproject.asgi:application --reload
```

Already have djust in an active environment? `python -m djust new myproject`
runs the same generator. For model-by-model CRUD generation and schema
options, see [Scaffolding Generator](../guides/scaffolding.md).

You can now edit the generated `myproject/views.py`, or follow
[Your First LiveView](./first-liveview.md) to create a separate counter app.
You do not need the manual setup below for a scaffolded project.

## Create a Django project manually

A **project** contains site settings and root URLs. An **app** contains a
feature's views, models, and templates. Here the project is `myproject` and
the app is `myapp`.

### 1. Create an environment and install packages

```bash
mkdir myproject
cd myproject
uv venv --python 3.12
source .venv/bin/activate
uv pip install "Django>=5.2,<5.3" djust "uvicorn[standard]"
```

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

## Add djust to an existing project

If you followed the manual steps above, the environment and app are ready.
For an existing project, activate its environment and install the packages
using its dependency manager. For a uv-managed project:

```bash
uv add djust "uvicorn[standard]"
```

Use your existing project and app names in place of `myproject` and `myapp`.
If you need a new app, run `python manage.py startapp myapp` beside `manage.py`.

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

Register djust's template backend **before** the existing Django backend.
Keeping Django's backend also supports the Django admin:

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
