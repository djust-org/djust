"""How each transport answers a turn's refusal, error or reauth (ADR-044 D5, D7).

``djust.turns.run_turn`` is wire-blind. The *port* a turn carries maps a
:class:`~djust.turns.Refusal` to the transport's established frames / responses
and runs the ``reauth`` built-in's fresh-principal check:

- :class:`RuntimeTurnPort` — turns the :class:`~djust.runtime.ViewRuntime`
  dispatches (mount, reconnect, event, params, async) over WebSocket and SSE.
- :class:`ConsumerTurnPort` — the WebSocket consumer's own server turns (push,
  tick, ``db_notify``, presence, async results, recovery render).
- :func:`http_refusal_response` — HTTP GET / POST and the auto-exposed API.
"""

from __future__ import annotations

import contextlib
import logging
import time
from typing import Any, Dict, Mapping, Optional

from .turns import Refusal, Turn

logger = logging.getLogger(__name__)

#: Fallback for ``LIVEVIEW_CONFIG["reauth_server_turn_interval"]``.
_SERVER_TURN_REAUTH_INTERVAL_S = 5.0

_MOUNT_KINDS = frozenset({"mount", "reconnect"})
_SERVER_KINDS = frozenset({"push", "async", "render"})


def login_url_for(turn: Turn, refusal: Refusal) -> str:
    """``view.login_url`` or ``settings.LOGIN_URL``, with ``?next=`` when given."""
    from django.conf import settings

    view_class = turn.view_class
    url = (
        getattr(turn.view, "login_url", None)
        or getattr(view_class, "login_url", None)
        or getattr(settings, "LOGIN_URL", "/accounts/login/")
    )
    from django.shortcuts import resolve_url

    url = str(resolve_url(url))
    if refusal.url:
        from django.utils.http import urlencode

        sep = "&" if "?" in url else "?"
        url = "%s%s%s" % (url, sep, urlencode({"next": safe_target(turn, refusal.url)}))
    return url


def safe_target(turn: Turn, url: Optional[str]) -> str:
    """``url`` when it stays on this site, else ``"/"`` (open-redirect guard)."""
    from django.utils.http import url_has_allowed_host_and_scheme

    if not url or not isinstance(url, str):
        return "/"
    request = turn._effective_request()
    hosts = None
    secure = False
    if request is not None:
        with contextlib.suppress(Exception):
            hosts = {request.get_host()}
            secure = bool(request.is_secure())
    if url_has_allowed_host_and_scheme(url, allowed_hosts=hosts, require_https=secure):
        return url
    logger.warning("Turn refusal redirect target rejected; falling back to '/'")
    return "/"


def _target_for(turn: Turn, refusal: Refusal) -> str:
    if refusal.kind == "login":
        return login_url_for(turn, refusal)
    return safe_target(turn, refusal.url)


def _ref_fields(turn: Turn) -> Dict[str, Any]:
    ref = turn._ref if turn.kind == "event" else None
    if type(ref) is int or type(ref) is float:
        try:
            return {"source": "event", "ref": int(ref)}
        except (ValueError, OverflowError):
            return {"source": "event"}
    return {"source": "event"} if turn.kind == "event" else {}


def _default_close(turn: Turn, refusal: Refusal) -> bool:
    if refusal.close is not None:
        return bool(refusal.close)
    if refusal.kind == "login":
        return True
    if refusal.kind == "deny":
        return turn.kind in _MOUNT_KINDS
    return False


async def reauth_live_turn(owner: Any, transport: Any, view: Any, *, server: bool) -> bool:
    """The ``reauth`` built-in's check, one implementation for WS and SSE.

    ``transport.recheck_event_auth`` reads the fresh principal and, on failure,
    sends the transport's refusal (navigate to login on WS, ``permission_denied``
    on SSE) and closes. A server turn's pass is remembered on ``owner`` (the WS
    consumer, or the SSE runtime) for ``reauth_server_turn_interval`` seconds, so
    a ticking view does not read the session store every tick. Fails closed.
    """
    from .config import config as djust_config

    if server:
        try:
            interval = float(djust_config.get("reauth_server_turn_interval") or 0)
        except (TypeError, ValueError):
            interval = _SERVER_TURN_REAUTH_INTERVAL_S
        last = getattr(owner, "_server_turn_reauth_at", None)
        now = time.monotonic()
        if last is not None and interval > 0 and 0 <= now - last < interval:
            return True
    try:
        allowed = bool(await transport.recheck_event_auth(view))
    except Exception as exc:  # noqa: BLE001 - fail CLOSED; type + view class only
        logger.warning(
            "reauth_on_event re-check raised %s on view %s; denying the turn",
            type(exc).__name__,
            type(view).__name__,
        )
        allowed = False
        with contextlib.suppress(Exception):
            await transport.close(code=4403)
    if server:
        with contextlib.suppress(AttributeError):
            owner._server_turn_reauth_at = time.monotonic() if allowed else None
    elif not allowed:
        with contextlib.suppress(AttributeError):
            owner._server_turn_reauth_at = None
    return allowed


