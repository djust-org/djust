"""URL patterns for the djust HTTP API (ADR-008).

Include ``djust.api.urls`` from your project's ``urls.py`` to wire up the
dispatch view and the OpenAPI schema endpoint::

    from django.urls import include, path

    urlpatterns = [
        path("djust/api/", include("djust.api.urls")),
        # ...
    ]

Or use :func:`api_patterns` to embed the routes under a custom prefix::

    from djust.api import api_patterns

    urlpatterns = [
        api_patterns(),  # mounts at /djust/api/ by default
        # ...
    ]
"""

from __future__ import annotations

from django.urls import include, path

from djust.api.dispatch import DjustAPIDispatchView
from djust.api.openapi import OpenAPISchemaView


def default_api_urlpatterns():
    """Return the default djust API URL patterns (relative to their mount prefix)."""
    return [
        path("openapi.json", OpenAPISchemaView.as_view(), name="djust-api-openapi"),
        path(
            "<str:view_slug>/<str:handler_name>/",
            DjustAPIDispatchView.as_view(),
            name="djust-api-dispatch",
        ),
    ]


def api_patterns(prefix: str = "djust/api/"):
    """Return a single ``path()`` mounting the djust API under ``prefix``.

    The returned value is a Django ``URLPattern`` / ``URLResolver`` suitable for
    inclusion in ``urlpatterns``. Example::

        urlpatterns = [
            api_patterns(),
            # your routes
        ]
    """
    return path(prefix, include((default_api_urlpatterns(), "djust_api")))


# ``include("djust.api.urls")`` uses this module-level ``urlpatterns``.
urlpatterns = default_api_urlpatterns()
