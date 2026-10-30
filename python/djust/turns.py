"""Turn middleware: one async chain around every LiveView turn (ADR-044).

A *turn* is one unit of work in which djust runs view code (mount, handler,
hook, render) or delivers view data to a client: a mount or reconnect, a client
event, a URL change, a server push / tick / ``db_notify`` / presence frame, a
background (``start_async`` / ``@background``) start or result, and a recovery
render. Every turn, on every transport (WebSocket, SSE, HTTP, the auto-exposed
API), runs this chain::

    error_boundary            (built-in, always)
      reauth                  (built-in; active when reauth_on_event is on)
        LIVEVIEW_MIDDLEWARE[0] ... [n]
          the turn (check_view_auth, on_mount, permissions, view code, render)

``LIVEVIEW_MIDDLEWARE`` lists dotted paths to ``async def mw(turn, next)``
callables, Django-middleware shaped: return ``await next(turn)`` to run the
rest of the turn, or return a :class:`Refusal` (``Refuse.deny()``,
``Refuse.login()``, ...) to stop it. Raising ``PermissionDenied`` is the same
as returning ``Refuse.deny()``.

**This is hot-path code.** It runs for every event, tick and push. Keep
middleware cheap; cache anything that does I/O. A server turn may run while
the session's render lock is held.

Context variables a middleware sets before ``await next(turn)`` are visible to
the handler, to sync code djust runs through ``sync_to_async``, and to every
task the turn starts (``start_async``, ``@background``, the tick task a mount
starts). Each background start and result is itself a turn, so the chain runs
again there.
"""

from __future__ import annotations

import contextvars
import logging
import time
import weakref
from typing import Any, Awaitable, Callable, Dict, Mapping, Optional, Tuple

from .observability.timings import record_turn_timing

logger = logging.getLogger(__name__)

__all__ = [
    "Turn",
    "Refusal",
    "Refuse",
    "TurnMiddleware",
    "current_turn",
    "run_turn",
    "app_chain_configured",
    "TURN_KINDS",
    "TURN_TRANSPORTS",
]

#: Every value ``Turn.kind`` takes.
TURN_KINDS = frozenset({"mount", "reconnect", "event", "params", "push", "async", "render"})
#: Every value ``Turn.transport`` takes.
TURN_TRANSPORTS = frozenset({"ws", "sse", "http", "api"})

#: Turns a client asked for over a live connection (re-checked every turn).
_CLIENT_LIVE_KINDS = frozenset({"event", "params", "render"})
#: Turns the server originated (re-checked at most every interval).
_SERVER_KINDS = frozenset({"push", "async"})

#: Transports that keep a connection open (HTTP / API authenticate every request).
_LIVE_TRANSPORTS = frozenset({"ws", "sse"})

_EMPTY: Mapping[str, Any] = {}


class Refusal:
    """A middleware's answer that stops the turn.

    Build one with :class:`Refuse`. ``close`` overrides whether a socket turn
    closes the connection (``None`` = the default for the refusal kind and turn,
    see ADR-044 D5). ``sent`` is set once the transport has answered.
    """

    __slots__ = ("kind", "message", "url", "level", "close", "sent")

    def __init__(
        self,
        kind: str,
        *,
        message: Optional[str] = None,
        url: Optional[str] = None,
        level: str = "error",
        close: Optional[bool] = None,
        sent: bool = False,
    ) -> None:
        self.kind = kind
        self.message = message
        self.url = url
        self.level = level
        self.close = close
        self.sent = sent

    def __repr__(self) -> str:
        return "Refusal(%r)" % (self.kind,)


