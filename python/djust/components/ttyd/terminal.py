"""TtydTerminalView — embed a ttyd WebSocket terminal via xterm.js.

The view renders a container div with dj-hook="TtydTerminal" and passes
all config as data-* attributes. The JS hook (ttyd_terminal.js) opens a
direct WebSocket to the ttyd backend — no djust relay.

Primary use cases:
  - djustlive deploy log tailing
  - Local dev shell embedding

Usage::

    # urls.py
    from djust.components.ttyd import TtydTerminalView

    path("shell/", TtydTerminalView.as_view(), name="shell"),

    # With custom defaults (subclass pattern)
    class DeployLogView(TtydTerminalView):
        ttyd_url = "ws://localhost:7682"
        rows = 40
        cols = 120

    # ``rows`` / ``cols`` / ``theme`` may also come from the query string:
    #     /shell/?rows=30&cols=100
    # ``?url=`` is honoured ONLY for an origin the view allows (see below):
    #     /shell/?url=ws://localhost:7681/ws          # the view's own endpoint
    #     /shell/?url=wss://shell.example.com/ws      # needs ttyd_allowed_origins

Note: ttyd must be run with --check-origin=false (or same origin) to allow
WebSocket connections from the browser. xterm.js is vendored by djust (asset
``xterm``) and loaded same-origin with Subresource Integrity, so no internet
access is needed.
"""

import json
import logging
import re
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlsplit

from djust import LiveView
from djust.decorators import event_handler

logger = logging.getLogger(__name__)

#: The schemes a ttyd endpoint may use, with the port each implies when none is
#: written. The browser opens this URL with ``new WebSocket()``, so nothing else
#: is meaningful (and ``http``/``https`` would be refused by the browser anyway).
_WS_DEFAULT_PORTS = {"ws": 80, "wss": 443}

#: The longest ``?url=`` value looked at. A real endpoint is far shorter.
_MAX_URL_LENGTH = 2048

#: The largest terminal a ``?rows=`` / ``?cols=`` may ask for (#3699). The browser draws
#: ``rows`` x ``cols`` cells and sizes the container with ``calc(<rows> * 1.2em)``, so an
#: unbounded value would let a link make the victim's own tab allocate a huge terminal.
MAX_ROWS = 500
MAX_COLS = 1000


_INTEGER_RE = re.compile(r"\s*([+-]?)([0-9]+)\s*")


def _bounded_int(value: Any, default: int, maximum: int) -> int:
    """``value`` as an int clamped to ``1..maximum``, or ``default`` if it is not an integer.

    ``value`` is a query-string parameter, so it is untrusted (#3699). ``None`` (the
    parameter is absent) keeps ``default`` as the class author wrote it. A string that
    is not an integer (``"x"``, ``""``, ``"1.5"``) also falls back to ``default``
    rather than raising. A valid integer is pulled into ``1..maximum``: zero and
    negative become 1, an oversized one the cap.

    A digit string too long for ``int()`` to parse (Python refuses past
    ``sys.get_int_max_str_digits()``, 4300 by default, and ``PYTHONINTMAXSTRDIGITS=0``
    lifts the limit) is still a syntactically valid integer, just a huge one, so it
    clamps like any other: the cap, or 1 if it is negative or all zeros. The answer must not depend
    on the interpreter's limit setting (4299 nines and 4301 nines both give the cap).
    """
    if value is None:
        return default
    try:
        number = int(value)
    except (TypeError, ValueError):
        found = _INTEGER_RE.fullmatch(value) if isinstance(value, str) else None
        if found is None:
            return default
        # Zero and negative clamp to 1; any other value is above the cap.
        return 1 if found.group(1) == "-" or not found.group(2).strip("0") else maximum
    return max(1, min(number, maximum))


def _split_ws_url(value: Any) -> Optional[Tuple[str, str, int, str, str]]:
    """``(scheme, host, port, path, query)`` of a ``ws://`` / ``wss://`` URL, else None.

    Strict by design, because the result decides which endpoint receives a
    user's keystrokes. Refused outright: a non-string, an over-long value, any
    backslash, whitespace or control character (a browser treats ``\\`` as ``/``
    in a special-scheme URL, so ``ws://evil.example\\@localhost`` names ``evil.example``
    to the browser and ``localhost`` to a ``urlsplit`` that does not know that),
    any non-ASCII character (look-alike dots and hosts), userinfo, a missing host
    and a scheme other than ``ws``/``wss``. The scheme and host are lower-cased and
    the port made explicit so two spellings of one origin compare equal.
    """
    if not isinstance(value, str) or not value or len(value) > _MAX_URL_LENGTH:
        return None
    if not value.isascii() or any(
        ch == "\\" or ch.isspace() or not ch.isprintable() for ch in value
    ):
        return None
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    if scheme not in _WS_DEFAULT_PORTS or not host or "@" in parts.netloc or "%" in host:
        return None
    return (
        scheme,
        host,
        port if port is not None else _WS_DEFAULT_PORTS[scheme],
        parts.path,
        parts.query,
    )


def _serialize_origin(scheme: str, host: str, port: int) -> str:
    """``scheme://host[:port]`` from the parts :func:`_split_ws_url` returns."""
    if ":" in host:  # an IPv6 literal, which ``urlsplit`` hands back without brackets
        host = "[%s]" % host
    if port == _WS_DEFAULT_PORTS[scheme]:
        return "%s://%s" % (scheme, host)
    return "%s://%s:%d" % (scheme, host, port)


