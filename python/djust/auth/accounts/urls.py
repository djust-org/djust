"""The one include for account pages, whatever the backend (ADR-039)::

    path("accounts/", include("djust.auth.accounts.urls"))

The backend's views live in the ``djust_auth`` namespace under stable names
(``djust_auth:login``, ``signup``, ``logout``, …). ``extra_urlpatterns()``
mounts routes that must stay un-namespaced (allauth's own URL names).
"""

from django.urls import include, path

from .registry import get_account_backend

_backend = get_account_backend()

urlpatterns = [path("", include((_backend.urlpatterns(), "djust_auth")))] + list(
    _backend.extra_urlpatterns()
)