class Refuse:
    """Constructors for :class:`Refusal` (``return Refuse.deny()``)."""

    @staticmethod
    def deny(message: Optional[str] = None, *, close: Optional[bool] = None) -> Refusal:
        """Permission denied: 403 over HTTP, a ``permission_denied`` frame on a socket."""
        return Refusal("deny", message=message, close=close)

    @staticmethod
    def login(next_url: Optional[str] = None, *, close: Optional[bool] = None) -> Refusal:
        """Send the user to ``settings.LOGIN_URL`` (with ``?next=``)."""
        return Refusal("login", url=next_url, close=close)

    @staticmethod
    def redirect(url: str, *, close: Optional[bool] = None) -> Refusal:
        """Navigate to ``url`` (must be a same-site URL; others fall back to ``/``)."""
        return Refusal("redirect", url=url, close=close)

    @staticmethod
    def flash(message: str, level: str = "error", *, close: Optional[bool] = None) -> Refusal:
        """Show ``message`` as a flash; the socket stays open."""
        return Refusal("flash", message=message, level=level, close=close)

    @staticmethod
    def rate_limited(message: Optional[str] = None) -> Refusal:
        """Too many turns: a ``rate_limit_exceeded`` frame, or 429 over HTTP."""
        return Refusal("rate_limited", message=message)


class Turn:
    """One LiveView turn, as middleware sees it (ADR-044 D2).

    Attributes:
        kind: one of :data:`TURN_KINDS`.
        source: finer detail, e.g. ``"page"``, ``"batch"``, ``"tick"``, ``"result"``.
        transport: one of :data:`TURN_TRANSPORTS`.
        view_class: the LiveView class (``None`` only when djust could not resolve it).
        view: the view instance; ``None`` on a mount until djust creates it.
        event: the handler name (``event``) or task name (``async``).
        params: read-only mapping of the frame's params.
        request: the best ``HttpRequest`` djust has for the turn, or ``None``.
        scope: the ASGI scope (WebSocket), or ``None``.
        session_id: the djust session id, when there is one.
        state: scratch space middleware may use to pass data inward.
        started: ``time.perf_counter()`` when the turn started.
    """

    __slots__ = (
        "kind",
        "source",
        "transport",
        "view_class",
        "view",
        "event",
        "params",
        "request",
        "scope",
        "session_id",
        "state",
        "started",
        "_port",
        "_body",
        "_open",
        "_ref",
        "_locked",
        "__weakref__",
    )

    def __init__(
        self,
        kind: str,
        transport: str,
        *,
        source: str = "",
        view_class: Optional[type] = None,
        view: Any = None,
        event: Optional[str] = None,
        params: Optional[Mapping[str, Any]] = None,
        request: Any = None,
        scope: Optional[Mapping[str, Any]] = None,
        session_id: Optional[str] = None,
        port: Any = None,
        ref: Any = None,
    ) -> None:
        self.kind = kind
        self.source = source
        self.transport = transport
        if view_class is None and view is not None:
            view_class = type(view)
        self.view_class = view_class
        self.view = view
        self.event = event
        self.params = params if params is not None else _EMPTY
        self.request = request
        self.scope = scope
        self.session_id = session_id
        self.state: Dict[str, Any] = {}
        self.started = time.perf_counter()
        self._port = port
        self._body: Optional[Callable[["Turn"], Awaitable[Any]]] = None
        self._open = False
        self._ref = ref
        # Set by a transport whose turn body runs under the session render lock.
        self._locked = False

    @property
    def user(self) -> Any:
        """The user djust has for this turn (request first, then the ASGI scope)."""
        request = self._effective_request()
        user = getattr(request, "user", None) if request is not None else None
        if user is None and self.scope is not None:
            user = self.scope.get("user")
        return user

    @property
    def session(self) -> Any:
        """The Django session for this turn (request first, then the ASGI scope)."""
        request = self._effective_request()
        session = getattr(request, "session", None) if request is not None else None
        if session is None and self.scope is not None:
            session = self.scope.get("session")
        return session

    def _effective_request(self) -> Any:
        if self.request is not None:
            return self.request
        view = self.view
        return getattr(view, "request", None) if view is not None else None

    def __repr__(self) -> str:
        name = self.view_class.__name__ if self.view_class is not None else None
        return "Turn(%s/%s %s %s)" % (self.kind, self.source, self.transport, name)


