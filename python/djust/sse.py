"""
SSE (Server-Sent Events) transport for djust LiveView.

Provides a fallback transport when WebSocket is unavailable (corporate proxies,
certain enterprise environments). The SSE transport is one-directional:

- Server → Client: EventSource (SSE stream at /djust/sse/<session_id>/)
- Client → Server: HTTP POST to /djust/sse/<session_id>/event/

Feature limitations compared to WebSocket transport:
- No binary file uploads (use WebSocket or direct HTTP upload endpoints)
- No presence tracking (requires persistent bidirectional channel)
- No actor-based state management
- No MessagePack binary encoding
- No embedded child-view scoped updates (full-page updates only)

Usage: Add SSE URL patterns to your Django URL configuration::

    from djust.sse import sse_urlpatterns
    from django.urls import path, include

    urlpatterns = [
        ...
        path("djust/", include(sse_urlpatterns)),
    ]

Or register the views directly::

    from djust.sse import DjustSSEStreamView, DjustSSEEventView

    urlpatterns = [
        path("djust/sse/<str:session_id>/", DjustSSEStreamView.as_view()),
        path("djust/sse/<str:session_id>/event/", DjustSSEEventView.as_view()),
    ]

Transport negotiation is handled automatically in the djust client JS:
the client tries WebSocket first, then falls back to SSE if the WebSocket
connection fails after the maximum number of reconnect attempts.

Multi-process note: SSE sessions are stored in-process. In multi-process
deployments the SSE stream GET and event POST requests must be routed to the
same worker process (e.g., nginx ``ip_hash`` or a sticky load-balancer).
"""

import asyncio
import json
import logging
import time
import uuid
from typing import Any, Dict, Optional

from asgiref.sync import sync_to_async
from django.http import JsonResponse, StreamingHttpResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from .rate_limit import ConnectionRateLimiter
from .security import handle_exception, sanitize_for_log
from .serialization import DjangoJSONEncoder
from .validation import validate_handler_params
from .websocket import _snapshot_assigns, _compute_changed_keys
from .websocket_utils import (
    _call_handler,
    _safe_error,
    _validate_event_security,
    get_handler_coerce_setting,
)

logger = logging.getLogger(__name__)

# In-memory SSE session registry: session_id → SSESession.
# Sessions are created by DjustSSEStreamView.get() and removed when the stream closes.
_sse_sessions: Dict[str, "SSESession"] = {}

# SSE keepalive interval in seconds (sent as ":" comment lines)
_KEEPALIVE_TIMEOUT = 25.0

# How long to retain a session after the stream closes, in seconds.
# Allows in-flight event POSTs to still find the session briefly.
_SESSION_LINGER_S = 5.0


class SSESession:
    """
    Per-connection state for a single SSE client.

    Holds the mounted LiveView instance and an asyncio Queue used to pass
    server-side update messages to the SSE stream generator.

    Also exposes the minimal interface required by ``_validate_event_security``
    (``send_error``, ``close``, ``_client_ip``) so that security utilities from
    ``websocket_utils`` can be reused without modification.
    """

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.view_instance: Optional[Any] = None
        self.queue: asyncio.Queue = asyncio.Queue()
        self.active = True
        self._rate_limiter: ConnectionRateLimiter = ConnectionRateLimiter()
        self._client_ip: Optional[str] = None

    # ------------------------------------------------------------------ #
    # Queue helpers
    # ------------------------------------------------------------------ #

    def push(self, msg: Dict[str, Any]) -> None:
        """Enqueue a message to be sent to the SSE client."""
        self.queue.put_nowait(msg)

    def shutdown(self) -> None:
        """Signal the SSE stream generator to close the connection."""
        self.active = False
        self.queue.put_nowait(None)  # None is the sentinel value

    # ------------------------------------------------------------------ #
    # Interface for _validate_event_security
    # ------------------------------------------------------------------ #

    async def send_error(self, error: str, **kwargs: Any) -> None:
        """Push an error message to the SSE client."""
        self.push({"type": "error", "error": error, **kwargs})

    async def close(self, code: int = 1000) -> None:
        """Called by rate-limit logic to force-close the transport."""
        self.shutdown()


