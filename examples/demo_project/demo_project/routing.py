"""
WebSocket routing for LiveView
"""

from django.urls import path
from django_rust_live.websocket import LiveViewConsumer

websocket_urlpatterns = [
    path('ws/live/', LiveViewConsumer.as_asgi()),
]
