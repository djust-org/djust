"""
Base view helpers for observability endpoints. Each Phase 7.x PR adds
endpoint handlers here; this file initially ships only the `health`
endpoint so the foundation PR is independently verifiable.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET

from djust.observability.registry import (
    get_registered_session_count,
    get_view_for_session,
)
from djust.observability.tracebacks import get_recent_tracebacks

logger = logging.getLogger("djust.observability")


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


@csrf_exempt
@require_GET
def view_assigns(request):
    """Return the mounted LiveView's public state for a session.

    Query params:
        session_id (required): session uuid from the WS handshake ack.

    Response (200):
        {"session_id": "...", "view_class": "CounterView", "assigns": {...}}

    Response (400): session_id missing.
    Response (404): DEBUG=False, or session not registered.
    """
    if not settings.DEBUG:
        return _debug_gate()

    session_id = request.GET.get("session_id", "").strip()
    if not session_id:
        return JsonResponse(
            {"error": "session_id query param required"},
            status=400,
        )

    view = get_view_for_session(session_id)
    if view is None:
        return JsonResponse(
            {"error": f"no view registered for session {session_id}"},
            status=404,
        )

    try:
        assigns = view.get_state()
    except Exception as e:  # noqa: BLE001
        # get_state raises TypeError on non-serializable values in DEBUG.
        # Observability must still return something useful — fall back to
        # a best-effort shallow dict of repr()s.
        logger.warning("view.get_state() failed for session %s: %s", session_id, e)
        assigns = {
            k: repr(v)[:200]
            for k, v in view.__dict__.items()
            if not k.startswith("_") and not callable(v)
        }

    return JsonResponse(
        {
            "session_id": session_id,
            "view_class": view.__class__.__name__,
            "view_module": view.__class__.__module__,
            "assigns": assigns,
        }
    )


@csrf_exempt
@require_GET
def last_traceback(request):
    """Return the most-recent N captured exceptions (newest first).

    Query params:
        n (optional): how many entries to return. Defaults to 1. Capped
            at the ring buffer's size.

    Each entry: {timestamp_ms, exception_type, exception_module, message,
    error_type, event_name, view_class, session_id, traceback}.

    Captures flow through `handle_exception()` — the single entry point
    for djust-managed errors. Every handler / mount / render error ends
    up here.
    """
    if not settings.DEBUG:
        return _debug_gate()

    try:
        n = int(request.GET.get("n", "1"))
    except (TypeError, ValueError):
        n = 1
    n = max(1, min(n, 50))

    return JsonResponse({"count": n, "entries": get_recent_tracebacks(n)})