class RuntimeTurnPort:
    """Port methods for :class:`~djust.runtime.ViewRuntime` (mixed in).

    Uses the runtime's ``transport`` and ``view_instance`` attributes.
    """

    @property
    def turn_transport(self) -> str:
        return getattr(self.transport, "turn_transport", "ws")

    async def turn_reauth(self, turn: Turn, *, server: bool) -> bool:
        view = turn.view
        consumer = getattr(self.transport, "_consumer", None)
        if server and consumer is not None:
            # One throttle per WebSocket connection for every server turn.
            allowed = await consumer._reauth_legacy_server_turn(view, locked=turn._locked)
            if not allowed and self.view_instance is view:
                self.view_instance = None
            return allowed
        owner = consumer if consumer is not None else self
        allowed = await reauth_live_turn(owner, self.transport, view, server=server)
        if not allowed:
            # The client-event reauth has always cleared only the runtime's view
            # (#291); the transport closed, and the disconnect releases it.
            if self.view_instance is view:
                self.view_instance = None
        return allowed

    async def _drop_turn_view(self, turn: Turn) -> None:
        """Nothing keeps serving a view whose turn was refused with a close."""
        view = turn.view
        if self.view_instance is view:
            self.view_instance = None  # unconditional state-clear (#291)
        consumer = getattr(self.transport, "_consumer", None)
        drop = getattr(consumer, "_drop_revoked_view", None)
        if drop is not None:
            await drop(view, locked=turn._locked)

    async def turn_refused(self, turn: Turn, refusal: Refusal) -> None:
        transport = self.transport
        kind = refusal.kind
        is_mount = turn.kind in _MOUNT_KINDS
        close = _default_close(turn, refusal)
        if kind == "deny":
            message = refusal.message or "Permission denied"
            if turn.kind in _SERVER_KINDS:
                pass  # nobody asked: drop the turn, send nothing
            elif is_mount or close:
                if not await self._refused_view("permission_denied"):  # type: ignore[attr-defined]
                    await transport.send(
                        {"type": "error", "error": message, "code": "permission_denied"}
                    )
            else:
                await transport.send_error(
                    message,
                    code="permission_denied",
                    _exc_info=(None, None, None),
                    **_ref_fields(turn),
                )
            if is_mount:
                if close:
                    await self._finalize_mount_auth("permission_denied")  # type: ignore[attr-defined]
                return
        elif kind in ("login", "redirect"):
            target = _target_for(turn, refusal)
            reason = "login_required" if kind == "login" else "redirect"
            if not await self._refused_view(reason, target):  # type: ignore[attr-defined]
                await transport.send({"type": "navigate", "to": target})
            if is_mount:
                if close:
                    await self._finalize_mount_auth("redirect")  # type: ignore[attr-defined]
                return
            await self._send_turn_ack(turn)
        elif kind == "flash":
            await transport.send(
                {
                    "type": "flash",
                    "action": "put",
                    "level": refusal.level,
                    "message": refusal.message or "",
                }
            )
            await self._send_turn_ack(turn)
        elif kind == "rate_limited":
            if turn.kind not in _SERVER_KINDS:
                await transport.send(
                    {
                        "type": "rate_limit_exceeded",
                        "message": refusal.message or "Too many requests, please slow down",
                    }
                )
                await self._send_turn_ack(turn)
            return
        if close and not is_mount:
            await self._drop_turn_view(turn)
            with contextlib.suppress(Exception):
                await transport.close(code=4403)

    async def _send_turn_ack(self, turn: Turn) -> None:
        """A refused event that sent no error still ends the client's loading state."""
        if turn.kind != "event":
            return
        frame: Dict[str, Any] = {"type": "noop", "event_name": turn.event, **_ref_fields(turn)}
        with contextlib.suppress(Exception):
            await self.transport.send(frame)

    async def turn_failed(self, turn: Turn, exc: BaseException) -> bool:
        """The value-free error envelope for an exception escaping a turn.

        Returns False (re-raise to the transport's own handler) while
        diagnostics are allowed, as ``dispatch_message``'s catch always did.
        """
        from ._exposure_diagnostics import diagnostics_allowed
        from .security.error_handling import handle_exception

        if turn.kind in _SERVER_KINDS or turn.kind in _MOUNT_KINDS or turn.source == "deferred":
            # These keep their callers' established handling: an async
            # callback's failure still reaches its error arm; a mount failure
            # reaches the SSE stream's protected 500, mount_batch's failed[],
            # or dispatch_message's envelope.
            return False
        if diagnostics_allowed():
            return False
        response = handle_exception(exc, expose_details=False, logger=logger)  # type: ignore[arg-type]
        response.update(_ref_fields(turn))
        try:
            await self.transport.send(response)
        except Exception:  # noqa: BLE001 - do not leak delivery errors
            logger.error("Protected error response could not be delivered")
            try:
                await self.transport.close(code=1011)
            except Exception:  # noqa: BLE001 - even close failures must be value-free
                logger.error("Protected transport could not be closed")
        return True