TurnMiddleware = Callable[[Turn, Callable[[Turn], Awaitable[Optional[Refusal]]]], Awaitable[Any]]

#: A weak reference: a task or thread a turn starts keeps a copy of the
#: context, and the turn (with its view and connection) must not live on in it
#: after the turn ends (#3116).
_current_turn: contextvars.ContextVar[Optional["weakref.ref[Turn]"]] = contextvars.ContextVar(
    "djust_current_turn", default=None
)


def current_turn() -> Optional[Turn]:
    """The turn running now, or ``None``.

    A task started by a turn inherits the turn's context; once that turn has
    ended this returns ``None`` there until the task's own turn starts.
    """
    ref = _current_turn.get()
    turn = ref() if ref is not None else None
    if turn is not None and turn._open:
        return turn
    return None


# --------------------------------------------------------------------------- #
# Built-in middleware (always on the chain, outermost; ADR-044 D7)
# --------------------------------------------------------------------------- #


async def error_boundary(turn: Turn, next: Callable[[Turn], Awaitable[Any]]) -> Any:
    """Map what escapes a turn: ``PermissionDenied`` -> deny; errors -> the port.

    The port (the transport adapter) answers a socket client turn with the
    value-free error envelope, logs a server turn's error value-free, or
    re-raises (HTTP, and legacy views while diagnostics are allowed).
    """
    from django.core.exceptions import PermissionDenied

    try:
        return await next(turn)
    except PermissionDenied:
        return Refuse.deny()
    except Exception as exc:
        port = turn._port
        if port is None or not await port.turn_failed(turn, exc):
            raise
        return None


async def reauth(turn: Turn, next: Callable[[Turn], Awaitable[Any]]) -> Any:
    """``reauth_on_event``: re-check the principal of a live connection.

    Client turns re-check every turn; server turns at most every
    ``reauth_server_turn_interval`` seconds per connection. Mounts and HTTP
    turns authenticate fresh anyway. Only legacy-exposure views that set
    ``login_required`` / ``permission_required`` are re-checked (explicit
    views carry their own always-on authority, ADR-038). On a failed check the
    transport has already answered (navigate to login / ``permission_denied``)
    and closed the connection.
    """
    kind = turn.kind
    view = turn.view
    if (
        view is not None
        and (kind in _CLIENT_LIVE_KINDS or kind in _SERVER_KINDS)
        and turn.transport in _LIVE_TRANSPORTS
        and turn._port is not None
        and (getattr(view, "login_required", None) or getattr(view, "permission_required", None))
    ):
        from ._exposure import uses_legacy_exposure
        from .config import config as djust_config

        if djust_config.get("reauth_on_event") and uses_legacy_exposure(view):
            if not await turn._port.turn_reauth(turn, server=kind in _SERVER_KINDS):
                return Refusal("login", sent=True)
    return await next(turn)


_BUILTINS: Tuple[TurnMiddleware, ...] = (error_boundary, reauth)


# --------------------------------------------------------------------------- #
# Chain construction
# --------------------------------------------------------------------------- #


class _Chain:
    __slots__ = ("entry", "app_count")

    def __init__(self, entry: Callable[[Turn], Awaitable[Any]], app_count: int) -> None:
        self.entry = entry
        self.app_count = app_count


_chain: Optional[_Chain] = None


async def _terminal(turn: Turn) -> Any:
    body = turn._body
    if body is None:  # pragma: no cover - run_turn always sets it
        return None
    return await body(turn)


def _link(mw: TurnMiddleware, inner: Callable[[Turn], Awaitable[Any]]) -> Callable[[Turn], Any]:
    def call(turn: Turn) -> Any:
        return mw(turn, inner)

    return call


