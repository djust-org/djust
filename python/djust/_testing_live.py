"""Live sessions and real transports for tests (published from ``djust.testing``).

An explicit view (``exposure_policy = "explicit"``, the 2.0 default) binds its
state to a server session when it mounts and reads that session, and its
user, again on every turn (ADR-038). A test therefore needs what a browser
has: a saved server session, a user logged into it, the page GET that mints
the tab id, and a transport connected with them. A stub user, a bare
consumer or a ``RequestFactory`` request without a saved session is refused
("State binding requires bounded nonempty identifiers") or denied every event.

Two layers:

* :func:`live_session` and :func:`with_live_session` give a hand-built
  ``WebsocketCommunicator``, ASGI scope or request a saved session and user.
* :func:`open_live_page` does the page GET with a real ``django.test.Client``
  and returns a :class:`LivePage`, from which a test sends the HTTP fallback
  POST (:meth:`LivePage.post_event`), connects a socket
  (:meth:`LivePage.connect`) or opens an SSE stream
  (:meth:`LivePage.connect_sse`), each carrying the page's session, user, tab
  and request headers, as the client does.

Database: sessions are database rows with the default ``SESSION_ENGINE``. A
socket or SSE test runs the view on other threads, so under pytest-django it
needs ``@pytest.mark.django_db(transaction=True)``, and under Django's own
runner a ``TransactionTestCase``.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from html import unescape
from typing import Any, Dict, List, Optional, Tuple, TypeVar
from urllib.parse import parse_qs, urlsplit

__all__ = [
    "LivePage",
    "LiveSSE",
    "LiveSocket",
    "live_session",
    "open_live_page",
    "with_live_session",
]

T = TypeVar("T")


class _Unset:
    def __repr__(self) -> str:
        return "<unset>"


#: The default of ``with_live_session(user=...)``: keep the target's own user.
_UNSET: Any = _Unset()

#: The tab id the page GET puts in its document (``_automatic_state.TAB_META``).
_TAB_META = re.compile(r'<meta name="djust-tab" content="([^"]+)">')
#: The view path the page's root carries (``mixins/template.py``).
_DJ_VIEW = re.compile(r'\sdj-view="([^"]+)"')
#: The frames that answer a mount.
_MOUNT_ANSWERS = ("mount", "error", "navigate", "redirect")
_DEFAULT_TIMEOUT = 10.0


# --------------------------------------------------------------------------- #
# Sessions
# --------------------------------------------------------------------------- #


def _default_store() -> Any:
    """The configured ``SESSION_ENGINE``'s store when it can hold server
    state, else Django's database store."""
    from django.conf import settings

    from ._exposure_sessions import session_engine_store, session_store_supported

    store = session_engine_store(getattr(settings, "SESSION_ENGINE", None))
    if session_store_supported(store):
        return store
    from django.contrib.sessions.backends.db import SessionStore

    return SessionStore


def _is_saved_user(user: Any) -> bool:
    from django.contrib.auth.base_user import AbstractBaseUser

    return isinstance(user, AbstractBaseUser) and user.pk is not None


def live_session(user: Any = None, *, store: Any = None) -> Any:
    """A new saved server session, logged in as ``user`` when it is a saved user.

    The keys are the ones ``django.contrib.auth.login`` writes, so the view's
    per-turn re-read of the session finds the user that mounted. ``user=None``
    (or an unsaved or anonymous user) gives an anonymous session.

    Args:
        user: A saved ``AUTH_USER_MODEL`` instance, or ``None``.
        store: The ``SessionStore`` class to use. Defaults to the configured
            ``SESSION_ENGINE`` when it is a server-side store, else Django's
            database store.

    Returns:
        The saved session (it has a ``session_key``). Needs database access.
    """
    from django.contrib.auth import BACKEND_SESSION_KEY, HASH_SESSION_KEY, SESSION_KEY

    session = (store or _default_store())()
    if _is_saved_user(user):
        session[SESSION_KEY] = user._meta.pk.value_to_string(user)
        session[BACKEND_SESSION_KEY] = "django.contrib.auth.backends.ModelBackend"
        session[HASH_SESSION_KEY] = user.get_session_auth_hash()
    session.save()
    return session