def _get_session(session_id: str) -> Optional["SSESession"]:
    """Return the SSESession for *session_id*, or None if not found."""
    return _sse_sessions.get(session_id)


def _client_ip_from_request(request) -> Optional[str]:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


# ------------------------------------------------------------------ #
# Mount helper
# ------------------------------------------------------------------ #


async def _sse_mount_view(session: SSESession, request, view_path: str) -> None:
    """
    Mount a LiveView into *session* and push the initial ``mount`` message.

    This is the SSE equivalent of ``LiveViewConsumer.handle_mount()``. It shares
    the same security model (module allowlist, LiveView subclass check, auth) and
    the same render path (``render_with_diff``).

    Differences from the WebSocket path:
    - The real HTTP ``request`` object is used directly (no fake RequestFactory).
    - No channel groups, presence tracking, or actor-based state.
    - No pre-rendered content optimisation (SSE always sends fresh HTML).
    """
    from django.conf import settings

    # ---- Security: module allowlist ----
    allowed_modules = getattr(settings, "LIVEVIEW_ALLOWED_MODULES", [])
    if allowed_modules and not any(view_path.startswith(m) for m in allowed_modules):
        logger.warning("SSE: blocked attempt to mount view from unauthorized module: %s", sanitize_for_log(view_path))
        session.push(
            {
                "type": "error",
                "error": _safe_error(
                    f"View {view_path} is not in allowed modules", "View not found"
                ),
            }
        )
        return

    # ---- Import view class ----
    try:
        module_path, class_name = view_path.rsplit(".", 1)
        module = __import__(module_path, fromlist=[class_name])
        view_class = getattr(module, class_name)
    except (ValueError, ImportError, AttributeError) as exc:
        error_msg = "Failed to load view %s: %s" % (view_path, exc)
        logger.error("Failed to load view %s: %s", sanitize_for_log(view_path), exc)
        session.push({"type": "error", "error": _safe_error(error_msg, "View not found")})
        return

    # ---- Security: must be a LiveView subclass ----
    from .live_view import LiveView

    if not (isinstance(view_class, type) and issubclass(view_class, LiveView)):
        error_msg = "Security: %s is not a LiveView subclass." % view_path
        logger.error("Security: %s is not a LiveView subclass.", sanitize_for_log(view_path))
        session.push({"type": "error", "error": _safe_error(error_msg, "Invalid view class")})
        return

    # ---- Instantiate view ----
    try:
        view_instance = view_class()
    except Exception as exc:
        response = handle_exception(
            exc,
            error_type="mount",
            view_class=view_path,
            logger=logger,
            log_message="Failed to instantiate %s" % view_path,
        )
        session.push(response)
        return

    # Mark this view as using the SSE transport (for introspection / limits)
    view_instance._sse_session_id = session.session_id
    view_instance._sse_session = session
    view_instance._websocket_path = request.path
    view_instance._websocket_query_string = request.META.get("QUERY_STRING", "")
    # Use a stable session-scoped ID so VDOM caching works the same as WS
    view_instance._websocket_session_id = session.session_id

    session.view_instance = view_instance

    # ---- Auth check ----
    try:
        from .auth import check_view_auth
        from django.core.exceptions import PermissionDenied

        try:
            redirect_url = await sync_to_async(check_view_auth)(view_instance, request)
        except PermissionDenied:
            session.push({"type": "error", "error": "Permission denied"})
            return

        if redirect_url:
            session.push({"type": "navigate", "to": redirect_url})
            return
    except Exception as exc:
        logger.warning("SSE: auth check error: %s", exc)

    # ---- Resolve URL kwargs (for path-based params like pk, slug) ----
    mount_kwargs: Dict[str, Any] = {}
    page_url = request.path
    try:
        from django.urls import resolve

        match = resolve(page_url)
        if match.kwargs:
            mount_kwargs.update(match.kwargs)
    except Exception:
        pass  # URL may not resolve (e.g. plain path like "/items/123") — skip kwargs extraction

    # Merge query-string params passed by the client
    for key, value in request.GET.items():
        if key != "view":  # "view" is the view-path param, not a mount kwarg
            mount_kwargs[key] = value

    # ---- Store request and call mount() ----
    try:
        view_instance.request = request
        await sync_to_async(view_instance._initialize_temporary_assigns)()
        await sync_to_async(view_instance.mount)(request, **mount_kwargs)
    except Exception as exc:
        response = handle_exception(
            exc,
            error_type="mount",
            view_class=view_path,
            logger=logger,
            log_message="Error in %s.mount()" % sanitize_for_log(view_path),
        )
        session.push(response)
        return

    # ---- Initial render ----
    try:
        await sync_to_async(view_instance._initialize_rust_view)(request)
        await sync_to_async(view_instance._sync_state_to_rust)()
        html, _patches, version = await sync_to_async(view_instance.render_with_diff)()
        html = await sync_to_async(view_instance._strip_comments_and_whitespace)(html)
        html = await sync_to_async(view_instance._extract_liveview_content)(html)
    except Exception as exc:
        response = handle_exception(
            exc,
            error_type="render",
            view_class=view_path,
            logger=logger,
            log_message="Error rendering %s" % sanitize_for_log(view_path),
        )
        session.push(response)
        return

    # ---- Push mount message ----
    mount_msg: Dict[str, Any] = {
        "type": "mount",
        "session_id": session.session_id,
        "view": view_path,
        "version": version,
        "html": html,
        "has_ids": "dj-id=" in html,
    }

    # Include @cache decorator configuration if present
    cache_config = _extract_cache_config(view_instance)
    if cache_config:
        mount_msg["cache_config"] = cache_config

    session.push(mount_msg)
    logger.info("SSE: mounted view %s (session %s)", sanitize_for_log(view_path), sanitize_for_log(session.session_id))


