"""
WebSocket routing for LiveView
"""

from django.urls import path
from djust.websocket import LiveViewConsumer

websocket_urlpatterns = [
    path('ws/live/', LiveViewConsumer.as_asgi()),
]
