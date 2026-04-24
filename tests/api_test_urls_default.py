"""URL configuration for {% djust_client_config %} tests.

Mounts the djust API at the default ``/djust/api/`` prefix via
``include("djust.api.urls")``. No namespace — mirrors the demo_project
deployment.
"""

from __future__ import annotations

from django.urls import include, path

from djust.sse import sse_urlpatterns

urlpatterns = [
    path("djust/api/", include("djust.api.urls")),
    # SSE app uses include(sse_urlpatterns) under "djust/" per djust/sse.py
    # example wiring — mirror that here so tests can verify both the API
    # prefix and SSE prefix resolve correctly. Unnamespaced (matches the
    # bare URL name probed by ``_DJUST_SSE_URL_NAMES``).
    path("djust/", include(sse_urlpatterns)),
]
