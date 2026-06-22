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
import inspect
import json
import logging
import time
import uuid
from typing import Any, Dict, Optional

from asgiref.sync import sync_to_async
from django.http import HttpResponseForbidden, JsonResponse, StreamingHttpResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from .rate_limit import ConnectionRateLimiter
from .security import handle_exception, sanitize_for_log
from .serialization import DjangoJSONEncoder
from .validation import validate_handler_params
from .websocket import (
    _compute_changed_keys,
    _is_allowed_origin,
    _snapshot_assigns,
    _tenant_context,
)
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

# ---- Resource-exhaustion caps (Finding #25, CWE-770/CWE-400) ----------------
# The WebSocket transport throttles abusive clients via ConnectionRateLimiter;
# the SSE stream-GET path had no equivalent ceiling, so a scripted client could
# allocate unbounded long-lived sessions (each a registered SSESession + queue +
# mounted view). These caps bound that growth. Both are module constants with
# sensible defaults, overridable per-project via settings.
#
# Per-client cap is keyed by owner principal (authenticated user pk, else the
# anonymous Django session key — see Finding #24), falling back to client IP.
# Exceeding it returns HTTP 429. The global cap on len(_sse_sessions) returns
# HTTP 503 when the whole process is saturated.
_MAX_SESSIONS_PER_CLIENT = 20
_MAX_SESSIONS_TOTAL = 10_000


def _max_sessions_per_client() -> int:
    """Per-client live-session cap (settings-overridable)."""
    from django.conf import settings

    return int(getattr(settings, "DJUST_SSE_MAX_SESSIONS_PER_CLIENT", _MAX_SESSIONS_PER_CLIENT))


def _max_sessions_total() -> int:
    """Global live-session cap (settings-overridable)."""
    from django.conf import settings

    return int(getattr(settings, "DJUST_SSE_MAX_SESSIONS_TOTAL", _MAX_SESSIONS_TOTAL))


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

        # ---- Owner binding (Finding #24, CWE-639/CWE-862) -------------------
        # The client-chosen session_id alone is NOT an authorization capability:
        # whoever learns it could otherwise drive this view with the MOUNTER's
        # captured request.user. We bind the session to its creating principal
        # at stream-GET creation and re-verify on every event/message POST.
        #
        #   * authenticated mounter -> bound by user pk (_owner_user_pk)
        #   * anonymous mounter      -> bound by Django session key
        #     (_owner_session_key; forced non-None at GET so anonymous sessions
        #     are tied to the browser session cookie)
        self._owner_user_pk: Optional[Any] = None
        self._owner_session_key: Optional[str] = None

        # Lazy: avoid circular import at module load. The runtime is the
        # transport-agnostic dispatcher (#1237) and is shared with the WS
        # consumer's ``handle_url_change`` shim.
        from .runtime import SSESessionTransport, ViewRuntime

        self.runtime = ViewRuntime(SSESessionTransport(self), rate_limiter=self._rate_limiter)

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


