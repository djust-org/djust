"""
ASGI config for demo_project.
"""

import os
from django.core.asgi import get_asgi_application
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack
import demo_project.routing

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'demo_project.settings')

application = ProtocolTypeRouter({
    "http": get_asgi_application(),
    "websocket": AuthMiddlewareStack(
        URLRouter(
            demo_project.routing.websocket_urlpatterns
        )
    ),
})
