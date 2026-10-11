"""
Print the token the ``/_djust/observability/`` endpoints require.

The endpoints only answer requests that carry this value in the
``X-Djust-Observability-Token`` header (see ``djust.observability.middleware``).
The djust MCP server (``manage.py djust_mcp``) sends it on its own; use this
command for other local tools, e.g.::

    curl -H "X-Djust-Observability-Token: $(python manage.py djust_observability_token)" \\
        http://127.0.0.1:8000/_djust/observability/health/
"""

from typing import Any

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Print the token required by the djust observability endpoints"

    def handle(self, *args: Any, **options: Any) -> None:
        from djust.observability.middleware import get_observability_token

        self.stdout.write(get_observability_token())
