"""URL configuration for {% djust_client_config %} tests — custom prefix mount.

Mounts the djust API at ``/myapi/`` via ``api_patterns(prefix='myapi/')``.
Uses the ``djust_api`` namespace (set by ``api_patterns()``).
"""

from __future__ import annotations

from djust.api import api_patterns

urlpatterns = [
    api_patterns(prefix="myapi/"),
]
