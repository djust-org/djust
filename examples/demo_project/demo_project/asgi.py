"""
ASGI config for demo_project.

Wraps the HTTP handler with ASGIStaticFilesHandler so that static files
(client.js, CSS, etc.) are served correctly under ASGI servers like
uvicorn and daphne without needing a separate static file server.
"""

import os
from django.core.asgi import get_asgi_application
from django.contrib.staticfiles.handlers import ASGIStaticFilesHandler
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack
import demo_project.routing

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'demo_project.settings')

application = ProtocolTypeRouter({
    "http": ASGIStaticFilesHandler(get_asgi_application()),
    "websocket": AuthMiddlewareStack(
        URLRouter(
            demo_project.routing.websocket_urlpatterns
        )
    ),
})