class TtydTerminalView(LiveView):
    """LiveView that renders an xterm.js terminal connected to a ttyd backend.

    ``rows``, ``cols`` and ``theme`` may come from the query string (mount-time
    only). ``?rows=`` / ``?cols=`` are untrusted: a value that is not an integer
    (``x``, empty) falls back to the class default, and an integer is clamped to
    ``1..MAX_ROWS`` (500) / ``1..MAX_COLS`` (1000), so a link cannot make the
    victim's tab allocate a huge terminal (#3699). ``?url=`` is a development convenience and is **restricted**: the
    browser sends everything the user types to that WebSocket and draws whatever
    it answers as terminal output, so a link that chose the endpoint freely could
    point a victim's terminal at a server that shows a fake prompt and records the
    keystrokes (#3679). A ``?url=`` is therefore honoured only when it is a
    ``ws://`` or ``wss://`` URL with no query string whose origin (scheme, host
    and port, compared exactly) is the view's own :attr:`ttyd_url` or is listed in
    :attr:`ttyd_allowed_origins`; anything else is ignored (a warning is logged,
    without the value) and the view falls back to :attr:`ttyd_url`. A query is
    refused because a ttyd started with ``--url-arg`` turns URL arguments into
    command-line arguments of the spawned process. The URL the browser receives is
    rebuilt from the allowed origin plus the requested path, never echoed. A
    subclass's own ``ttyd_url`` is trusted as written and may carry a query; that
    query is NOT restored when ``?url=`` restates the URL, so leave ``?url=`` off
    to keep it.

    To embed a terminal on a deployment you control, set ``ttyd_url`` on a
    subclass; to let ``?url=`` reach other hosts, list them in
    ``ttyd_allowed_origins``::

        class ShellView(TtydTerminalView):
            ttyd_url = "wss://shell.internal.example:7681/ws"
            ttyd_allowed_origins = ("wss://shell-2.internal.example",)

    Lifecycle callbacks::

        class MyTerminalView(TtydTerminalView):
            def on_ttyd_connect(self, timestamp="", user_agent="", **kwargs):
                self.session_log.append(f"Connected at {timestamp}")

            def on_ttyd_disconnect(self, timestamp="", code=0, reason="", **kwargs):
                self.session_log.append(f"Disconnected at {timestamp} (code={code})")
    """

    template_name = "djust_components/ttyd_terminal.html"
    login_required = False  # v1: assume open/local access; override in subclasses

    # Default props — override by subclassing (``?url=`` is restricted, see the class docstring)
    ttyd_url: str = "ws://localhost:7681"
    #: Extra origins (``"wss://host"`` / ``"ws://host:port"``) a ``?url=`` may name,
    #: besides the origin of ``ttyd_url``. Scheme, host and port are matched exactly.
    ttyd_allowed_origins: Tuple[str, ...] = ()
    rows: int = 24
    cols: int = 80
    theme: Optional[dict] = None

    def mount(self, request: Any, **kwargs: Any) -> None:
        params = request.GET
        self.ttyd_url = self._resolve_ttyd_url(params.get("url"))
        self.rows = _bounded_int(params.get("rows"), self.__class__.rows, MAX_ROWS)
        self.cols = _bounded_int(params.get("cols"), self.__class__.cols, MAX_COLS)

        theme_param = params.get("theme", None)
        if theme_param:
            try:
                self.theme = json.loads(theme_param)
            except (json.JSONDecodeError, TypeError):
                self.theme = {}
        else:
            class_theme = self.__class__.theme
            self.theme = dict(class_theme) if class_theme else {}

        self.terminal_connected: bool = False
        self.session_start: Optional[str] = None
        self.session_end: Optional[str] = None

    @classmethod
    def _resolve_ttyd_url(cls, requested: Optional[str]) -> str:
        """The endpoint to render: ``requested`` if it names an allowed origin, else ``ttyd_url``."""
        if not requested:
            return cls.ttyd_url
        target = _split_ws_url(requested)
        extra = cls.ttyd_allowed_origins or ()
        if isinstance(extra, str):  # ("wss://host") without the comma is a str, not a tuple
            extra = (extra,)
        allowed = {
            entry[:3] for entry in map(_split_ws_url, (cls.ttyd_url, *extra)) if entry is not None
        }
        # A requested query string is refused too: a ttyd run with ``--url-arg`` turns
        # URL arguments into command-line arguments of the spawned process, and a link
        # has no business supplying them. A subclass's own ``ttyd_url`` may carry one.
        if target is None or target[:3] not in allowed or target[4]:
            logger.warning(
                "%s ignored a ?url= that is not a ws:// or wss:// URL, without a query "
                "string, on an allowed origin (its own ttyd_url or ttyd_allowed_origins)",
                cls.__name__,
            )
            return cls.ttyd_url
        scheme, host, port, path, _query = target
        return _serialize_origin(scheme, host, port) + path

    def get_context_data(self, **kwargs: Any) -> Dict[str, Any]:
        ctx = super().get_context_data(**kwargs)
        ctx["theme_json"] = json.dumps(self.theme or {})
        return ctx

    @event_handler
    def on_ttyd_connect(self, timestamp: str = "", user_agent: str = "", **kwargs: Any) -> None:
        """Called when the ttyd WebSocket connection opens in the browser."""
        self.terminal_connected = True
        self.session_start = timestamp
        self.session_end = None

    @event_handler
    def on_ttyd_disconnect(
        self, timestamp: str = "", code: int = 0, reason: str = "", **kwargs: Any
    ) -> None:
        """Called when the ttyd WebSocket connection closes in the browser."""
        self.terminal_connected = False
        self.session_end = timestamp
