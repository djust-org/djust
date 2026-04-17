"""
Base view helpers for observability endpoints. Each Phase 7.x PR adds
endpoint handlers here; this file initially ships only the `health`
endpoint so the foundation PR is independently verifiable.
"""

from __future__ import annotations

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET

from djust.observability.registry import get_registered_session_count


def _debug_gate():
    """Return a 404-style response if DEBUG is off — mirrors how Django
    hides debug URLs in production. We return 404 rather than 403 so
    the endpoint's existence isn't disclosed.
    """
    return HttpResponse(status=404)


@csrf_exempt
@require_GET
def health(request):
    """Liveness probe. Returns the registry size + DEBUG flag.

    `curl http://127.0.0.1:8000/_djust/observability/health/` during
    live-verification. Returns:

        {"ok": true, "debug": true, "registered_sessions": 0}
    """
    if not settings.DEBUG:
        return _debug_gate()
    return JsonResponse(
        {
            "ok": True,
            "debug": settings.DEBUG,
            "registered_sessions": get_registered_session_count(),
        }
    )
