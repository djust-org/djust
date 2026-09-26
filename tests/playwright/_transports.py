"""Force and verify the transport a Playwright script claims to test.

The page's own configuration script assigns ``window.DJUST_USE_WEBSOCKET``
after any init script runs, so a plain ``window.DJUST_USE_WEBSOCKET = false``
init script is overwritten and the page silently keeps using WebSocket. These
init scripts pin the value with a no-op setter instead. ``TransportWatch``
then records which transports the page really used, so a script can fail when
its "SSE" or "HTTP-only" run was not.
"""

_PIN_NO_WEBSOCKET = (
    "Object.defineProperty(window, 'DJUST_USE_WEBSOCKET', "
    "{configurable: false, get: () => false, set: () => {}});"
)
_PIN_NO_EVENTSOURCE = (
    "Object.defineProperty(window, 'EventSource', "
    "{configurable: false, get: () => undefined, set: () => {}});"
)

INIT = {
    "websocket": "",
    "sse": _PIN_NO_WEBSOCKET,
    "http": _PIN_NO_WEBSOCKET + _PIN_NO_EVENTSOURCE,
}


class TransportWatch:
    """Record LiveView WebSocket connections, SSE streams and HTTP event POSTs."""

    def __init__(self, page):
        self.websockets = 0
        self.sse_streams = 0
        self.http_events = 0
        page.on("websocket", self._on_websocket)
        page.on("request", self._on_request)

    def _on_websocket(self, ws):
        if "/ws/live" in ws.url:
            self.websockets += 1

    def _on_request(self, request):
        if "/djust/sse/" in request.url and request.method == "GET":
            self.sse_streams += 1
        elif request.method == "POST" and request.headers.get("x-djust-event"):
            self.http_events += 1

    def check(self, transport, label, failures):
        """Append a failure unless the page used exactly the claimed transport."""
        used = {
            "websocket": self.websockets > 0,
            "sse": self.sse_streams > 0,
            "http": self.http_events > 0,
        }
        others = [name for name, seen in used.items() if seen and name != transport]
        if transport == "http":
            # HTTP-only pages may still POST from a WebSocket/SSE page's
            # fallback; the claim is that no live transport was opened.
            others = [name for name in others if name != "http"]
        if not used[transport] or others:
            failures.append(
                f"{label}: expected transport {transport}, saw "
                f"websocket={self.websockets} sse={self.sse_streams} http={self.http_events}"
            )
