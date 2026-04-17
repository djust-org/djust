"""
Observability URL patterns. Include via:

    urlpatterns = [
        path("_djust/observability/", include("djust.observability.urls")),
        ...
    ]

Each Phase 7.x PR adds more routes below `health`. Routes are
additionally DEBUG-gated at the view level so a stray include in a
production config still refuses to serve data.
"""

from django.urls import path

from djust.observability.views import health

app_name = "djust_observability"

urlpatterns = [
    path("health/", health, name="health"),
]
