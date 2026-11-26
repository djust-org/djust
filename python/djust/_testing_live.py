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
  (:meth:`LivePage.connect_sse`), each as the browser's client does and each
  through the project's own stack: its middleware and URLconf for HTTP, its
  ``ASGI_APPLICATION`` for the socket. A test written with them fails where
  production would refuse.

Database: sessions are database rows with the default ``SESSION_ENGINE``. A
socket or SSE test runs the view on other threads, so under pytest-django it
needs ``@pytest.mark.django_db(transaction=True)``; under Django's own runner
see the testing guide ("Real sessions and transports").
"""

from __future__ import annotations

import asyncio
import collections
import json
import re
import sys
import uuid
import weakref
from html import unescape
from typing import Any, AsyncIterator, Callable, Dict, List, Optional, Tuple, TypeVar
from urllib.parse import parse_qs, urlsplit

from ._protocol import PROTOCOL_HEADER, PROTOCOL_QUERY_PARAM, PROTOCOL_VERSION

__all__ = [
    "LivePage",
    "LiveSSE",
    "LiveSocket",
    "close_live_pages",
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

#: The 1.x frame names a ``render`` frame's DOM part stands for (#3658).
_DOM_PART_NAMES = {"patches": "patch", "html": "html_update", "embedded": "embedded_update"}


def _frame_is(frame: Dict[str, Any], types: Optional[Tuple[str, ...]]) -> bool:
    """Whether ``frame`` is one of ``types`` (any frame when ``None``).

    A ``render`` frame (#3658) also answers to the 1.x name of its DOM part
    (``patch``, ``html_update``, ``embedded_update``, or ``noop`` for none) and
    to the ``kind`` of any effect it carries (``navigate``, ``push_event``...),
    so a caller can wait for "the patch" or "the navigation" by its old name.
    """
    if types is None or frame.get("type") in types:
        return True
    if frame.get("type") != "render":
        return False
    from ._render_frame import dom_part

    if _DOM_PART_NAMES.get(dom_part(frame), "noop") in types:
        return True
    return any(
        isinstance(effect, dict) and effect.get("kind") in types
        for effect in frame.get("effects") or ()
    )


_DEFAULT_TIMEOUT = 10.0


# --------------------------------------------------------------------------- #
# Sessions
# --------------------------------------------------------------------------- #


def _default_store() -> Any:
    """The configured ``SESSION_ENGINE``'s store, resolved the way the
    framework resolves it (``djust.utils.get_session_store_class``).

    An engine that cannot hold server state (the signed-cookies store, or one
    that does not import) is refused here, as production refuses an explicit
    view on it, instead of quietly saving the session somewhere else.
    """
    from django.conf import settings
    from django.core.exceptions import ImproperlyConfigured

    from ._exposure_sessions import SUPPORTED_SESSION_ENGINES, session_store_supported
    from .utils import get_session_store_class

    store = get_session_store_class()
    if session_store_supported(store):
        return store
    raise ImproperlyConfigured(
        "djust.testing's live sessions need a server-side SESSION_ENGINE, as an "
        "explicit view does; SESSION_ENGINE is %r. Set it to one of %s (or a "
        "subclass of its store), or pass store= to live_session()."
        % (getattr(settings, "SESSION_ENGINE", None), ", ".join(SUPPORTED_SESSION_ENGINES))
    )


def _is_saved_user(user: Any) -> bool:
    from django.contrib.auth.base_user import AbstractBaseUser

    return isinstance(user, AbstractBaseUser) and user.pk is not None


def _login_backend() -> str:
    """The backend ``Client.force_login`` records: the first entry of
    ``AUTHENTICATION_BACKENDS`` that can load a user (``get_user``)."""
    from django.conf import settings
    from django.contrib.auth import load_backend

    for path in settings.AUTHENTICATION_BACKENDS:
        if hasattr(load_backend(path), "get_user"):
            return str(path)
    raise ValueError("no AUTHENTICATION_BACKENDS entry can load a user (get_user)")


def live_session(user: Any = None, *, store: Any = None, backend: Optional[str] = None) -> Any:
    """A new saved server session, logged in as ``user`` when it is a saved user.

    The keys are the ones ``django.contrib.auth.login`` writes, so the view's
    per-turn re-read of the session finds the user that mounted. ``user=None``
    (or an unsaved or anonymous user) gives an anonymous session.

    Args:
        user: A saved ``AUTH_USER_MODEL`` instance, or ``None``.
        store: The ``SessionStore`` class to use. Defaults to the configured
            ``SESSION_ENGINE``'s store; an engine that cannot hold server
            state (signed cookies) raises ``ImproperlyConfigured``.
        backend: The authentication backend to record, as a dotted path.
            Defaults to the one ``Client.force_login`` chooses: the first entry
            of ``AUTHENTICATION_BACKENDS`` that can load a user.

    Returns:
        The saved session (it has a ``session_key``). Needs database access.
    """
    from django.contrib.auth import BACKEND_SESSION_KEY, HASH_SESSION_KEY, SESSION_KEY

    session = (store or _default_store())()
    if _is_saved_user(user):
        session[SESSION_KEY] = user._meta.pk.value_to_string(user)
        session[BACKEND_SESSION_KEY] = backend or _login_backend()
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

#: The page's render digest (``mixins/request.py``), echoed by the fallback client.
_DIGEST_META = re.compile(r'<meta name="djust-render-digest" content="([^"]*)">')
#: Where the page tells its client the CSRF cookie's name and the token (00-namespace.js).
_CSRF_COOKIE_META = re.compile(r'<meta name="djust-csrf-cookie" content="([^"]*)">')
_CSRF_TOKEN_META = re.compile(r'<meta name="djust-csrf-token" content="([^"]*)">')
_CSRF_INPUT = re.compile(r"""<[^>]*\bname=["']?csrfmiddlewaretoken["']?[^>]*>""", re.IGNORECASE)
_VALUE_ATTR = re.compile(r"""\bvalue=(?:"([^"]*)"|'([^']*)')""", re.IGNORECASE)
#: The socket path and SSE prefix the page gives its client (``{% djust_client_config %}``).
_WS_PATH_META = re.compile(r'<meta name="djust-ws-path" content="([^"]*)">')
_SSE_PREFIX_META = re.compile(r'<meta name="djust-sse-prefix" content="([^"]*)">')
#: How many whole-page answers in a row make the client stop sending its
#: digest, and for how many requests (``11-event-handler.js``).
_DIGEST_HOLD = 8
_SSE_ROUTING_HINT = (
    "Route them in the project's URLconf, as the browser's client expects them "
    "at the page's SSE prefix (default /djust/):\n\n"
    "    from djust.sse import sse_urlpatterns\n"
    '    urlpatterns += [path("djust/", include(sse_urlpatterns))]'
)


def _meta(pattern: "re.Pattern[str]", html: str) -> Optional[str]:
    match = pattern.search(html)
    return unescape(match.group(1)) if match else None


def _http_headers(extra: Dict[str, Any]) -> Dict[str, str]:
    """``HTTP_X_TENANT_ID`` style request extras as header names and values."""
    return {
        key[5:].replace("_", "-").title(): str(value)
        for key, value in extra.items()
        if key.startswith("HTTP_")
    }


def _query_params(url: str) -> Dict[str, Any]:
    """The page URL's query string as the client sends it: a repeated key is
    the list of its values (#4125)."""
    return {
        key: values if len(values) > 1 else values[0]
        for key, values in parse_qs(urlsplit(url).query, keep_blank_values=True).items()
    }


def _page_session_and_user(page: "LivePage") -> Tuple[Any, Any]:
    """The page's session and whoever it is logged in as, resolved as
    ``AuthenticationMiddleware`` does (``AnonymousUser`` when no one). Sync
    (database). Used only for the bare consumer, which has no middleware."""
    from types import SimpleNamespace

    from django.contrib.auth import get_user

    session = page.session
    if not _has_live_session(session):
        raise AssertionError(
            "the page GET left no saved server session (is SessionMiddleware "
            "installed and SESSION_ENGINE a server-side store?)"
        )
    return session, get_user(SimpleNamespace(session=session))


def _project_asgi_application() -> Any:
    """The project's ``ASGI_APPLICATION``, or ``None`` when it sets none."""
    from django.conf import settings
    from django.utils.module_loading import import_string

    dotted = getattr(settings, "ASGI_APPLICATION", None)
    return import_string(dotted) if dotted else None


def _resolves_to(path: str, view_class: type) -> bool:
    from django.urls import Resolver404, resolve

    try:
        match = resolve(path)
    except Resolver404:
        return False
    return getattr(match.func, "view_class", None) is view_class


class LivePage:
    """A page loaded with a real ``django.test.Client``; see :func:`open_live_page`.

    Attributes:
        url: The page URL that was requested.
        client: The ``django.test.Client`` holding the browser's cookies. Load
            another tab of the same browser with
            ``open_live_page(url, client=page.client)``.
        response: The page GET's response.
        html: The page GET's decoded body.
        tab: The tab id the page was given (``<meta name="djust-tab">``), or
            ``None`` when the page carries none.
        view_path: The view path on the page's root (``dj-view``), or ``None``.
        user: The user passed to :func:`open_live_page`, or ``None``. The
            transports are whoever the page's session is logged in as.
        extra: The request extras (``HTTP_HOST``, ``HTTP_X_TENANT_ID``, ...) the
            GET was made with; every request and socket opened from the page
            sends them.
        render_digest: The render digest the next fallback POST sends, as the
            client would (``None``: it sends none).
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
        self.render_digest: Optional[str] = _meta(_DIGEST_META, self.html) or None
        self._whole_answers = 0
        self._digest_hold = 0

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

    @property
    def ws_path(self) -> str:
        """The socket path the page gives its client (``djust-ws-path``), as the
        client honours it: a root-relative path, else ``/ws/live/``."""
        path = (_meta(_WS_PATH_META, self.html) or "").strip()
        if not path.startswith("/") or path.startswith("//"):
            return "/ws/live/"
        return path

    @property
    def csrf_token(self) -> str:
        """The CSRF token the page's client sends, found as it finds it: a
        rendered ``csrfmiddlewaretoken`` input, then the CSRF cookie (when
        script can read it), then ``<meta name="djust-csrf-token">``."""
        tag = _CSRF_INPUT.search(self.html)
        if tag:
            value = _VALUE_ATTR.search(tag.group(0))
            token = value and (value.group(1) or value.group(2))
            if token:
                return unescape(token)
        name = _meta(_CSRF_COOKIE_META, self.html) or "csrftoken"
        morsel = self.client.cookies.get(name)
        if morsel is not None and morsel.value and not morsel["httponly"]:
            return str(morsel.value)
        return _meta(_CSRF_TOKEN_META, self.html) or ""

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
            # The client names its wire protocol (#3658).
            "protocol": PROTOCOL_VERSION,
        }

    # -- HTTP fallback --------------------------------------------------------

    def _digest_to_send(self) -> Optional[str]:
        if not self.render_digest:
            return None
        if self._digest_hold > 0:
            self._digest_hold -= 1
            return None
        return self.render_digest

    def _note_answer(self, answer: Dict[str, Any], sent: Optional[str]) -> None:
        """The client's digest bookkeeping after an answer (``11-event-handler.js``)."""
        whole = isinstance(answer.get("html"), str)
        changed = whole or bool(answer.get("patches"))
        if answer.get("render_digest"):
            self.render_digest = str(answer["render_digest"])
        elif changed:
            self.render_digest = None
        if not sent or not changed:
            return
        if not whole:
            self._whole_answers = 0
        else:
            self._whole_answers += 1
            if self._whole_answers >= 2:
                self._digest_hold = _DIGEST_HOLD

    def post_event(
        self,
        event: str,
        params: Optional[Dict[str, Any]] = None,
        *,
        full: bool = False,
        **extra: Any,
    ) -> Any:
        """Send ``event`` over the HTTP fallback, as the client does without a socket.

        A JSON POST to the page URL with what the client sends: ``X-Djust-Event``,
        the page's tab (``X-Djust-Tab``), its CSRF token (``X-CSRFToken``), the
        page's request extras, and the render digest of the last answer
        (``X-Djust-Render-Digest``), so the server answers with patches where it
        can, as the browser gets them. Returns the response; its JSON body is
        the fallback answer (``patches`` or ``html``). Sync: call it with
        ``sync_to_async`` from an async test.

        Args:
            event: The handler name.
            params: The handler's parameters (the JSON body).
            full: Send no render digest, so the answer is the whole page.
            **extra: More request extras; they replace the ones above.
        """
        headers: Dict[str, Any] = {
            **self.extra,
            "HTTP_X_DJUST_EVENT": event,
            "HTTP_X_DJUST_PROTOCOL": str(PROTOCOL_VERSION),
        }
        if self.tab:
            headers["HTTP_X_DJUST_TAB"] = self.tab
        token = self.csrf_token
        if token:
            headers["HTTP_X_CSRFTOKEN"] = token
        sent = None if full else self._digest_to_send()
        if sent:
            headers["HTTP_X_DJUST_RENDER_DIGEST"] = sent
        headers.update(extra)
        response = self.client.post(
            self.url, data=json.dumps(params or {}), content_type="application/json", **headers
        )
        if response.status_code == 200 and "json" in response.get("Content-Type", ""):
            answer = json.loads(response.content)
            if isinstance(answer, dict):
                self._note_answer(answer, sent)
        return response

    # -- WebSocket -------------------------------------------------------------

    async def connect(
        self,
        view_path: Optional[str] = None,
        *,
        params: Optional[Dict[str, Any]] = None,
        application: Any = None,
        path: Optional[str] = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> "LiveSocket":
        """Connect a ``WebsocketCommunicator`` as the page's client and mount the view.

        The socket goes through the project's ``ASGI_APPLICATION`` (its origin
        validator, auth stack, router and any middleware of its own), with the
        browser's cookies, ``Origin`` and the page's request extras, at the
        socket path the page gives its client. When the project sets no
        ``ASGI_APPLICATION``, a bare ``LiveViewConsumer`` is used and its scope
        is given what ``AuthMiddlewareStack`` would put there: the page's
        session and its user.

        Args:
            view_path: The view to mount; defaults to the page's ``dj-view``.
            params: The mount parameters; defaults to the page URL's query.
            application: An ASGI application to use instead of ``ASGI_APPLICATION``.
            path: The WebSocket path; defaults to the page's (``/ws/live/``).
            timeout: Seconds to wait for the connection and each frame.

        Returns:
            A connected :class:`LiveSocket`; its ``mount`` is the mount answer
            (``"mount"`` on success, ``"error"`` / ``"navigate"`` otherwise).
        """
        from channels.testing import WebsocketCommunicator

        if application is None:
            application = _project_asgi_application()
        bare = application is None
        if bare:
            from .websocket import LiveViewConsumer

            application = LiveViewConsumer.as_asgi()
        # The browser sends every cookie of the site, HttpOnly ones included.
        cookie = "; ".join(
            "%s=%s" % (name, morsel.value) for name, morsel in self.client.cookies.items()
        )
        headers = [
            (b"host", self.host.encode("latin-1")),
            (b"origin", self.origin.encode("latin-1")),
            (b"cookie", cookie.encode("latin-1")),
            *(
                (name.lower().encode("latin-1"), value.encode("latin-1"))
                for name, value in _http_headers(self.extra).items()
                if name not in ("Host", "Origin")
            ),
        ]
        communicator = WebsocketCommunicator(application, path or self.ws_path, headers=headers)
        if bare:
            from asgiref.sync import sync_to_async

            session, user = await sync_to_async(_page_session_and_user)(self)
            communicator.scope["session"] = session
            communicator.scope["user"] = user
        socket = LiveSocket(communicator, timeout=timeout)
        try:
            connected, code = await communicator.connect(timeout=timeout)
            if not connected:
                raise AssertionError("the socket was refused (close code %r)" % (code,))
            socket._start()
            await socket.send(self.mount_frame(view_path, params=params))
            socket.mount = await socket.receive(_MOUNT_ANSWERS)
        except BaseException as exc:
            await socket._close_after(exc)
            raise
        return socket

    # -- SSE -------------------------------------------------------------------

    def _sse_urls(self, session_id: str) -> Tuple[str, str]:
        """The stream and message URLs the page's client uses, checked against
        the project's URLconf."""
        from .sse import DjustSSEMessageView, DjustSSEStreamView

        prefix = (_meta(_SSE_PREFIX_META, self.html) or "").strip() or "/djust/"
        if not prefix.endswith("/"):
            prefix += "/"
        stream = "%ssse/%s/" % (prefix, session_id)
        message = stream + "message/"
        if not _resolves_to(stream, DjustSSEStreamView) or not _resolves_to(
            message, DjustSSEMessageView
        ):
            raise AssertionError(
                "djust's SSE views are not routed at %ssse/<id>/ in this project's "
                "URLconf, so a browser's SSE client would get a 404. %s"
                % (prefix, _SSE_ROUTING_HINT)
            )
        return stream, message

    async def connect_sse(
        self,
        view_path: Optional[str] = None,
        *,
        params: Optional[Dict[str, Any]] = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> "LiveSSE":
        """Open the SSE stream as the page's client; the stream GET mounts the view.

        The stream GET and each event POST go through the project's URLconf
        and middleware (``django.test.AsyncClient``, with the browser's
        cookies), at the URLs the page gives its client, with ``Origin`` and
        the page's request extras. A project that does not route djust's SSE
        views fails here with how to route them.

        Args:
            view_path: The view to mount; defaults to the page's ``dj-view``.
            params: The mount parameters; defaults to the page URL's query.
            timeout: Seconds to wait for the stream GET and each frame.

        Returns:
            A :class:`LiveSSE`; its ``mount`` is the mount answer.

        Raises:
            AssertionError: The SSE views are not routed, or the stream GET
                did not answer a 200 stream (a middleware redirect, a refusal).
        """
        from django.test import AsyncClient

        session_id = str(uuid.uuid4())
        stream_url, message_url = self._sse_urls(session_id)
        client = AsyncClient(
            enforce_csrf_checks=getattr(self.client.handler, "enforce_csrf_checks", False)
        )
        client.cookies = self.client.cookies  # one browser: the same cookie jar
        query: Dict[str, Any] = _query_params(self.url) if params is None else dict(params)
        query.update(
            {
                "view": self._view(view_path),
                "_djust_url": self.path,
                PROTOCOL_QUERY_PARAM: str(PROTOCOL_VERSION),
            }
        )
        if self.tab:
            query["_djust_tab"] = self.tab
        stream = LiveSSE(self, session_id, client, message_url, timeout=timeout)
        try:
            response = await asyncio.wait_for(
                client.get(stream_url, query, headers=stream._headers()), timeout
            )
            if response.status_code != 200 or not getattr(response, "streaming", False):
                raise AssertionError(
                    "the SSE stream GET answered %s%s: %r"
                    % (
                        response.status_code,
                        " to %s" % response["Location"] if response.has_header("Location") else "",
                        getattr(response, "content", b"")[:300],
                    )
                )
            stream._start(response)
            stream.mount = await stream.receive(_MOUNT_ANSWERS)
        except BaseException as exc:
            await stream._close_after(exc)
            raise
        return stream


def open_live_page(url: str, *, user: Any = None, client: Any = None, **extra: Any) -> LivePage:
    """Load ``url`` as a browser does and return the :class:`LivePage`.

    A GET with a ``django.test.Client`` (``enforce_csrf_checks=True``, as a
    browser gets) through the project's own middleware and URLconf: the page
    view saves a server session, mounts the view and gives the page a tab id.
    Each call is a new page load, so a new tab; pass the same ``client`` to
    load a second tab in the same browser session. Sync: from an async test
    call it with ``sync_to_async``.

    Args:
        url: The page URL (path and query).
        user: A saved user to log in first (``client.force_login``), or ``None``
            for an anonymous page.
        client: The ``django.test.Client`` to use; a new one, enforcing CSRF,
            by default.
        **extra: Request extras sent with the GET and with every request and
            socket opened from the page: ``HTTP_HOST`` for a subdomain tenant,
            ``HTTP_X_TENANT_ID`` for a header tenant, and so on.

    Raises:
        AssertionError: The GET did not answer 200.
    """
    if client is None:
        from django.test import Client

        client = Client(enforce_csrf_checks=True)
    if user is not None:
        client.force_login(user)
    response = client.get(url, **extra)
    if response.status_code != 200:
        raise AssertionError(
            "GET %s answered %s%s"
            % (
                url,
                response.status_code,
                " to %s" % response["Location"] if response.has_header("Location") else "",
            )
        )
    return LivePage(url, client, response, user=user, extra=extra)


# --------------------------------------------------------------------------- #
# Transports
# --------------------------------------------------------------------------- #


#: Frames a connection may hold unread before its reader stops with an error:
#: a test that never reads a flood of pushes fails clearly instead of
#: exhausting memory. ``frames`` keeps as many of the latest frames.
_MAX_UNREAD_FRAMES = 10_000


class _Overflow(Exception):
    pass


def _deliver(frame: Dict[str, Any], frames: Any, queue: "asyncio.Queue[Any]") -> None:
    """Record ``frame`` and queue it for reading, within the unread cap."""
    if queue.qsize() >= _MAX_UNREAD_FRAMES:
        raise _Overflow(
            "more than %d frames arrived without being read; read them with "
            "receive() (or assert on frames) as they come" % _MAX_UNREAD_FRAMES
        )
    frames.append(frame)
    queue.put_nowait(frame)


def _reader_failure(exc: BaseException) -> str:
    if isinstance(exc, _Overflow):
        return str(exc)
    return "the frame reader failed: %r" % (exc,)


class _Closed:
    """Queued once the connection ended; every later read sees it."""

    def __init__(self, reason: str) -> None:
        self.reason = reason


#: Every transport opened and not yet closed, for :func:`close_live_pages`.
_OPEN: "weakref.WeakSet[_Transport]" = weakref.WeakSet()


def _cancel_quietly(task: "Optional[asyncio.Future[Any]]") -> None:
    """Cancel ``task`` if its loop is still open (a finaliser may run later)."""
    if task is None or task.done():
        return
    try:
        task.cancel()
    except RuntimeError:
        pass  # its loop has closed: nothing is left to stop


def _abandon_sse(session_id: str, pump: "Optional[asyncio.Future[Any]]") -> None:
    """Release an SSE stream nobody closed: what the stream view's own guard
    does for a stream that is gone (unregister the session, shut it down)."""
    _cancel_quietly(pump)
    sse = sys.modules.get("djust.sse")
    if sse is None:
        return
    session = sse._get_session(session_id)
    if session is not None:
        session._stream_closed = True
        sse._unregister_sse_session(session_id, session)
        session.shutdown()


def _abandon_socket(
    pump: "Optional[asyncio.Future[Any]]", app: "Optional[asyncio.Future[Any]]"
) -> None:
    _cancel_quietly(pump)
    _cancel_quietly(app)


def close_live_pages() -> None:
    """Release every socket and SSE stream opened from a :class:`LivePage`
    and not closed: a test that failed before its ``async with`` ended, or
    never closed what it opened.

    Sync, so it runs after the test's event loop is gone: an SSE session is
    unregistered and shut down as the stream view does for a client that
    went away; a socket's application and frame reader are cancelled.
    :data:`djust.testing.djust_live_pages` runs it after each pytest test;
    in a ``django.test`` case add ``self.addCleanup(close_live_pages)``.
    """
    for transport in list(_OPEN):
        transport._abandon()


class _Transport:
    """Frame reading shared by :class:`LiveSocket` and :class:`LiveSSE`.

    A background task reads every frame into a queue as it arrives, so a
    ``receive()`` that times out only stops waiting: the connection and the
    frames that arrive later are untouched.
    """

    _kind = "connection"

    def __init__(self, timeout: float) -> None:
        self.timeout = timeout
        #: The mount answer (``{"type": "mount", ...}`` on success).
        self.mount: Dict[str, Any] = {}
        #: Every frame received so far, in order (the latest
        #: ``_MAX_UNREAD_FRAMES`` of them).
        self.frames: "collections.deque[Dict[str, Any]]" = collections.deque(
            maxlen=_MAX_UNREAD_FRAMES
        )
        self._queue: "asyncio.Queue[Any]" = asyncio.Queue()
        self._unread: List[Any] = []
        self._pump: "Optional[asyncio.Future[Any]]" = None
        self._finalizer: Optional[Callable[[], Any]] = None
        self._ref = 0
        self.closed = False
        _OPEN.add(self)

    def _abandon(self) -> None:
        self.closed = True
        _OPEN.discard(self)
        if self._finalizer is not None:
            self._finalizer()

    async def _send(self, frame: Dict[str, Any]) -> None:
        raise NotImplementedError

    async def send(self, frame: Dict[str, Any]) -> None:
        """Send a raw frame (``url_change``, ``upload_register``, ...)."""
        await self._send(frame)

    async def close(self) -> None:
        raise NotImplementedError

    async def _next(self, timeout: float) -> Dict[str, Any]:
        """The next frame; ``asyncio.TimeoutError`` leaves the connection as it is."""
        if self._unread:
            item = self._unread.pop(0)
        else:
            item = await asyncio.wait_for(self._queue.get(), timeout)
        if isinstance(item, _Closed):
            self._unread.insert(0, item)  # every later read sees it too
            raise AssertionError("the %s ended: %s" % (self._kind, item.reason))
        return dict(item)

    async def receive(
        self, types: Optional[Tuple[str, ...]] = None, *, timeout: Optional[float] = None
    ) -> Dict[str, Any]:
        """The next frame (of one of ``types`` when given; others are skipped,
        and kept in :attr:`frames`).

        Raises:
            asyncio.TimeoutError: No such frame arrived within ``timeout``; the
                connection is untouched.
            AssertionError: The connection ended first.
        """
        wait = self.timeout if timeout is None else timeout
        loop = asyncio.get_running_loop()
        deadline = loop.time() + wait
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0 and not self._unread:
                raise asyncio.TimeoutError("no frame of %r within %ss" % (types, wait))
            frame = await self._next(max(remaining, 0))
            if _frame_is(frame, types):
                return frame

    async def receive_nothing(
        self, types: Optional[Tuple[str, ...]] = None, *, within: float = 0.5
    ) -> None:
        """Assert that no frame (of one of ``types`` when given) arrives within
        ``within`` seconds. Other frames stay unread, in order.

        Raises:
            AssertionError: Such a frame arrived.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + within
        others: List[Any] = []
        try:
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0 and not self._unread:
                    return
                try:
                    frame = await self._next(max(remaining, 0))
                except asyncio.TimeoutError:
                    return
                except AssertionError:
                    return  # the connection ended: nothing more can arrive
                if _frame_is(frame, types):
                    raise AssertionError(
                        "expected no %s frame within %ss, got %r"
                        % (" / ".join(types) if types else "", within, frame)
                    )
                others.append(frame)
        finally:
            self._unread[0:0] = others

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
        echoes it (``patch``, ``html_update``, ``noop``, ``error``, ...). An
        ``error`` with no ``ref`` (a refusal the server could not tie to an
        event) is also taken as the answer, since nothing else will come; an
        ``error`` that names another event's ``ref`` is not. Frames that arrive
        before the answer (push events, for example) stay unread for
        :meth:`receive`, and are in :attr:`frames`.

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
        skipped: List[Any] = []
        try:
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0 and not self._unread:
                    raise asyncio.TimeoutError("no answer to %r within %ss" % (event, wait))
                frame = await self._next(max(remaining, 0))
                if frame.get("ref") == ref:
                    return frame
                if frame.get("type") == "error" and frame.get("ref") is None:
                    return frame
                skipped.append(frame)
        finally:
            self._unread[0:0] = skipped

    async def _close_after(self, exc: BaseException) -> None:
        """Close after ``exc``; a failure to close never replaces ``exc``."""
        try:
            await self.close()
        except Exception as failure:  # noqa: BLE001 - reported on exc instead
            # Only an ordinary error is folded into ``exc``: a cancellation,
            # KeyboardInterrupt or SystemExit during the close propagates.
            note = getattr(exc, "add_note", None)
            if note is not None:
                note("djust.testing: closing the %s also failed: %r" % (self._kind, failure))

    async def __aexit__(self, exc_type: Any, exc: Optional[BaseException], tb: Any) -> None:
        if exc is None:
            await self.close()
        else:
            await self._close_after(exc)


async def _pump_socket(communicator: Any, frames: Any, queue: "asyncio.Queue[Any]") -> None:
    """Read the application's output into ``queue`` without ever timing out
    (a ``receive_output`` timeout cancels the application)."""
    reason = "the application finished"
    getter: "Optional[asyncio.Future[Any]]" = None
    try:
        while True:
            getter = asyncio.ensure_future(communicator.output_queue.get())
            await asyncio.wait({getter, communicator.future}, return_when=asyncio.FIRST_COMPLETED)
            if not getter.done():
                getter.cancel()
                if communicator.output_queue.empty():
                    if communicator.future.cancelled():
                        reason = "the application was cancelled"
                    elif communicator.future.exception() is not None:
                        reason = "the application raised %r" % (communicator.future.exception(),)
                    return
                message = communicator.output_queue.get_nowait()
            else:
                message = getter.result()
            getter = None
            if message.get("type") == "websocket.close":
                reason = "the server closed the socket (code %s)" % message.get("code", 1000)
                return
            if message.get("type") != "websocket.send":
                continue
            if message.get("text") is not None:
                try:
                    frame = json.loads(message["text"])
                except ValueError as exc:
                    raise ValueError(
                        "a text frame that is not JSON: %r" % message["text"][:200]
                    ) from exc
                _deliver(frame, frames, queue)
            elif message.get("bytes") is not None:
                # djust's binary frames (uploads) are frames too: never dropped.
                _deliver({"type": "binary", "bytes": message["bytes"]}, frames, queue)
    except Exception as exc:
        reason = _reader_failure(exc)
    finally:
        if getter is not None and not getter.done():
            getter.cancel()
        queue.put_nowait(_Closed(reason))


class LiveSocket(_Transport):
    """A connected, mounted ``WebsocketCommunicator``; see :meth:`LivePage.connect`.

    Use it as an async context manager, or call :meth:`disconnect`.

    Attributes:
        communicator: The ``channels.testing.WebsocketCommunicator``.
        mount: The mount answer.
        frames: Every frame received, in order.
    """

    _kind = "socket"

    def __init__(self, communicator: Any, *, timeout: float = _DEFAULT_TIMEOUT) -> None:
        super().__init__(timeout)
        self.communicator = communicator

    def _start(self) -> None:
        """Start reading frames; called once the socket is connected."""
        if self._pump is not None:
            return
        self._pump = asyncio.ensure_future(
            _pump_socket(self.communicator, self.frames, self._queue)
        )
        self._finalizer = weakref.finalize(
            self, _abandon_socket, self._pump, getattr(self.communicator, "future", None)
        )

    async def _send(self, frame: Dict[str, Any]) -> None:
        if self._pump is None:
            self._start()
        await self.communicator.send_json_to(frame)

    async def receive(
        self, types: Optional[Tuple[str, ...]] = None, *, timeout: Optional[float] = None
    ) -> Dict[str, Any]:
        if self._pump is None:
            self._start()
        return await super().receive(types, timeout=timeout)

    async def disconnect(self, code: int = 1000) -> None:
        """Close the socket as the browser does; the view is torn down. An
        exception the application raised is raised here."""
        if self.closed:
            return
        self.closed = True
        _OPEN.discard(self)
        if self._finalizer is not None:
            self._finalizer.detach()  # type: ignore[attr-defined]
        try:
            future = getattr(self.communicator, "future", None)
            if future is not None and future.done():
                if not future.cancelled():
                    future.result()
            else:
                await self.communicator.disconnect(code=code, timeout=self.timeout)
        finally:
            if self._pump is not None:
                self._pump.cancel()
                await asyncio.wait({self._pump})

    async def close(self) -> None:
        """The same as :meth:`disconnect`."""
        await self.disconnect()

    async def __aenter__(self) -> "LiveSocket":
        return self


async def _pump_sse(response: Any, frames: Any, queue: "asyncio.Queue[Any]") -> None:
    """Read the stream response into ``queue``; cancelling this task is a
    browser going away (the stream's own teardown runs)."""
    reason = "the stream ended"
    content: AsyncIterator[Any] = response.streaming_content
    try:
        async for chunk in content:
            text = chunk.decode("utf-8") if isinstance(chunk, bytes) else str(chunk)
            data = [line[6:] for line in text.splitlines() if line.startswith("data: ")]
            if not data:
                continue  # a keepalive comment
            try:
                frame = json.loads("\n".join(data))
            except ValueError as exc:
                raise ValueError("an SSE event that is not JSON: %r" % text[:200]) from exc
            if frame.get("type") == "sse_connect":
                continue
            _deliver(frame, frames, queue)
    except asyncio.CancelledError:
        reason = "the stream was closed"
        raise
    except Exception as exc:
        reason = _reader_failure(exc)
    finally:
        queue.put_nowait(_Closed(reason))


class LiveSSE(_Transport):
    """An open SSE stream with a mounted view; see :meth:`LivePage.connect_sse`.

    Events are POSTed to the project's SSE message URL as the client does;
    frames are read from the stream response. Use it as an async context
    manager, or call :meth:`close`.

    Attributes:
        session_id: The SSE session id.
        response: The stream GET's ``StreamingHttpResponse``.
        mount: The mount answer.
        frames: Every frame received, in order.
    """

    _kind = "SSE stream"

    def __init__(
        self,
        page: LivePage,
        session_id: str,
        client: Any,
        message_url: str,
        *,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        super().__init__(timeout)
        self.page = page
        self.session_id = session_id
        self.response: Any = None
        self._client = client
        self._message_url = message_url
        self._finalizer = weakref.finalize(self, _abandon_sse, session_id, None)

    def _headers(self) -> Dict[str, str]:
        # The test client sends the host (testserver) itself; a page opened
        # with ``HTTP_HOST`` sends that one through the extras.
        return {
            **_http_headers(self.page.extra),
            "Origin": self.page.origin,
            PROTOCOL_HEADER: str(PROTOCOL_VERSION),
        }

    def _start(self, response: Any) -> None:
        self.response = response
        session = self.session
        if session is not None:
            # This client has no POST in flight when it closes (each one was
            # answered), so the closing stream need not wait for one.
            session._skip_close_linger = True
        self._pump = asyncio.ensure_future(_pump_sse(response, self.frames, self._queue))
        self._finalizer.detach()  # type: ignore[union-attr]
        self._finalizer = weakref.finalize(self, _abandon_sse, self.session_id, self._pump)

    @property
    def session(self) -> Any:
        """The live ``djust.sse.SSESession`` (``None`` once it is closed or when the mount failed)."""
        from .sse import _get_session

        return _get_session(self.session_id)

    async def _send(self, frame: Dict[str, Any]) -> None:
        response = await self._client.post(
            self._message_url,
            data=json.dumps(frame),
            content_type="application/json",
            headers=self._headers(),
        )
        if response.status_code != 200:
            raise AssertionError(
                "the SSE message POST answered %s: %r"
                % (response.status_code, response.content[:300])
            )

    async def close(self) -> None:
        """Close the stream as a browser going away does: the stream's own
        teardown saves the view's state and disposes the session."""
        if self.closed:
            return
        self.closed = True
        _OPEN.discard(self)
        if self._finalizer is not None:
            self._finalizer.detach()  # type: ignore[attr-defined]
        if self._pump is not None:
            self._pump.cancel()
            await asyncio.wait({self._pump})
        else:
            _abandon_sse(self.session_id, None)

    async def __aenter__(self) -> "LiveSSE":
        return self


#: Closes what each test's pages opened (:func:`close_live_pages`). Import it
#: into a ``conftest.py`` (``from djust.testing import djust_live_pages``); it
#: is autouse, so every test below that conftest gets it. Defined only when
#: pytest is running, so importing ``djust.testing`` never imports pytest.
if "pytest" in sys.modules:
    _pytest = sys.modules["pytest"]

    @_pytest.fixture(autouse=True)
    def djust_live_pages() -> Any:
        """Close every socket and SSE stream a test's live pages left open."""
        yield
        close_live_pages()

    __all__.append("djust_live_pages")