def _extract_cache_config(view_instance) -> Optional[Dict[str, Any]]:
    """Extract @cache decorator configuration from a view instance (mirrors WS consumer)."""
    try:
        cache_config: Dict[str, Any] = {}
        for attr_name in dir(type(view_instance)):
            if attr_name.startswith("_"):
                continue
            method = getattr(view_instance, attr_name, None)
            if method and hasattr(method, "_djust_decorators"):
                cache_info = method._djust_decorators.get("cache")
                if cache_info:
                    cache_config[attr_name] = cache_info
        return cache_config or None
    except Exception:
        return None


# ------------------------------------------------------------------ #
# Event dispatch helper
# ------------------------------------------------------------------ #


async def _sse_handle_event(session: SSESession, event_name: str, params: Dict[str, Any]) -> None:
    """
    Dispatch a client event to the mounted LiveView and push the result.

    This is a simplified version of ``LiveViewConsumer.handle_event()`` that
    handles the common case (single view, no embedded children, no actors).
    Component events and embedded-child routing are not supported over SSE.
    """
    view_instance = session.view_instance
    if not view_instance:
        await session.send_error("View not mounted. Please reload the page.")
        return

    start_time = time.perf_counter()

    # Extract internal params
    cache_request_id = params.pop("_cacheRequestId", None)
    positional_args = params.pop("_args", [])

    # ---- Security checks ----
    handler = await _validate_event_security(
        session, event_name, view_instance, session._rate_limiter
    )
    if handler is None:
        return

    # ---- Parameter validation ----
    coerce = get_handler_coerce_setting(handler)
    validation = validate_handler_params(
        handler, params, event_name, coerce=coerce, positional_args=positional_args
    )
    if not validation["valid"]:
        logger.error("SSE: parameter validation failed: %s", sanitize_for_log(validation["error"]))
        await session.send_error(
            validation["error"],
            validation_details={
                "expected_params": validation["expected"],
                "provided_params": validation["provided"],
                "type_errors": validation["type_errors"],
            },
        )
        return

    coerced_params = validation.get("coerced_params", params)

    # ---- Snapshot before handler ----
    pre_assigns = _snapshot_assigns(view_instance)

    # ---- Call handler ----
    try:
        await _call_handler(handler, coerced_params if coerced_params else None)
    except Exception as exc:
        response = handle_exception(
            exc,
            error_type="event",
            event_name=event_name,
            view_class=view_instance.__class__.__name__,
            logger=logger,
            log_message="Error in SSE event handler %s.%s()" % (view_instance.__class__.__name__, sanitize_for_log(event_name)),
        )
        session.push(response)
        return

    # ---- Auto-detect unchanged state ----
    skip_render = getattr(view_instance, "_skip_render", False)
    if not skip_render:
        post_assigns = _snapshot_assigns(view_instance)
        if pre_assigns == post_assigns:
            skip_render = True
        else:
            view_instance._changed_keys = _compute_changed_keys(pre_assigns, post_assigns)

    # Detect scheduled async work
    has_async = getattr(view_instance, "_async_pending", None) is not None

    if skip_render:
        view_instance._skip_render = False
        # Push any queued push_events before noop
        _flush_push_events_to_queue(session, view_instance)
        noop_msg: Dict[str, Any] = {"type": "noop"}
        if has_async:
            noop_msg["async_pending"] = True
        session.push(noop_msg)
        if has_async:
            asyncio.ensure_future(_sse_run_async_work(session, event_name))
        return

    # ---- Render ----
    try:

        def _sync_render():
            return view_instance.render_with_diff()

        html, patches, version = await sync_to_async(_sync_render)()
    except Exception as exc:
        response = handle_exception(
            exc,
            error_type="render",
            view_class=view_instance.__class__.__name__,
            logger=logger,
            log_message="SSE: render error",
        )
        session.push(response)
        return

    should_reset_form = getattr(view_instance, "_should_reset_form", False)
    if should_reset_form:
        view_instance._should_reset_form = False

    # ---- Build update message ----
    if patches is not None:
        patch_list = json.loads(patches) if patches else []

        # Patch compression: if too many patches, send full HTML instead
        PATCH_THRESHOLD = 100
        if patch_list and len(patch_list) > PATCH_THRESHOLD:
            patches_size = len(patches.encode("utf-8"))
            html_size = len(html.encode("utf-8"))
            if html_size < patches_size * 0.7:
                if hasattr(view_instance, "_rust_view") and view_instance._rust_view:
                    view_instance._rust_view.reset()
                patch_list = None

        if patch_list is not None:
            update_msg: Dict[str, Any] = {
                "type": "patch",
                "patches": patch_list,
                "version": version,
                "event_name": event_name,
            }
            if cache_request_id:
                update_msg["cache_request_id"] = cache_request_id
            if should_reset_form:
                update_msg["reset_form"] = True
            if has_async:
                update_msg["async_pending"] = True
            session.push(update_msg)
        else:
            # Compression fallback: send HTML
            html_stripped = view_instance._strip_comments_and_whitespace(html)
            html_content = view_instance._extract_liveview_content(html_stripped)
            update_msg = {
                "type": "html_update",
                "html": html_content,
                "version": version,
                "event_name": event_name,
            }
            if cache_request_id:
                update_msg["cache_request_id"] = cache_request_id
            if should_reset_form:
                update_msg["reset_form"] = True
            if has_async:
                update_msg["async_pending"] = True
            session.push(update_msg)
    else:
        # VDOM diff unavailable — send full HTML
        html_stripped = view_instance._strip_comments_and_whitespace(html)
        html_content = view_instance._extract_liveview_content(html_stripped)
        update_msg = {
            "type": "html_update",
            "html": html_content,
            "version": version,
            "event_name": event_name,
        }
        if cache_request_id:
            update_msg["cache_request_id"] = cache_request_id
        if should_reset_form:
            update_msg["reset_form"] = True
        if has_async:
            update_msg["async_pending"] = True
        session.push(update_msg)

    # Flush push_events and navigation commands
    _flush_push_events_to_queue(session, view_instance)
    _flush_navigation_to_queue(session, view_instance)

    if has_async:
        asyncio.ensure_future(_sse_run_async_work(session, event_name))

    logger.debug(
        "SSE: event '%s' handled in %.1fms",
        sanitize_for_log(event_name),
        (time.perf_counter() - start_time) * 1000,
    )