def _live_session_from_any_context(user: Any) -> Any:
    """``live_session(user)``, also from a coroutine (a socket test's setup).

    The ORM refuses to run on an event loop, so there the session is saved
    from a worker thread.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return live_session(user)
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(live_session, user).result()


def _has_live_session(session: Any) -> bool:
    from ._exposure_sessions import session_store_supported

    return session_store_supported(type(session)) and bool(getattr(session, "session_key", None))


def with_live_session(target: T, user: Any = _UNSET) -> T:
    """Give ``target`` a saved server session and a user, and return ``target``.

    ``target`` is a ``channels.testing.WebsocketCommunicator`` (its
    ``scope``), an ASGI scope ``dict`` or an ``HttpRequest`` (for example from
    ``RequestFactory``). A saved server session it already has is kept;
    otherwise a new one (:func:`live_session`) is saved, logged in as ``user``.
    ``user`` replaces the target's user when given; when not given an existing
    user is kept, and ``AnonymousUser`` is supplied when there is none.

    Example::

        communicator = with_live_session(
            WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/live/"), user
        )
        connected, _ = await communicator.connect()
    """
    from django.contrib.auth.models import AnonymousUser

    scope = target if isinstance(target, dict) else getattr(target, "scope", None)
    if scope is not None:
        chosen = scope.get("user") if user is _UNSET else user
        if chosen is None:
            chosen = AnonymousUser()
        if not _has_live_session(scope.get("session")):
            scope["session"] = _live_session_from_any_context(chosen)
        scope["user"] = chosen
        return target
    chosen = getattr(target, "user", None) if user is _UNSET else user
    if chosen is None:
        chosen = AnonymousUser()
    if not _has_live_session(getattr(target, "session", None)):
        target.session = _live_session_from_any_context(chosen)  # type: ignore[attr-defined]
    target.user = chosen  # type: ignore[attr-defined]
    return target


# --------------------------------------------------------------------------- #
# The page and its transports
# --------------------------------------------------------------------------- #


def _meta_to_headers(extra: Dict[str, Any]) -> List[Tuple[bytes, bytes]]:
    """``HTTP_X_TENANT_ID`` style request extras as ASGI header pairs."""
    headers = []
    for key, value in extra.items():
        if key.startswith("HTTP_") and key not in ("HTTP_HOST", "HTTP_ORIGIN"):
            name = key[5:].lower().replace("_", "-")
            headers.append((name.encode("latin-1"), str(value).encode("latin-1")))
    return headers


def _query_params(url: str) -> Dict[str, Any]:
    """The page URL's query string as the client sends it: a repeated key is
    the list of its values (#4125)."""
    return {
        key: values if len(values) > 1 else values[0]
        for key, values in parse_qs(urlsplit(url).query, keep_blank_values=True).items()
    }


def _page_session_and_user(page: "LivePage") -> Tuple[Any, Any]:
    """The page's session and whoever it is logged in as, resolved as
    ``AuthenticationMiddleware`` does (``AnonymousUser`` when no one), so a
    second tab of a logged-in client is logged in too. Sync (database)."""
    from types import SimpleNamespace

    from django.contrib.auth import get_user

    session = page.session
    if not _has_live_session(session):
        raise AssertionError(
            "the page GET left no saved server session (is SessionMiddleware "
            "installed and SESSION_ENGINE a server-side store?)"
        )
    return session, get_user(SimpleNamespace(session=session))


class LivePage:
    """A page loaded with a real ``django.test.Client``; see :func:`open_live_page`.

    Attributes:
        url: The page URL that was requested.
        client: The ``django.test.Client`` holding the session cookie. Load
            another tab of the same browser with
            ``open_live_page(url, client=page.client)``.
        response: The page GET's response.
        html: The page GET's decoded body.
        tab: The tab id the page was given (``<meta name="djust-tab">``), or
            ``None`` when the page carries none.
        view_path: The view path on the page's root (``dj-view``), or ``None``.
        user: The user passed to :func:`open_live_page`, or ``None``. The
            transports use whoever the page's session is logged in as.
        extra: The request extras (``HTTP_HOST``, ``HTTP_X_TENANT_ID``, ...) the
            GET was made with; every transport opened from the page sends them.
    """

    def __init__(
        self,
        url: str,
        client: Any,
        response: Any,
        *,
        user: Any = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.url = url
        self.client = client
        self.response = response
        self.html: str = response.content.decode(response.charset or "utf-8")
        tab = _TAB_META.search(self.html)
        self.tab: Optional[str] = tab.group(1) if tab else None
        view = _DJ_VIEW.search(self.html)
        self.view_path: Optional[str] = unescape(view.group(1)) if view else None
        self.user = user
        self.extra: Dict[str, Any] = dict(extra or {})

    def __repr__(self) -> str:
        return "<LivePage %s tab=%s view=%s>" % (self.url, self.tab, self.view_path)

    @property
    def path(self) -> str:
        """The page URL's path, which the client sends as the mount's ``url``."""
        return urlsplit(self.url).path

    @property
    def session(self) -> Any:
        """The page's server session, read from the client's session cookie."""
        return self.client.session

    @property
    def host(self) -> str:
        """The host the page was requested on (``HTTP_HOST``, default ``testserver``)."""
        return str(self.extra.get("HTTP_HOST", "testserver"))

    @property
    def origin(self) -> str:
        """The page's origin, sent as ``Origin`` by the socket and SSE requests."""
        return "http://%s" % self.host

    def _view(self, view_path: Optional[str]) -> str:
        view_path = view_path or self.view_path
        if not view_path:
            raise AssertionError("the page has no dj-view; pass view_path")
        return view_path

    def mount_frame(
        self, view_path: Optional[str] = None, *, params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """The socket mount frame the client sends for this page.

        Args:
            view_path: The view to mount; defaults to the page's ``dj-view``.
            params: The mount parameters; defaults to the page URL's query.
        """
        return {
            "type": "mount",
            "view": self._view(view_path),
            "url": self.path,
            "params": _query_params(self.url) if params is None else dict(params),
            "tab": self.tab,
            "has_prerendered": True,
        }

    def post_event(self, event: str, params: Optional[Dict[str, Any]] = None, **extra: Any) -> Any:
        """Send ``event`` over the HTTP fallback, as the client does without a socket.

        A JSON POST to the page URL with ``X-Djust-Event`` and the page's tab
        (``X-Djust-Tab``), on the page's session and request extras. Returns
        the response; its JSON body is the fallback answer (``html`` or
        ``patches``). Sync: call it with ``sync_to_async`` from an async test.

        Args:
            event: The handler name.
            params: The handler's parameters (the JSON body).
            **extra: More request extras, for example ``HTTP_X_DJUST_RENDER_DIGEST``.
        """
        headers: Dict[str, Any] = {**self.extra, "HTTP_X_DJUST_EVENT": event}
        if self.tab:
            headers["HTTP_X_DJUST_TAB"] = self.tab
        from django.conf import settings

        # The client sends the CSRF cookie's token back, as the page's script does.
        csrf = self.client.cookies.get(settings.CSRF_COOKIE_NAME)
        if csrf is not None:
            headers["HTTP_X_CSRFTOKEN"] = csrf.value
        headers.update(extra)
        return self.client.post(
            self.url, data=json.dumps(params or {}), content_type="application/json", **headers
        )

    async def connect(
        self,
        view_path: Optional[str] = None,
        *,
        params: Optional[Dict[str, Any]] = None,
        application: Any = None,
        path: str = "/ws/live/",
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> "LiveSocket":
        """Connect a ``WebsocketCommunicator`` as the page's client and mount the view.

        The socket carries the page's cookies (session and CSRF), ``Origin``
        and request extras. With the default bare consumer its scope is given
        the page's session and user, as ``AuthMiddlewareStack`` would; a
        project's own ``application`` resolves both from the cookie through its
        own middleware. The mount frame is :meth:`mount_frame`.

        Args:
            view_path: The view to mount; defaults to the page's ``dj-view``.
            params: The mount parameters; defaults to the page URL's query.
            application: The ASGI application; defaults to
                ``LiveViewConsumer.as_asgi()``. A project's own ``asgi.application``
                works too (``path`` must then match its WebSocket route).
            path: The WebSocket path.
            timeout: Seconds to wait for the connection and the mount answer.

        Returns:
            A connected :class:`LiveSocket`; its ``mount`` is the mount answer
            (``"mount"`` on success, ``"error"`` / ``"navigate"`` otherwise).
        """
        from asgiref.sync import sync_to_async
        from channels.testing import WebsocketCommunicator

        bare = application is None
        if bare:
            from .websocket import LiveViewConsumer

            application = LiveViewConsumer.as_asgi()
        session, user = await sync_to_async(_page_session_and_user)(self)
        # The browser's cookies (session and CSRF), for an application whose
        # own middleware reads them (``AuthMiddlewareStack``).
        cookie = "; ".join(
            "%s=%s" % (name, morsel.value) for name, morsel in self.client.cookies.items()
        )
        headers = [
            (b"host", self.host.encode("latin-1")),
            (b"origin", self.origin.encode("latin-1")),
            (b"cookie", cookie.encode("latin-1")),
            *_meta_to_headers(self.extra),
        ]
        communicator = WebsocketCommunicator(application, path, headers=headers)
        if bare:
            # What AuthMiddlewareStack would have put in the scope. A project's
            # own application resolves both from the cookie itself.
            communicator.scope["session"] = session
            communicator.scope["user"] = user
        connected, code = await communicator.connect(timeout=timeout)
        if not connected:
            raise AssertionError("the socket was refused (close code %r)" % (code,))
        socket = LiveSocket(communicator, timeout=timeout)
        await communicator.send_json_to(self.mount_frame(view_path, params=params))
        socket.mount = await socket.receive(_MOUNT_ANSWERS)
        return socket

    async def connect_sse(
        self,
        view_path: Optional[str] = None,
        *,
        params: Optional[Dict[str, Any]] = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> "LiveSSE":
        """Open the SSE stream as the page's client; the stream GET mounts the view.

        The stream request goes to djust's SSE stream view with the page's
        session, user, tab, ``Origin`` and request extras; the project need not
        route the SSE URLs.

        Args:
            view_path: The view to mount; defaults to the page's ``dj-view``.
            params: The mount parameters; defaults to the page URL's query.
            timeout: Seconds to wait for each frame.

        Returns:
            A :class:`LiveSSE`; its ``mount`` is the mount answer.
        """
        from asgiref.sync import sync_to_async
        from django.test import RequestFactory

        from .sse import DjustSSEStreamView

        session_id = str(uuid.uuid4())
        query: Dict[str, Any] = _query_params(self.url) if params is None else dict(params)
        query.update({"view": self._view(view_path), "_djust_url": self.path})
        if self.tab:
            query["_djust_tab"] = self.tab
        # A literal path: the stream view takes the session id as a keyword and
        # the page URL from ``_djust_url``, so the request path routes nothing
        # (and no non-literal URL reaches RequestFactory: the mount-URL pin).
        request = RequestFactory().get(
            "/djust/sse/",
            query,
            **{**self.extra, "HTTP_HOST": self.host, "HTTP_ORIGIN": self.origin},
        )
        request.session, request.user = await sync_to_async(_page_session_and_user)(self)
        response = await DjustSSEStreamView.as_view()(request, session_id=session_id)
        if getattr(response, "status_code", 200) != 200 or not getattr(
            response, "streaming", False
        ):
            raise AssertionError(
                "the SSE stream GET was refused: %s %r"
                % (getattr(response, "status_code", "?"), getattr(response, "content", b"")[:300])
            )
        stream = LiveSSE(self, session_id, response, timeout=timeout)
        stream.mount = await stream.receive(_MOUNT_ANSWERS)
        return stream


def open_live_page(url: str, *, user: Any = None, client: Any = None, **extra: Any) -> LivePage:
    """Load ``url`` as a browser does and return the :class:`LivePage`.

    A GET with a ``django.test.Client`` through the project's own middleware
    and URLconf: the page view saves a server session, mounts the view and
    gives the page a tab id. Each call is a new page load, so a new tab; pass
    the same ``client`` to load a second tab in the same browser session.
    Sync: from an async test call it with ``sync_to_async``.

    Args:
        url: The page URL (path and query).
        user: A saved user to log in first (``client.force_login``), or ``None``
            for an anonymous page.
        client: The ``django.test.Client`` to use; a new one by default.
        **extra: Request extras sent with the GET and with every transport
            opened from the page: ``HTTP_HOST`` for a subdomain tenant,
            ``HTTP_X_TENANT_ID`` for a header tenant, and so on.

    Raises:
        AssertionError: The GET did not answer 200.
    """
    if client is None:
        from django.test import Client

        client = Client()
    if user is not None:
        client.force_login(user)
    response = client.get(url, **extra)
    if response.status_code != 200:
        raise AssertionError("GET %s answered %s" % (url, response.status_code))
    return LivePage(url, client, response, user=user, extra=extra)


class _Transport:
    """Frame bookkeeping shared by :class:`LiveSocket` and :class:`LiveSSE`."""

    def __init__(self, timeout: float) -> None:
        self.timeout = timeout
        #: The mount answer (``{"type": "mount", ...}`` on success).
        self.mount: Dict[str, Any] = {}
        #: Every frame received so far, in order.
        self.frames: List[Dict[str, Any]] = []
        self._ref = 0
        self._unread: List[Dict[str, Any]] = []

    async def _next_frame(self, timeout: float) -> Dict[str, Any]:
        raise NotImplementedError

    async def _send(self, frame: Dict[str, Any]) -> None:
        raise NotImplementedError

    async def receive(
        self, types: Optional[Tuple[str, ...]] = None, *, timeout: Optional[float] = None
    ) -> Dict[str, Any]:
        """The next frame (of one of ``types`` when given; others are skipped,
        and kept in :attr:`frames`).

        Raises:
            asyncio.TimeoutError: No such frame arrived within ``timeout``.
        """
        wait = self.timeout if timeout is None else timeout
        loop = asyncio.get_running_loop()
        deadline = loop.time() + wait
        while True:
            if self._unread:
                frame = self._unread.pop(0)
            else:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise asyncio.TimeoutError("no frame of %r within %ss" % (types, wait))
                frame = await self._next_frame(remaining)
            if types is None or frame.get("type") in types:
                return frame

    async def send_event(
        self,
        event: str,
        params: Optional[Dict[str, Any]] = None,
        *,
        timeout: Optional[float] = None,
        **fields: Any,
    ) -> Dict[str, Any]:
        """Send ``event`` and return the frame that answers it.

        The event frame carries a ``ref``; the answer is the first frame that
        echoes it (``patch``, ``html_update``, ``noop``, ...) or an ``error``.
        Frames that arrive before it (push events, for example) are in
        :attr:`frames`.

        Args:
            event: The handler name.
            params: The handler's parameters.
            **fields: More event-frame fields, for example ``component_id``.
        """
        self._ref += 1
        ref = self._ref
        await self._send(
            {"type": "event", "event": event, "params": dict(params or {}), "ref": ref, **fields}
        )
        wait = self.timeout if timeout is None else timeout
        loop = asyncio.get_running_loop()
        deadline = loop.time() + wait
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise asyncio.TimeoutError("no answer to %r within %ss" % (event, wait))
            frame = await self._next_frame(remaining)
            if frame.get("ref") == ref or frame.get("type") == "error":
                return frame
            self._unread.append(frame)


class LiveSocket(_Transport):
    """A connected, mounted ``WebsocketCommunicator``; see :meth:`LivePage.connect`.

    Use it as an async context manager, or call :meth:`disconnect`.

    Attributes:
        communicator: The ``channels.testing.WebsocketCommunicator``.
        mount: The mount answer.
        frames: Every frame received, in order.
    """

    def __init__(self, communicator: Any, *, timeout: float = _DEFAULT_TIMEOUT) -> None:
        super().__init__(timeout)
        self.communicator = communicator

    async def _next_frame(self, timeout: float) -> Dict[str, Any]:
        frame: Dict[str, Any] = await self.communicator.receive_json_from(timeout=timeout)
        self.frames.append(frame)
        return frame

    async def _send(self, frame: Dict[str, Any]) -> None:
        await self.communicator.send_json_to(frame)

    async def send(self, frame: Dict[str, Any]) -> None:
        """Send a raw frame (``url_change``, ``upload_register``, ...)."""
        await self._send(frame)

    async def disconnect(self, code: int = 1000) -> None:
        """Close the socket as the browser does; the view is torn down."""
        await self.communicator.disconnect(code=code)

    async def __aenter__(self) -> "LiveSocket":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.disconnect()


class LiveSSE(_Transport):
    """An open SSE stream with a mounted view; see :meth:`LivePage.connect_sse`.

    Events are POSTed to djust's SSE message view as the client does; frames
    are read from the stream response. Use it as an async context manager, or
    call :meth:`close`.

    Attributes:
        session_id: The SSE session id.
        response: The stream GET's ``StreamingHttpResponse``.
        mount: The mount answer.
        frames: Every frame received, in order.
    """

    def __init__(
        self, page: LivePage, session_id: str, response: Any, *, timeout: float = _DEFAULT_TIMEOUT
    ) -> None:
        super().__init__(timeout)
        self.page = page
        self.session_id = session_id
        self.response = response
        # The stream body itself (djust's generator), not Django's re-wrapping
        # ``streaming_content``: closing it must run the stream's own teardown
        # (its ``finally``), as a disconnecting browser does.
        self._stream = response._iterator
        self._closed = False

    @property
    def session(self) -> Any:
        """The live ``djust.sse.SSESession`` (``None`` once it is closed or when the mount failed)."""
        from .sse import _get_session

        return _get_session(self.session_id)

    async def _next_frame(self, timeout: float) -> Dict[str, Any]:
        while True:
            chunk = await asyncio.wait_for(self._stream.__anext__(), timeout)
            if isinstance(chunk, bytes):
                chunk = chunk.decode("utf-8")
            data = [line[6:] for line in chunk.splitlines() if line.startswith("data: ")]
            if not data:
                continue  # a keepalive comment
            frame: Dict[str, Any] = json.loads("\n".join(data))
            if frame.get("type") == "sse_connect":
                continue
            self.frames.append(frame)
            return frame

    async def _send(self, frame: Dict[str, Any]) -> None:
        from asgiref.sync import sync_to_async
        from django.test import RequestFactory

        from .sse import DjustSSEMessageView

        request = RequestFactory().post(
            "/djust/sse/%s/message/" % self.session_id,
            data=json.dumps(frame),
            content_type="application/json",
            **{**self.page.extra, "HTTP_HOST": self.page.host, "HTTP_ORIGIN": self.page.origin},
        )
        request.session, request.user = await sync_to_async(_page_session_and_user)(self.page)
        response = await DjustSSEMessageView.as_view()(request, session_id=self.session_id)
        if response.status_code != 200:
            raise AssertionError(
                "the SSE message POST was refused: %s %r"
                % (response.status_code, response.content[:300])
            )

    async def send(self, frame: Dict[str, Any]) -> None:
        """POST a raw frame (``url_change``, ...) to the SSE message view."""
        await self._send(frame)

    async def close(self) -> None:
        """Close the stream as a browser going away does: the stream's own
        teardown saves the view's state and disposes the session."""
        if self._closed:
            return
        self._closed = True
        from unittest import mock

        from . import sse

        # The closing stream waits a grace period for event POSTs still in
        # flight. Every POST this helper made has been answered, so there is
        # nothing to wait for.
        with mock.patch.object(sse, "_SESSION_LINGER_S", 0):
            await self._stream.aclose()

    async def __aenter__(self) -> "LiveSSE":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()
