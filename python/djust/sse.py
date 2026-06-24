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
import uuid
from typing import Any, AsyncIterator, Dict, Optional

from asgiref.sync import sync_to_async
from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseForbidden,
    JsonResponse,
    StreamingHttpResponse,
)
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from .rate_limit import ConnectionRateLimiter
from .security import sanitize_for_log
from .serialization import DjangoJSONEncoder
from .websocket import (
    _is_allowed_origin,
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

        # The live event-POST request, stamped by the /event/ + /message/ endpoints
        # just before dispatch so the per-event auth re-check
        # (SSESessionTransport.recheck_event_auth, #1777) validates against the
        # CURRENT POSTer's request.user — not the stale mount request. None until
        # the first event POST (the re-check falls back to ``_request`` defensively).
        self._event_request: Optional[Any] = None

        # The mount-time request (the original stream-GET's request), stamped by
        # DjustSSEStreamView.get just before dispatch_mount. Read by
        # SSESessionTransport.build_request (runtime.py:1515) so the runtime mount
        # path sees the authenticated user/session/path. None until the stream-GET
        # stamps it.
        self._request: Optional[Any] = None

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


def _request_user_pk(request: HttpRequest) -> Optional[Any]:
    """Authenticated user pk for *request*, or None for anonymous/no-auth."""
    user = getattr(request, "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        return getattr(user, "pk", None)
    return None


def _request_session_key(request: HttpRequest) -> Optional[str]:
    """Django session key for *request*, or None when there is no session."""
    session = getattr(request, "session", None)
    if session is None:
        return None
    return getattr(session, "session_key", None)


def _request_owns_session(request: HttpRequest, session: "SSESession") -> bool:
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
        return bool(_request_user_pk(request) == session._owner_user_pk)
    # Anonymous owner: bound by session key (forced non-None at GET).
    if session._owner_session_key is None:
        # Defensive: an unbound session can't be owned by anyone.
        return False
    return _request_session_key(request) == session._owner_session_key


def _client_cap_key(request: HttpRequest) -> str:
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


def _client_ip_from_request(request: HttpRequest) -> Optional[str]:
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


async def _flush_deferred_to_sse(view_instance: Any) -> None:
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


# ------------------------------------------------------------------ #
# Django Views
# ------------------------------------------------------------------ #


def _sse_origin_allowed(request: HttpRequest) -> bool:
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


def _sse_content_type_is_json(request: HttpRequest) -> bool:
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

    async def get(self, request: HttpRequest, session_id: str) -> HttpResponse:
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
        # text/event-stream; and (c) the runtime mount path (dispatch_mount)
        # enforces the view-import allowlist + check_view_auth, so only
        # victim-authorized views mount and
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
        # Stash the REAL HTTP request so the runtime mounts against it (real
        # request.user / session / path for auth + object-perm) — the converged
        # runtime mount path reads it via SSESessionTransport.build_request
        # (#1887, ADR-022 Iter 1). Without this the runtime would synthesize a
        # userless RequestFactory request and deny every authenticated SSE view.
        session._request = request

        # Mount the view through the shared ViewRuntime (#1887, ADR-022 Iter 1):
        # converges the legacy bespoke _sse_mount_view onto dispatch_mount, the
        # SAME spine the WS url_change path + the SSE /message/ endpoint already
        # use, so the SSE mount can no longer drift from the runtime (#1646).
        # dispatch_mount pushes "mount" (or "error"/"navigate") onto the queue
        # and sets runtime.view_instance only on a fully-successful mount; that
        # is the SSE 'mounted' gate (it replaces the legacy bool return).
        #
        # Data-dict shape mirrors the WS mount frame + the existing dispatch_*
        # callers: 'view' is the ?view= path; 'url' is the real request.path so
        # _resolve_url_kwargs extracts pk/slug pattern kwargs; 'params' carries
        # the query-string params the legacy path merged into mount_kwargs
        # (every GET item except the 'view' selector itself).
        mount_params = {k: v for k, v in request.GET.items() if k != "view"}
        await session.runtime.dispatch_mount(
            {
                "type": "mount",
                "view": view_path,
                "url": request.path,
                "params": mount_params,
            }
        )
        mounted = session.runtime.view_instance is not None
        if mounted:
            _sse_sessions[session_id] = session
        else:
            # Failed/unauthorized/redirecting mount: do NOT register the session
            # (no POST can drive it; it isn't counted against the caps). The
            # stream below still drains the queued error/navigate message, then
            # closes promptly.
            session.shutdown()

        async def event_stream() -> AsyncIterator[str]:
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

    async def post(self, request: HttpRequest, session_id: str) -> HttpResponse:
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
        # Forward the client-sent ``ref`` (#560 ref echo, ADR-022 Iter 2 Phase
        # 2.0; #1891). The runtime's ``_dispatch_event_render`` reads ``ref`` from
        # the TOP LEVEL of the data dict (runtime.py:1489) to echo it on the noop /
        # update frame so the client can match responses to requests. The original
        # ``{type,event,params}`` rebuild dropped it, so the end-to-end ref echo
        # worked via the SSE ``/message/`` endpoint (which forwards the raw body)
        # but NOT via this ``/event/`` alias. Carry it through verbatim — the
        # runtime coerces it to int / None, so no validation is needed here.
        ref = body.get("ref")

        if not event_name:
            return JsonResponse({"error": "Missing 'event' field in request body"}, status=400)

        if not isinstance(params, dict):
            return JsonResponse({"error": "'params' must be a JSON object"}, status=400)

        # Dispatch through the shared ViewRuntime (#1887, ADR-022 Iter 1):
        # converges the legacy bespoke _sse_handle_event onto dispatch_event,
        # the SAME spine the SSE /message/ endpoint already routes through, so
        # the legacy /event/ alias can no longer drift from the runtime (#1646).
        # The runtime's dispatch_event applies the tenant context, runs the same
        # event security + param validation, renders, and (this PR) dispatches
        # start_async / @background work + the full 8-queue flush. Data-dict
        # shape matches the existing dispatch_message 'event' frame.
        #
        # Stamp the LIVE event-POST request on the session so the per-event auth
        # re-check (SSESessionTransport.recheck_event_auth, #1777 / ADR-022 Iter 2
        # Phase 2.3a) re-validates against the CURRENT POSTer's request.user — not
        # the stale mount request. Owner-binding (Finding #24) already ran above,
        # so this request is the session owner's.
        session._event_request = request
        await session.runtime.dispatch_event(
            {"type": "event", "event": event_name, "params": params, "ref": ref}
        )
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

    async def post(self, request: HttpRequest, session_id: str) -> HttpResponse:
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
        #
        # Stamp the LIVE POST request so an ``event`` frame routed through
        # dispatch_message → dispatch_event re-validates per-event auth against the
        # current POSTer (SSESessionTransport.recheck_event_auth, #1777). Same
        # rationale as the /event/ alias; the /message/ endpoint carries the same
        # owner-bound request.
        session._event_request = request
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