def _flush_push_events_to_queue(session: SSESession, view_instance) -> None:
    """Drain push_events from the view and send them via SSE."""
    if not hasattr(view_instance, "_drain_push_events"):
        return
    for event_name, payload in view_instance._drain_push_events():
        session.push({"type": "push_event", "event": event_name, "payload": payload})


def _flush_navigation_to_queue(session: SSESession, view_instance) -> None:
    """Drain navigation commands from the view and send them via SSE."""
    if not hasattr(view_instance, "_drain_navigation"):
        return
    for cmd in view_instance._drain_navigation():
        session.push({**cmd, "type": "navigation", "action": cmd["type"]})


async def _sse_run_async_work(session: SSESession, event_name: Optional[str]) -> None:
    """
    Run background work scheduled by ``start_async()`` and push the result via SSE.

    Mirrors ``LiveViewConsumer._dispatch_async_work`` and
    ``LiveViewConsumer._run_async_work``.
    """
    view_instance = session.view_instance
    if not view_instance:
        return

    # Collect pending tasks
    tasks = getattr(view_instance, "_async_tasks", None)
    pending = getattr(view_instance, "_async_pending", None)

    if tasks:
        for task_name, (callback, args, kwargs) in list(tasks.items()):
            asyncio.ensure_future(
                _sse_execute_async_task(session, task_name, callback, args, kwargs, event_name)
            )
        view_instance._async_tasks = {}

    if pending:
        view_instance._async_pending = None
        callback, args, kwargs = pending
        asyncio.ensure_future(
            _sse_execute_async_task(session, "_default", callback, args, kwargs, event_name)
        )


