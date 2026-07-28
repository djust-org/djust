"""
ASGI config for demo_project.

Wraps the HTTP handler with ASGIStaticFilesHandler so that static files
(client.js, CSS, etc.) are served correctly under ASGI servers like
uvicorn and daphne without needing a separate static file server.

Import order is load-bearing (#2164/#2166). ``DJANGO_SETTINGS_MODULE`` must be
set BEFORE anything that pulls in ``djust``: ``djust.config`` reads Django
settings once, at import, and a read before the variable is set raises
``ImproperlyConfigured`` and leaves the config singleton on pure defaults for
the life of the process — silently. This file previously imported
``demo_project.routing`` (-> ``djust.websocket`` -> ``djust.config``) three
lines above the ``setdefault``, so every ``LIVEVIEW_CONFIG`` key in the demo
was ignored. Matches the order ``djust new`` scaffolds.
"""

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "demo_project.settings")

from django.core.asgi import get_asgi_application  # noqa: E402

# Initialize Django's app registry BEFORE importing anything that touches
# models / consumers (channels, djust.websocket). ``get_asgi_application()``
# calls ``django.setup()`` internally, so the imports below are safe.
django_asgi_app = get_asgi_application()

from channels.auth import AuthMiddlewareStack  # noqa: E402
from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402
from channels.security.websocket import AllowedHostsOriginValidator  # noqa: E402
from django.contrib.staticfiles.handlers import ASGIStaticFilesHandler  # noqa: E402

import demo_project.routing  # noqa: E402

# CSWSH defense (#653): AllowedHostsOriginValidator rejects WebSocket
# handshakes whose Origin header is not in settings.ALLOWED_HOSTS. Apps that
# don't need django.contrib.auth should prefer djust.routing.DjustMiddlewareStack
# which applies this wrap automatically.
application = ProtocolTypeRouter({
    "http": ASGIStaticFilesHandler(django_asgi_app),
    "websocket": AllowedHostsOriginValidator(
        AuthMiddlewareStack(
            URLRouter(
                demo_project.routing.websocket_urlpatterns
            )
        )
    ),
})
