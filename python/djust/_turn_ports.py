"""How each transport answers a turn's refusal or error (ADR-044 D5, D7).

``djust.turns.run_turn`` is wire-blind. The *port* a turn carries maps a
:class:`~djust.turns.Refusal` to the transport's established frames / responses:

- :class:`RuntimeTurnPort` — turns the :class:`~djust.runtime.ViewRuntime`
  dispatches (mount, reconnect, event, params, async) over WebSocket and SSE.
- :class:`ConsumerTurnPort` — the WebSocket consumer's own server turns (push,
  tick, ``db_notify``, presence, async results, recovery render).
- :func:`http_refusal_response` — HTTP GET / POST and the auto-exposed API.
"""

from __future__ import annotations

import contextlib
import logging
from typing import Any, Dict, Mapping, Optional

from .turns import Refusal, Turn

logger = logging.getLogger(__name__)

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


def _refusal_frame(turn: Turn, effect: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """One ``render`` frame for a refused turn (#3658): its effect, if any, and,
    for an event, the acknowledgement (``event_name`` and ``ref``)."""
    from ._render_frame import render_frame

    fields: Dict[str, Any] = {"source": "mount" if turn.kind in _MOUNT_KINDS else "refusal"}
    if turn.kind == "event":
        fields["event_name"] = turn.event
        fields.update(_ref_fields(turn))
        fields["source"] = "refusal"
    return render_frame(effects=[effect] if effect else [], **fields)


class RuntimeTurnPort:
    """Port methods for :class:`~djust.runtime.ViewRuntime` (mixed in).

    Uses the runtime's ``transport`` and ``view_instance`` attributes.
    """

    @property
    def turn_transport(self) -> str:
        return getattr(self.transport, "turn_transport", "ws")

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
                # One frame: the navigation and, for an event, its acknowledgement.
                await transport.send(_refusal_frame(turn, {"kind": "navigate", "to": target}))
            elif not is_mount:
                await self._send_turn_ack(turn)
            if is_mount:
                if close:
                    await self._finalize_mount_auth("redirect")  # type: ignore[attr-defined]
                return
        elif kind == "flash":
            await transport.send(
                _refusal_frame(
                    turn,
                    {
                        "kind": "flash",
                        "action": "put",
                        "level": refusal.level,
                        "message": refusal.message or "",
                    },
                )
            )
        elif kind == "rate_limited":
            if turn.kind not in _SERVER_KINDS:
                frame: Dict[str, Any] = {
                    "type": "rate_limit_exceeded",
                    "message": refusal.message or "Too many requests, please slow down",
                }
                # The refused event's ref (#4322), as the global gate sends it
                # (#4309): the client settles that request as rate limited.
                if "ref" in (ref_fields := _ref_fields(turn)):
                    frame["ref"] = ref_fields["ref"]
                await transport.send(frame)
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
        with contextlib.suppress(Exception):
            await self.transport.send(_refusal_frame(turn, None))

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
                await self.send_json(  # type: ignore[attr-defined]
                    _refusal_frame(turn, {"kind": "navigate", "to": _target_for(turn, refusal)})
                )
        elif kind == "flash":
            with contextlib.suppress(Exception):
                await self.send_json(  # type: ignore[attr-defined]
                    _refusal_frame(
                        turn,
                        {
                            "kind": "flash",
                            "action": "put",
                            "level": refusal.level,
                            "message": refusal.message or "",
                        },
                    )
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