async def _sse_execute_async_task(
    session: SSESession,
    task_name: str,
    callback,
    args,
    kwargs,
    event_name: Optional[str],
) -> None:
    """Execute a single async task and push the rendered result via SSE."""
    view_instance = session.view_instance
    if not view_instance:
        return

    try:
        result = await sync_to_async(callback)(*args, **kwargs)

        if hasattr(view_instance, "handle_async_result"):
            await sync_to_async(view_instance.handle_async_result)(
                task_name, result=result, error=None
            )

        if hasattr(view_instance, "_sync_state_to_rust"):
            await sync_to_async(view_instance._sync_state_to_rust)()

        html, patches, version = await sync_to_async(view_instance.render_with_diff)()

        if patches is not None:
            patch_list = json.loads(patches) if patches else []
            msg: Dict[str, Any] = {
                "type": "patch",
                "patches": patch_list,
                "version": version,
                "event_name": event_name,
            }
        else:
            html_stripped = view_instance._strip_comments_and_whitespace(html)
            html_content = view_instance._extract_liveview_content(html_stripped)
            msg = {
                "type": "html_update",
                "html": html_content,
                "version": version,
                "event_name": event_name,
            }

        session.push(msg)
        _flush_push_events_to_queue(session, view_instance)

    except Exception as exc:
        logger.exception(
            "SSE: error in start_async callback '%s' on %s",
            task_name,
            view_instance.__class__.__name__ if view_instance else "?",
        )
        if hasattr(view_instance, "handle_async_result"):
            try:
                await sync_to_async(view_instance.handle_async_result)(
                    task_name, result=None, error=exc
                )
                if hasattr(view_instance, "_sync_state_to_rust"):
                    await sync_to_async(view_instance._sync_state_to_rust)()
                html, patches, version = await sync_to_async(view_instance.render_with_diff)()
                if patches is not None:
                    patch_list = json.loads(patches) if patches else []
                    session.push({"type": "patch", "patches": patch_list, "version": version})
                else:
                    html_stripped = view_instance._strip_comments_and_whitespace(html)
                    html_content = view_instance._extract_liveview_content(html_stripped)
                    session.push({"type": "html_update", "html": html_content, "version": version})
            except Exception:
                logger.exception("SSE: error in handle_async_result for task '%s'", task_name)


# ------------------------------------------------------------------ #
# Django Views
# ------------------------------------------------------------------ #