class ConsumerTurnPort:
    """Port methods for the WebSocket consumer's own (server) turns (mixed in)."""

    view_instance: Any

    async def turn_reauth(self, turn: Turn, *, server: bool) -> bool:
        view = turn.view
        if server:
            return bool(
                await self._reauth_legacy_server_turn(view, locked=turn._locked)  # type: ignore[attr-defined]
            )
        from .runtime import WSConsumerTransport

        runtime = getattr(self, "_runtime", None)
        transport = getattr(runtime, "transport", None) or WSConsumerTransport(self)
        allowed = await reauth_live_turn(self, transport, view, server=False)
        if not allowed:
            await self._drop_revoked_view(view, locked=turn._locked)
        return allowed

    async def _drop_revoked_view(self, view: Any, *, locked: bool = True) -> None:
        """Drop and release a view whose principal no longer passes.

        The view stops being served at once. Its release (disconnect hooks,
        background work) runs under the session render lock, as it always
        has: now when the caller holds it (``locked``), else once it is free,
        so a turn rendering the view is never torn down mid-render.
        """
        runtime = getattr(self, "_runtime", None)
        if runtime is not None and runtime.view_instance is view:
            runtime.view_instance = None
        revoked = None
        if self.view_instance is view:
            revoked, self.view_instance = view, None
        if revoked is None:
            return
        if locked:
            await self._release_revoked_view(revoked)  # type: ignore[attr-defined]
            return

        async def release_when_free() -> None:
            async with self._render_lock:  # type: ignore[attr-defined]
                await self._release_revoked_view(revoked)  # type: ignore[attr-defined]

        import asyncio

        asyncio.ensure_future(release_when_free())

    async def turn_refused(self, turn: Turn, refusal: Refusal) -> None:
        kind = refusal.kind
        close = _default_close(turn, refusal)
        if kind in ("login", "redirect"):
            with contextlib.suppress(Exception):
                await self.send_json({"type": "navigate", "to": _target_for(turn, refusal)})  # type: ignore[attr-defined]
        elif kind == "flash":
            with contextlib.suppress(Exception):
                await self.send_json(  # type: ignore[attr-defined]
                    {
                        "type": "flash",
                        "action": "put",
                        "level": refusal.level,
                        "message": refusal.message or "",
                    }
                )
        elif kind == "deny" and turn.kind == "render":
            with contextlib.suppress(Exception):
                await self.send_error(  # type: ignore[attr-defined]
                    refusal.message or "Permission denied", code="permission_denied"
                )
        # deny / rate_limited on a push: nobody asked, the turn is dropped.
        if close:
            await self._drop_revoked_view(turn.view, locked=turn._locked)
            with contextlib.suppress(Exception):
                await self.close(code=4403)  # type: ignore[attr-defined]

    async def turn_failed(self, turn: Turn, exc: BaseException) -> bool:
        # The consumer's turns keep their own handlers (logged, or the receive
        # loop's error frame); the error propagates to them unchanged.
        return False


