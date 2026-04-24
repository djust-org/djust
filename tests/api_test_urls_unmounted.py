"""URL configuration for {% djust_client_config %} tests — unmounted API.

Deliberately omits the djust API mount so ``reverse('djust-api-call', ...)``
raises ``NoReverseMatch``. Used to assert the tag gracefully falls back.
"""

from __future__ import annotations

urlpatterns = []