class DjustSSEStreamView(View):
    """
    GET endpoint that establishes an SSE stream and mounts the LiveView.

    Query parameters
    ----------------
    view
        Dotted Python path to the LiveView class, e.g.
        ``myapp.views.DashboardView``. **Required.**

    Any additional query parameters are forwarded to ``mount(request, **kwargs)``.

    The response is ``text/event-stream``. Each event is a JSON-encoded djust
    protocol message on a single ``data:`` line followed by two newlines.
    Keepalive comments (``:``) are sent every 25 seconds to prevent proxy
    timeouts.
    """

    async def get(self, request, session_id: str):
        # Validate session_id is a valid UUID to prevent path traversal
        try:
            uuid.UUID(session_id)
        except ValueError:
            return JsonResponse({"error": "Invalid session ID"}, status=400)

        view_path = request.GET.get("view")
        if not view_path:
            return JsonResponse({"error": "Missing required ?view= parameter"}, status=400)

        # Create session and register it
        session = SSESession(session_id)
        session._client_ip = _client_ip_from_request(request)
        _sse_sessions[session_id] = session

        # Mount the view; pushes "mount" message (or "error") to the queue
        await _sse_mount_view(session, request, view_path)

        async def event_stream():
            # Send connection acknowledgment immediately
            yield f"data: {json.dumps({'type': 'sse_connect', 'session_id': session_id})}\n\n"

            try:
                while session.active:
                    try:
                        msg = await asyncio.wait_for(
                            session.queue.get(), timeout=_KEEPALIVE_TIMEOUT
                        )
                        if msg is None:
                            # Sentinel: stream closed deliberately
                            break
                        yield f"data: {json.dumps(msg, cls=DjangoJSONEncoder)}\n\n"
                    except asyncio.TimeoutError:
                        # SSE keepalive comment — prevents proxy timeout
                        yield ": keepalive\n\n"
            finally:
                # Linger briefly so in-flight event POSTs can still find the session
                await asyncio.sleep(_SESSION_LINGER_S)
                _sse_sessions.pop(session_id, None)
                logger.debug("SSE: session %s closed", session_id)

        response = StreamingHttpResponse(
            event_stream(),
            content_type="text/event-stream; charset=utf-8",
        )
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"  # Disable nginx buffering
        return response


@method_decorator(csrf_exempt, name="dispatch")
class DjustSSEEventView(View):
    """
    POST endpoint for client → server events over the SSE transport.

    Request body (JSON)::

        {
            "event": "handler_name",
            "params": {"key": "value"}
        }

    Response: ``{"ok": true}`` — the actual DOM update is pushed via SSE.

    CSRF is exempted because:
    1. The SSE session ID in the URL acts as an opaque CSRF token.
    2. The client JS sends the session ID it received from the server.
    3. Simple-request POST bodies (``application/json``) are blocked by browsers
       for cross-origin requests unless CORS headers allow them.
    """

    async def post(self, request, session_id: str):
        session = _get_session(session_id)
        if not session:
            return JsonResponse(
                {"error": "SSE session not found or expired. Please reload the page."}, status=404
            )

        if not session.view_instance:
            return JsonResponse({"error": "View not mounted yet"}, status=503)

        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({"error": "Request body must be valid JSON"}, status=400)

        event_name = body.get("event")
        params = body.get("params") or {}

        if not event_name:
            return JsonResponse({"error": "Missing 'event' field in request body"}, status=400)

        if not isinstance(params, dict):
            return JsonResponse({"error": "'params' must be a JSON object"}, status=400)

        await _sse_handle_event(session, event_name, params)
        return JsonResponse({"ok": True})



# ------------------------------------------------------------------ #
# URL patterns
# ------------------------------------------------------------------ #

from django.urls import path  # noqa: E402 — import here to keep module-level imports clean

sse_urlpatterns = [
    path("sse/<str:session_id>/", DjustSSEStreamView.as_view(), name="djust-sse-stream"),
    path("sse/<str:session_id>/event/", DjustSSEEventView.as_view(), name="djust-sse-event"),
]
