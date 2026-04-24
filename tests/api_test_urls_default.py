"""URL configuration for {% djust_client_config %} tests.

Mounts the djust API at the default ``/djust/api/`` prefix via
``include("djust.api.urls")``. No namespace — mirrors the demo_project
deployment.
"""

from __future__ import annotations

from django.urls import include, path

urlpatterns = [
    path("djust/api/", include("djust.api.urls")),
]