def _request_user_pk(request) -> Optional[Any]:
    """Authenticated user pk for *request*, or None for anonymous/no-auth."""
    user = getattr(request, "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        return getattr(user, "pk", None)
    return None


def _request_session_key(request) -> Optional[str]:
    """Django session key for *request*, or None when there is no session."""
    session = getattr(request, "session", None)
    if session is None:
        return None
    return getattr(session, "session_key", None)


def _request_owns_session(request, session: "SSESession") -> bool:
    """Return True iff *request* is from the principal that created *session*.

    Owner-binding check shared by BOTH SSE POST endpoints (Finding #24 —
    don't duplicate, per the parallel-path-drift rule). The simplest correct
    rule:

      * Authenticated owner (``_owner_user_pk`` is not None): the POSTer must
        be authenticated as the SAME user pk.
      * Anonymous owner (``_owner_user_pk`` is None): the POSTer must present
        the SAME Django session key the stream-GET was bound to. (The GET
        forces a non-None session key for anonymous clients, so this binds the
        session to the browser session cookie.)

    A leaked ``session_id`` is therefore useless without also presenting the
    owner's auth/session cookie.
    """
    if session._owner_user_pk is not None:
        return _request_user_pk(request) == session._owner_user_pk
    # Anonymous owner: bound by session key (forced non-None at GET).
    if session._owner_session_key is None:
        # Defensive: an unbound session can't be owned by anyone.
        return False
    return _request_session_key(request) == session._owner_session_key


def _client_cap_key(request) -> str:
    """Per-client key for the concurrency cap (Finding #25).

    Keyed by owner principal — authenticated user pk, else the anonymous
    Django session key (the same identity used for owner-binding, Finding #24)
    — falling back to client IP when neither is available.
    """
    user_pk = _request_user_pk(request)
    if user_pk is not None:
        return f"user:{user_pk}"
    session_key = _request_session_key(request)
    if session_key:
        return f"session:{session_key}"
    return f"ip:{_client_ip_from_request(request) or 'unknown'}"


def _count_sessions_for_client(cap_key: str) -> int:
    """Number of live registered sessions owned by *cap_key* (Finding #25)."""
    count = 0
    for session in _sse_sessions.values():
        if session._owner_user_pk is not None:
            key = f"user:{session._owner_user_pk}"
        elif session._owner_session_key:
            key = f"session:{session._owner_session_key}"
        else:
            key = f"ip:{session._client_ip or 'unknown'}"
        if key == cap_key:
            count += 1
    return count


def _client_ip_from_request(request) -> Optional[str]:
    """Trustworthy client IP for the SSE transport.

    Defaults to ``REMOTE_ADDR``; ``X-Forwarded-For`` is honored only when
    ``DJUST_TRUSTED_PROXY_COUNT`` is set (peeled from the right). Shared with
    the WS path via :func:`djust._client_ip.resolve_client_ip` so the two
    transports can't drift (finding #5).
    """
    from ._client_ip import resolve_client_ip

    return resolve_client_ip(
        request.META.get("HTTP_X_FORWARDED_FOR"),
        request.META.get("REMOTE_ADDR"),
    )


# ------------------------------------------------------------------ #
# Mount helper
# ------------------------------------------------------------------ #


async def _sse_mount_view(session: SSESession, request, view_path: str) -> bool:
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

    # ---- Security (F22 — unsafe reflection / arbitrary module import) ----
    # Resolve the client-supplied dotted view path through the single shared
    # resolver: shape-check → allowlist BEFORE importing anything (fail-closed
    # to LIVEVIEW_ALLOWED_MODULES, else INSTALLED_APPS roots + djust) →
    # import_module + vars() (PEP 562-safe) → LiveView subclass check. Shared
    # with the WebSocket / runtime paths so they cannot drift (#1646).
    from .security.mount import resolve_view_class

    resolution = resolve_view_class(view_path)
    if not resolution:
        logger.warning(
            "SSE: blocked/failed mount of view: %s (%s)",
            sanitize_for_log(view_path),
            resolution.generic,
        )
        session.push({"type": "error", "error": _safe_error(resolution.detail, resolution.generic)})
        return False
    view_class = resolution.view_class

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
        return False

    # Mark this view as using the SSE transport (for introspection / limits)
    view_instance._sse_session_id = session.session_id
    view_instance._sse_session = session
    view_instance._websocket_path = request.path
    view_instance._websocket_query_string = request.META.get("QUERY_STRING", "")
    # Use a stable session-scoped ID so VDOM caching works the same as WS
    view_instance._websocket_session_id = session.session_id

    session.view_instance = view_instance

    # ---- Pre-mount security sequence (auth + tenant resolve/bind) ----
    # Single-sourced through djust.auth.core.run_pre_mount_auth so this legacy
    # SSE mount path cannot drift from the WS (handle_mount) + runtime
    # (dispatch_mount) paths in WHICH checks run or in WHAT ORDER (#1646 / #1853).
    # The helper runs the same leaf chokepoints this path used inline before:
    # check_view_auth (may raise PermissionDenied / return a redirect URL), then
    # — only on auth success — _ensure_tenant + the tenant ContextVar bind. The
    # SSE-specific verdict→envelope mapping (error/navigate push, return False)
    # stays here. A tenant-resolution failure (or a buggy custom check_permissions
    # hook raising a non-PermissionDenied) gets the mount-error envelope and
    # aborts (fail-closed), consolidating the legacy split tenant/auth catches.
    from .auth import run_pre_mount_auth
    from django.core.exceptions import PermissionDenied

    try:
        redirect_url = await sync_to_async(run_pre_mount_auth)(view_instance, request)
    except PermissionDenied:
        session.push({"type": "error", "error": "Permission denied"})
        return False
    except Exception as exc:  # noqa: BLE001 — fail-closed (tenant / auth-hook error)
        response = handle_exception(
            exc,
            error_type="mount",
            view_class=view_path,
            logger=logger,
            log_message="Error in pre-mount security sequence for %s" % sanitize_for_log(view_path),
        )
        session.push(response)
        return False

    if redirect_url:
        session.push({"type": "navigate", "to": redirect_url})
        # Auth succeeded but the view redirects elsewhere — no usable view is
        # mounted, so this is treated as a non-mount: the navigate message is
        # delivered over the stream, then the session is torn down and never
        # registered for POST routing (Finding #25 register-after-mount).
        return False

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
        return False

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
        return False

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
    logger.info(
        "SSE: mounted view %s (session %s)",
        sanitize_for_log(view_path),
        sanitize_for_log(session.session_id),
    )
    return True


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
    """Dispatch a client event over the legacy SSE path, tenant-scoped.

    Thin wrapper that binds the tenant context (Finding #6) for the duration of
    the event so the handler + render see the correct tenant in the
    tenant-scoped managers. Cleared on exit.
    """
    view_instance = session.view_instance
    tenant = getattr(view_instance, "_tenant", None) if view_instance else None
    with _tenant_context(tenant):
        await _sse_handle_event_inner(session, event_name, params)


async def _sse_handle_event_inner(
    session: SSESession, event_name: str, params: Dict[str, Any]
) -> None:
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
            log_message="Error in SSE event handler %s.%s()"
            % (view_instance.__class__.__name__, sanitize_for_log(event_name)),
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

    # Flush push_events, navigation commands, and deferred callbacks
    _flush_push_events_to_queue(session, view_instance)
    _flush_navigation_to_queue(session, view_instance)
    await _flush_deferred_to_sse(view_instance)

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


async def _flush_deferred_to_sse(view_instance) -> None:
    """Drain ``self.defer(...)`` callbacks from the view and execute them.

    Mirrors :meth:`LiveViewConsumer._flush_deferred` for the SSE transport.
    Without this, ``defer()`` calls on a view served over SSE would
    accumulate in ``_deferred_callbacks`` indefinitely (slow leak +
    contract violation — Phoenix-parity says "after each render+patch").

    Sync callbacks run directly; async callbacks (``async def`` or
    coroutine-returning) are awaited inline. A failing callback logs at
    WARN with traceback and execution continues to the next callback —
    a deferred callback's failure must not break the SSE stream or the
    user's interactive flow.
    """
    if not view_instance:
        return
    if not hasattr(view_instance, "_drain_deferred"):
        return
    callbacks = view_instance._drain_deferred()
    # Defensive: same shape as ``LiveViewConsumer._flush_deferred`` —
    # guard against test mocks (Mock view returns Mock, not list) and
    # any legacy view that overrode ``_drain_deferred`` to return non-list.
    if not isinstance(callbacks, list) or not callbacks:
        return
    for callback, args, kwargs in callbacks:
        try:
            result = callback(*args, **kwargs)
            if inspect.iscoroutine(result):
                await result
        except Exception:
            logger.warning(
                "[djust SSE] Deferred callback %s on %s raised; continuing to next",
                getattr(callback, "__qualname__", repr(callback)),
                view_instance.__class__.__name__,
                exc_info=True,
            )


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
        await _flush_deferred_to_sse(view_instance)

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


def _sse_origin_allowed(request) -> bool:
    """
    SSE CSRF defense: validate the request ``Origin`` against ALLOWED_HOSTS.

    This is the SSE transport's *real* CSRF protection — the same model as the
    WebSocket transport's CSWSH check (``_is_allowed_origin`` /
    ``AllowedHostsOriginValidator``, #653). The client→server SSE endpoints are
    ``@csrf_exempt`` (no Django CSRF cookie/token), so without an Origin check a
    cross-origin page could drive a victim-cookie-authenticated SSE session
    (mount views, fire state-changing handlers) using ``credentials: include``
    (Finding #7, CWE-352).

    A browser ALWAYS sends ``Origin`` on a cross-origin request, so an attacker
    page's Origin won't match ALLOWED_HOSTS and is rejected. Same-origin requests
    pass. Non-browser clients (curl, native, tests) send no Origin and are
    allowed — the helper allows missing/empty Origin by design.
    """
    origin = request.META.get("HTTP_ORIGIN")
    # ``_is_allowed_origin`` takes the Origin as bytes (WS layer passes header
    # bytes). For SSE the header is an str, so encode it; missing -> b"" -> allowed.
    return _is_allowed_origin((origin or "").encode("utf-8", "surrogatepass"))


def _sse_content_type_is_json(request) -> bool:
    """
    Defense-in-depth: require ``Content-Type: application/json`` on SSE POSTs.

    Closes the CORS *simple-request* bypass: a JSON body sent with
    ``Content-Type: text/plain`` is a CORS simple request that needs no preflight
    and can be sent cross-origin from an attacker page. Requiring a custom
    (non-simple) content type forces a CORS preflight cross-origin, which the
    attacker's page cannot satisfy. (The real djust client always POSTs
    ``application/json``.)
    """
    content_type = (request.content_type or "").lower()
    return content_type == "application/json"


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
        # ---- SSE CSRF defense: reject cross-origin handshakes (Finding #7) ----
        # Must run BEFORE creating/mounting the session: an attacker page driving
        # GET ?view=... would otherwise mount a LiveView as the victim via cookies.
        #
        # Known limitation (mirrors the WebSocket transport's missing-Origin
        # policy): a *no-Origin* cross-origin GET (e.g. via <img>/<iframe>/top-
        # nav, which send cookies but no Origin) still reaches session-create +
        # mount. This is acceptable because (a) the two-step CSRF attack is
        # broken at the POST step — every client->server POST is Origin-gated
        # below; (b) the attacker cannot read the opaque cross-origin
        # text/event-stream; and (c) _sse_mount_view enforces the view-import
        # allowlist + check_view_auth, so only victim-authorized views mount and
        # only mount-time write side effects remain (the same residual as the
        # framework's HTTP GET-render path). Requiring an Origin on GET would
        # break legitimate non-browser SSE clients (curl, native).
        if not _sse_origin_allowed(request):
            logger.warning(
                "SSE: rejected stream GET from disallowed origin %s",
                sanitize_for_log(request.META.get("HTTP_ORIGIN", "")),
            )
            return HttpResponseForbidden("Origin not allowed")

        # Validate session_id is a valid UUID to prevent path traversal.
        # Use the canonical string form to break the taint chain from the URL parameter.
        try:
            session_id = str(uuid.UUID(session_id))
        except ValueError:
            return JsonResponse({"error": "Invalid session ID"}, status=400)

        view_path = request.GET.get("view")
        if not view_path:
            return JsonResponse({"error": "Missing required ?view= parameter"}, status=400)

        # ---- Owner binding (Finding #24) ----
        # Capture the creating principal so event/message POSTs can be verified
        # against it. For anonymous clients, force a Django session key to exist
        # BEFORE reading it so the session is bound to the browser session
        # cookie (an unbound owner is unownable).
        owner_user_pk = _request_user_pk(request)
        owner_session_key = _request_session_key(request)
        if owner_user_pk is None and owner_session_key is None:
            django_session = getattr(request, "session", None)
            if django_session is not None:
                await sync_to_async(django_session.save)()
                owner_session_key = django_session.session_key

        # ---- Resource-exhaustion caps (Finding #25) ----
        # Reject (without allocating/registering a session) when the process is
        # globally saturated (503) or this client already holds too many live
        # sessions (429). Checked BEFORE creation so a flood leaves no live
        # session behind.
        if len(_sse_sessions) >= _max_sessions_total():
            logger.warning(
                "SSE: global session cap reached (%d) — rejecting stream GET",
                len(_sse_sessions),
            )
            return JsonResponse(
                {"error": "Server is at capacity. Please try again later."},
                status=503,
            )
        cap_key = _client_cap_key(request)
        if _count_sessions_for_client(cap_key) >= _max_sessions_per_client():
            logger.warning(
                "SSE: per-client session cap reached for %s — rejecting stream GET",
                sanitize_for_log(cap_key),
            )
            return JsonResponse(
                {"error": "Too many concurrent SSE sessions for this client."},
                status=429,
            )

        # Create the session and bind it to its owner. NOTE: not yet registered
        # in _sse_sessions — registration happens only after a successful mount
        # (Finding #25 register-after-mount), so an unauthorized/errored mount
        # leaves no POST-routable session behind.
        session = SSESession(session_id)
        session._client_ip = _client_ip_from_request(request)
        session._owner_user_pk = owner_user_pk
        session._owner_session_key = owner_session_key

        # Mount the view; pushes "mount" message (or "error"/"navigate") to the
        # queue. Returns True only when a usable view is fully mounted.
        mounted = await _sse_mount_view(session, request, view_path)
        if mounted:
            _sse_sessions[session_id] = session
        else:
            # Failed/unauthorized/redirecting mount: do NOT register the session
            # (no POST can drive it; it isn't counted against the caps). The
            # stream below still drains the queued error/navigate message, then
            # closes promptly.
            session.shutdown()

        async def event_stream():
            # Send connection acknowledgment immediately
            yield f"data: {json.dumps({'type': 'sse_connect', 'session_id': session_id})}\n\n"

            try:
                # Drain any messages already queued before streaming begins (the
                # "mount"/"error"/"navigate" pushed synchronously above). When the
                # mount failed, shutdown() already queued the sentinel, so this
                # delivers the error/navigate even though session.active is False.
                while not session.queue.empty():
                    msg = session.queue.get_nowait()
                    if msg is None:
                        break
                    yield f"data: {json.dumps(msg, cls=DjangoJSONEncoder)}\n\n"

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
                if mounted:
                    # Linger briefly so in-flight event POSTs can still find the
                    # session (only meaningful for registered/mounted sessions).
                    await asyncio.sleep(_SESSION_LINGER_S)
                    _sse_sessions.pop(session_id, None)
                logger.debug("SSE: session %s closed", sanitize_for_log(session_id))

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

    ``@csrf_exempt`` removes Django's CSRF cookie/token check, so this endpoint's
    CSRF defense is the **Origin allowlist** (``_sse_origin_allowed``) — the same
    model as the WebSocket transport's CSWSH check (``AllowedHostsOriginValidator``
    / ``_is_allowed_origin``, #653). The session_id in the URL is NOT a CSRF token:
    it is client-chosen (``DjustSSEStreamView.get`` only validates UUID *format*),
    so it provides no cross-origin protection. The Origin check + the
    ``application/json`` content-type requirement together close Finding #7
    (CWE-352): a cross-origin page cannot forge a same-origin ``Origin`` and cannot
    send a custom content type without a CORS preflight it can't satisfy.
    """

    async def post(self, request, session_id: str):
        # ---- SSE CSRF defense (Finding #7): Origin allowlist + JSON content type ----
        if not _sse_origin_allowed(request):
            logger.warning(
                "SSE: rejected event POST from disallowed origin %s",
                sanitize_for_log(request.META.get("HTTP_ORIGIN", "")),
            )
            return HttpResponseForbidden("Origin not allowed")
        if not _sse_content_type_is_json(request):
            return JsonResponse({"error": "Content-Type must be application/json"}, status=415)

        session = _get_session(session_id)
        if not session:
            return JsonResponse(
                {"error": "SSE session not found or expired. Please reload the page."}, status=404
            )

        # ---- Owner binding (Finding #24): the POSTer must own the session ----
        # The client-chosen session_id is not an authorization capability; a
        # leaked id must not let a third party drive the mounter's view with the
        # mounter's captured request.user.
        if not _request_owns_session(request, session):
            logger.warning(
                "SSE: rejected event POST for session %s — requester is not the owner",
                sanitize_for_log(session_id),
            )
            return JsonResponse({"error": "forbidden"}, status=403)

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


@method_decorator(csrf_exempt, name="dispatch")
class DjustSSEMessageView(View):
    """
    POST endpoint for client -> server messages over the SSE transport
    (introduced in #1237).

    Accepts the same JSON envelope shape as a WebSocket frame::

        {"type": "mount", "view": ..., "params": ..., "url": ...}
        {"type": "event", "event": "increment", "params": {...}}
        {"type": "url_change", "params": {...}, "uri": "..."}

    Delegates to ``session.runtime.dispatch_message(body)`` which routes
    by type to the appropriate dispatch method on the shared runtime.

    Response body is always ``{"ok": true}`` — the actual DOM update (or
    error envelope) is pushed via the SSE stream.

    The legacy ``DjustSSEEventView`` (``/event/``) remains as a deprecated
    alias for back-compat.

    ``@csrf_exempt`` removes Django's CSRF check; the real CSRF defense is the
    **Origin allowlist** (``_sse_origin_allowed``) plus the ``application/json``
    content-type requirement — the same model as the WebSocket transport's CSWSH
    check (#653). The URL session_id is client-chosen and is NOT a CSRF token.
    See Finding #7 (CWE-352).
    """

    async def post(self, request, session_id: str):
        # ---- SSE CSRF defense (Finding #7): Origin allowlist + JSON content type ----
        if not _sse_origin_allowed(request):
            logger.warning(
                "SSE: rejected message POST from disallowed origin %s",
                sanitize_for_log(request.META.get("HTTP_ORIGIN", "")),
            )
            return HttpResponseForbidden("Origin not allowed")
        if not _sse_content_type_is_json(request):
            return JsonResponse({"error": "Content-Type must be application/json"}, status=415)

        session = _get_session(session_id)
        if not session:
            return JsonResponse(
                {"error": "SSE session not found or expired. Please reload the page."},
                status=404,
            )

        # ---- Owner binding (Finding #24): the POSTer must own the session ----
        # Same shared check as the legacy /event/ endpoint (don't duplicate the
        # rule — #1646). A leaked session_id must not let a third party drive
        # the mounter's view with the mounter's captured request.user.
        if not _request_owns_session(request, session):
            logger.warning(
                "SSE: rejected message POST for session %s — requester is not the owner",
                sanitize_for_log(session_id),
            )
            return JsonResponse({"error": "forbidden"}, status=403)

        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({"error": "Request body must be valid JSON"}, status=400)

        if not isinstance(body, dict):
            return JsonResponse({"error": "Request body must be a JSON object"}, status=400)

        # Route through the shared runtime. Validation of frame-specific
        # shape (e.g., mount needs 'view') is handled by the runtime,
        # which pushes structured error envelopes onto the SSE queue.
        await session.runtime.dispatch_message(body)
        return JsonResponse({"ok": True})


# ------------------------------------------------------------------ #
# URL patterns
# ------------------------------------------------------------------ #

from django.urls import path  # noqa: E402 — import here to keep module-level imports clean

sse_urlpatterns = [
    path("sse/<str:session_id>/", DjustSSEStreamView.as_view(), name="djust-sse-stream"),
    path("sse/<str:session_id>/event/", DjustSSEEventView.as_view(), name="djust-sse-event"),
    path(
        "sse/<str:session_id>/message/",
        DjustSSEMessageView.as_view(),
        name="djust-sse-message",
    ),
]
