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

    # Or pass URL query params: /shell/?url=ws://host:7681&rows=30&cols=100

Note: ttyd must be run with --check-origin=false (or same origin) to allow
WebSocket connections from the browser. CDN-loaded xterm.js requires internet
access; vendor xterm.js to static/ for offline/air-gapped environments.
"""

import json
from typing import Optional

from djust import LiveView
from djust.decorators import event_handler


class TtydTerminalView(LiveView):
    """LiveView that renders an xterm.js terminal connected to a ttyd backend.

    All terminal configuration flows through URL query params (mount-time only).
    To hardcode config, use the subclass pattern — this avoids user-controlled
    WebSocket URL injection in sensitive deployments.

    Lifecycle callbacks::

        class MyTerminalView(TtydTerminalView):
            def on_ttyd_connect(self, timestamp="", user_agent="", **kwargs):
                self.session_log.append(f"Connected at {timestamp}")

            def on_ttyd_disconnect(self, timestamp="", code=0, reason="", **kwargs):
                self.session_log.append(f"Disconnected at {timestamp} (code={code})")
    """

    template_name = "djust_components/ttyd_terminal.html"
    login_required = False  # v1: assume open/local access; override in subclasses

    # Default props — override by subclassing or via URL query params
    ttyd_url: str = "ws://localhost:7681"
    rows: int = 24
    cols: int = 80
    theme: Optional[dict] = None

    def mount(self, request, **kwargs):
        params = request.GET
        self.ttyd_url = params.get("url", self.__class__.ttyd_url)
        self.rows = int(params.get("rows", self.__class__.rows))
        self.cols = int(params.get("cols", self.__class__.cols))

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

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["theme_json"] = json.dumps(self.theme or {})
        return ctx

    @event_handler
    def on_ttyd_connect(self, timestamp: str = "", user_agent: str = "", **kwargs):
        """Called when the ttyd WebSocket connection opens in the browser."""
        self.terminal_connected = True
        self.session_start = timestamp
        self.session_end = None

    @event_handler
    def on_ttyd_disconnect(self, timestamp: str = "", code: int = 0, reason: str = "", **kwargs):
        """Called when the ttyd WebSocket connection closes in the browser."""
        self.terminal_connected = False
        self.session_end = timestamp