def http_refusal_response(turn: Turn, refusal: Refusal, *, json_body: bool) -> Any:
    """The HTTP answer to a refused turn (GET page: redirect / 403; POST / API: JSON)."""
    from django.http import HttpResponseForbidden, HttpResponseRedirect, JsonResponse

    kind = refusal.kind
    if turn.transport == "api":
        from .api.errors import api_error

        if kind == "login":
            return api_error(401, "login_required", refusal.message or "Authentication required")
        if kind == "redirect":
            return api_error(
                403,
                "permission_denied",
                refusal.message or "Permission denied",
                details={"redirect": safe_target(turn, refusal.url)},
            )
        if kind == "rate_limited":
            return api_error(429, "rate_limited", refusal.message or "Rate limit exceeded")
        return api_error(403, "permission_denied", refusal.message or "Permission denied")
    if kind in ("login", "redirect"):
        if refusal.kind == "login" and refusal.url is None and turn.request is not None:
            refusal = Refusal("login", url=turn.request.get_full_path())
        target = _target_for(turn, refusal)
        if json_body:
            return JsonResponse({"redirect": target}, status=403)
        return HttpResponseRedirect(target)
    if kind == "rate_limited":
        return JsonResponse(
            {"error": refusal.message or "Rate limit exceeded", "code": "rate_limited"},
            status=429,
        )
    message = refusal.message or "Permission denied"
    if json_body:
        return JsonResponse({"error": message, "code": "permission_denied"}, status=403)
    # text/plain: the message may echo request data; never serve it as HTML.
    return HttpResponseForbidden(message, content_type="text/plain; charset=utf-8")


def run_http_turn(
    request: Any,
    body: Any,
    *,
    kind: str,
    source: str,
    transport: str = "http",
    view: Any = None,
    view_class: Optional[type] = None,
    event: Optional[str] = None,
    params: Optional[Mapping[str, Any]] = None,
    json_body: bool,
) -> Any:
    """Run a sync HTTP handler ``body()`` as one turn; return its response.

    Called from sync view code. The chain runs on the event loop
    (``async_to_sync``) and the body returns to this thread
    (``sync_to_async``, thread-sensitive), so context a middleware sets reaches
    it. A refusal becomes the HTTP answer (:func:`http_refusal_response`; a
    page ``deny`` raises ``PermissionDenied`` so the project's 403 handler
    renders it). ``PermissionDenied`` the body raises propagates as before.
    Callers skip this entirely when no app middleware is configured
    (``djust.turns.app_chain_configured``).
    """
    from asgiref.sync import async_to_sync, sync_to_async
    from django.core.exceptions import PermissionDenied

    from .turns import run_turn

    turn = Turn(
        kind,
        transport,
        source=source,
        view=view,
        view_class=view_class,
        event=event,
        params=params,
        request=request,
    )
    response: list = []
    denied: list = []

    def call_body() -> Any:
        try:
            return body()
        except PermissionDenied as exc:
            denied.append(exc)
            return None

    async def run_body(_turn: Turn) -> None:
        response.append(await sync_to_async(call_body, thread_sensitive=True)())

    refusal = async_to_sync(run_turn)(turn, run_body)
    if denied:
        raise denied[0]
    if refusal is not None:
        if refusal.kind == "deny" and not json_body:
            raise PermissionDenied(refusal.message or "")
        return http_refusal_response(turn, refusal, json_body=json_body)
    return response[0] if response else None


async def run_async_http_turn(
    request: Any,
    body: Any,
    *,
    kind: str,
    source: str,
    view: Any = None,
    json_body: bool,
) -> Any:
    """:func:`run_http_turn` for async view code (``aget``): ``await body()``."""
    from django.core.exceptions import PermissionDenied

    from .turns import run_turn

    turn = Turn(kind, "http", source=source, view=view, request=request)
    response: list = []
    denied: list = []

    async def run_body(_turn: Turn) -> None:
        try:
            response.append(await body())
        except PermissionDenied as exc:
            denied.append(exc)

    refusal = await run_turn(turn, run_body)
    if denied:
        raise denied[0]
    if refusal is not None:
        if refusal.kind == "deny" and not json_body:
            raise PermissionDenied(refusal.message or "")
        return http_refusal_response(turn, refusal, json_body=json_body)
    return response[0] if response else None
