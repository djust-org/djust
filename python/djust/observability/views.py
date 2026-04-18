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

from djust.observability.log_handler import get_recent_logs
from djust.observability.registry import (
    get_registered_session_count,
    get_view_for_session,
)
from djust.observability.sql import get_queries_since
from djust.observability.timings import get_timing_stats
from djust.observability.tracebacks import get_recent_tracebacks


def _is_jsonable(value):
    """Last-resort serializability check when the view doesn't expose
    `_is_serializable`. Cheap for small values; we tolerate the cost
    because observability endpoints aren't hot paths."""
    import json

    try:
        json.dumps(value)
        return True
    except (TypeError, ValueError):
        return False


def _lenient_assigns(view):
    """Serialize public attrs one-by-one with per-attr fallback.

    Why not just call `view.get_state()`? In DEBUG mode that raises on
    the first non-serializable attr, forcing an all-or-nothing choice.
    A common pattern (`self.request = request` stored during mount)
    then loses the other 95% of legitimate state to a blanket repr.

    This walker keeps JSON-serializable values as themselves and tags
    each non-serializable one with `{_repr, _type}` so the agent can
    see at a glance which attrs fell back and why.

    Uses the view's own `_is_serializable` when available (matches the
    framework's definition) and falls back to a direct json.dumps probe
    for anything that doesn't expose it (e.g. test doubles).
    """
    checker = getattr(view, "_is_serializable", None)

    def _safe_check(val):
        if checker is not None:
            try:
                return bool(checker(val))
            except Exception:  # noqa: BLE001
                return False
        return _is_jsonable(val)

    assigns = {}
    for key, value in view.__dict__.items():
        if key.startswith("_") or callable(value):
            continue
        if _safe_check(value):
            assigns[key] = value
        else:
            assigns[key] = {
                "_repr": repr(value)[:200],
                "_type": type(value).__name__,
            }
    return assigns


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

    assigns = _lenient_assigns(view)

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


@csrf_exempt
@require_GET
def log_tail(request):
    """Return buffered log records.

    Query params:
        since_ms (optional): only entries with timestamp > since_ms.
        level (optional): minimum level, one of DEBUG/INFO/WARNING/ERROR/CRITICAL.
            Default INFO.
        limit (optional): max entries to return (default 500, capped to
            buffer size).

    Entries ordered chronologically (oldest first).
    """
    if not settings.DEBUG:
        return _debug_gate()

    try:
        since_ms = int(request.GET.get("since_ms", "0"))
    except (TypeError, ValueError):
        since_ms = 0

    level = request.GET.get("level", "INFO").strip() or "INFO"

    try:
        limit = int(request.GET.get("limit", "500"))
    except (TypeError, ValueError):
        limit = 500
    limit = max(1, min(limit, 500))

    entries = get_recent_logs(since_ms=since_ms, level=level, limit=limit)
    return JsonResponse(
        {
            "count": len(entries),
            "since_ms": since_ms,
            "level": level,
            "entries": entries,
        }
    )


@csrf_exempt
@require_GET
def handler_timings(request):
    """Return per-handler percentile stats over the rolling sample window.

    Query params:
        handler_name (optional): filter to a single handler name. If
            multiple views expose handlers with the same name, each
            appears as its own row.
        since_ms (optional): only include samples with timestamp > since_ms.

    Each row: {view_class, handler_name, count, min_ms, max_ms, avg_ms,
    p50_ms, p90_ms, p99_ms}. Sorted by p90 descending so the slowest
    handlers are first.
    """
    if not settings.DEBUG:
        return _debug_gate()

    handler_name = request.GET.get("handler_name", "").strip() or None

    since_ms_raw = request.GET.get("since_ms", "").strip()
    try:
        since_ms = int(since_ms_raw) if since_ms_raw else None
    except ValueError:
        since_ms = None

    rows = get_timing_stats(handler_name=handler_name, since_ms=since_ms)
    return JsonResponse({"count": len(rows), "stats": rows})


@csrf_exempt
def reset_view_state(request):
    """Replay `view.mount()` on the registered instance — resets all public
    attrs back to their post-mount values.

    Accepts POST only. Requires the consumer to have stashed
    `_djust_mount_request` + `_djust_mount_kwargs` (automatic since djust
    framework Phase 11 #42). Views mounted before this was wired will
    fail with a 409 and a clear message.

    Does NOT push a fresh render to the connected client — the caller
    must trigger one (user interaction, force reload). This limitation
    is acceptable for test-harness / fixture-cleanup use cases.

    Query params:
        session_id (required)

    Response (200): {session_id, view_class, assigns_after_reset}
    Response (400): session_id missing
    Response (404): DEBUG=False, or session not registered
    Response (405): wrong HTTP method
    Response (409): mount args not stashed (view predates this feature)
    Response (500): mount() raised
    """
    if not settings.DEBUG:
        return _debug_gate()

    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    session_id = request.GET.get("session_id", "").strip()
    if not session_id:
        return JsonResponse({"error": "session_id query param required"}, status=400)

    view = get_view_for_session(session_id)
    if view is None:
        return JsonResponse(
            {"error": f"no view registered for session {session_id}"},
            status=404,
        )

    mount_request = getattr(view, "_djust_mount_request", None)
    mount_kwargs = getattr(view, "_djust_mount_kwargs", None)
    if mount_request is None or mount_kwargs is None:
        return JsonResponse(
            {
                "error": "view was mounted before reset_view_state was wired",
                "hint": "Reconnect the WebSocket to re-mount under the new consumer.",
            },
            status=409,
        )

    # Clear all public, non-callable attrs. This is the "reset" — mount()
    # will then repopulate them. Private (_foo) attrs stay so that framework
    # bookkeeping (websocket_session_id, stashed request, etc.) isn't lost.
    for key in list(view.__dict__.keys()):
        if not key.startswith("_") and not callable(getattr(view, key, None)):
            delattr(view, key)

    try:
        view.mount(mount_request, **mount_kwargs)
    except Exception as e:  # noqa: BLE001
        return JsonResponse(
            {
                "error": f"mount() raised: {type(e).__name__}: {e}",
                "session_id": session_id,
            },
            status=500,
        )

    assigns = _lenient_assigns(view)
    return JsonResponse(
        {
            "session_id": session_id,
            "view_class": view.__class__.__name__,
            "assigns_after_reset": assigns,
        }
    )


@csrf_exempt
@require_GET
def sql_queries(request):
    """Return captured SQL queries, filtered by session/handler/since_ms.

    Query params:
        session_id: filter to one session
        handler_name: filter to one handler
        since_ms: only queries with timestamp > since_ms
        limit: max rows returned (default 500)

    Each entry: {timestamp_ms, session_id, event_id, handler_name, sql,
    params, many, duration_ms, stack_top}. Entries chronological.
    """
    if not settings.DEBUG:
        return _debug_gate()

    session_id = request.GET.get("session_id", "").strip() or None
    handler_name = request.GET.get("handler_name", "").strip() or None

    try:
        since_ms = int(request.GET.get("since_ms", "0"))
    except (TypeError, ValueError):
        since_ms = 0
    try:
        limit = int(request.GET.get("limit", "500"))
    except (TypeError, ValueError):
        limit = 500
    limit = max(1, min(limit, 500))

    entries = get_queries_since(
        since_ms=since_ms,
        session_id=session_id,
        handler_name=handler_name,
        limit=limit,
    )
    return JsonResponse(
        {
            "count": len(entries),
            "since_ms": since_ms,
            "session_id": session_id,
            "handler_name": handler_name,
            "entries": entries,
        }
    )