def resolve_middleware(paths: Any) -> Tuple[TurnMiddleware, ...]:
    """Import ``LIVEVIEW_MIDDLEWARE`` entries; raise ``ImproperlyConfigured`` on a bad one."""
    from django.core.exceptions import ImproperlyConfigured
    from django.utils.module_loading import import_string

    from ._view_hooks import is_async_hook

    if paths is None:
        return ()
    if isinstance(paths, str) or not isinstance(paths, (list, tuple)):
        raise ImproperlyConfigured("LIVEVIEW_MIDDLEWARE must be a list of dotted paths.")
    resolved = []
    for path in paths:
        if not isinstance(path, str):
            raise ImproperlyConfigured(
                "LIVEVIEW_MIDDLEWARE entries must be dotted-path strings, got %r." % (path,)
            )
        try:
            mw = import_string(path)
        except ImportError as exc:
            raise ImproperlyConfigured(
                "LIVEVIEW_MIDDLEWARE entry %r could not be imported: %s" % (path, exc)
            ) from exc
        if not callable(mw) or not is_async_hook(mw):
            raise ImproperlyConfigured(
                "LIVEVIEW_MIDDLEWARE entry %r must be an `async def mw(turn, next)` "
                "callable; sync middleware is not supported (ADR-044 D1)." % (path,)
            )
        resolved.append(mw)
    return tuple(resolved)


def _build_chain() -> _Chain:
    from django.conf import settings

    app = resolve_middleware(getattr(settings, "LIVEVIEW_MIDDLEWARE", None))
    entry: Callable[[Turn], Any] = _terminal
    for mw in reversed(_BUILTINS + app):
        entry = _link(mw, entry)
    return _Chain(entry, len(app))


def _get_chain() -> _Chain:
    global _chain
    chain = _chain
    if chain is None:
        # Two statements, not ``chain = _chain = ...``: code scanning read the
        # chained store as an unused global (alert #3087), but ``_chain`` is the
        # module-level cache every later turn reads above.
        _chain = _build_chain()
        chain = _chain
    return chain


def reset_chain() -> None:
    """Drop the compiled chain; the next turn rebuilds it from settings."""
    global _chain
    _chain = None


def app_chain_configured() -> bool:
    """Whether ``LIVEVIEW_MIDDLEWARE`` lists any middleware.

    HTTP and API turns skip the chain when it does not: the built-ins do
    nothing there (ADR-044 D8).
    """
    return _get_chain().app_count > 0


# --------------------------------------------------------------------------- #
# Running a turn
# --------------------------------------------------------------------------- #


async def run_turn(turn: Turn, body: Callable[[Turn], Awaitable[Any]]) -> Optional[Refusal]:
    """Run ``body`` as ``turn`` through the chain.

    Returns ``None`` when the turn ran (or an error was answered), or the
    :class:`Refusal` that stopped it. A refusal is answered through the turn's
    port before this returns (``refusal.sent`` is then True).
    """
    turn._body = body
    turn._open = True
    token = _current_turn.set(weakref.ref(turn))
    try:
        result = await _get_chain().entry(turn)
    finally:
        turn._open = False
        _current_turn.reset(token)
        _record_timing(turn)
    if isinstance(result, Refusal):
        if not result.sent:
            logger.debug("Turn %r refused: %s", turn, result.kind)
            port = turn._port
            if port is not None:
                await port.turn_refused(turn, result)
            result.sent = True
        return result
    return None


def _record_timing(turn: Turn) -> None:
    """Per-turn timing for the debug panel / observability (ADR-044 D9)."""
    try:
        record_turn_timing(
            turn.kind,
            turn.source,
            turn.transport,
            turn.view_class.__name__ if turn.view_class is not None else "",
            (time.perf_counter() - turn.started) * 1000.0,
        )
    except Exception:  # noqa: BLE001 - timing must never break a turn
        logger.debug("turn timing could not be recorded", exc_info=True)


def _reset_on_setting_change(*, setting: str, **kwargs: Any) -> None:
    if setting == "LIVEVIEW_MIDDLEWARE":
        reset_chain()


try:
    from django.core.signals import setting_changed

    setting_changed.connect(_reset_on_setting_change, dispatch_uid="djust.turns")
except ImportError:  # pragma: no cover - Django is a hard dependency
    logger.debug("[djust] django unavailable; turn chain will not follow override_settings")
