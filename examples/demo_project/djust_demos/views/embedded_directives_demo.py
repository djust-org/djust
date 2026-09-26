"""ADR-037 row 20 live surface: which view an embedded child's events reach.

The parent renders a child with ``{% live_render %}``. Both views declare the
same handler names, so the page shows which one received each event:
``tests/playwright/test_embedded_directives.py`` drives ``dj-shortcut``,
``dj-click-away``, ``dj-paste`` and ``dj-click`` inside the child over
WebSocket, SSE and HTTP-only transports.
"""

from djust import LiveView
from djust.decorators import event_handler


class _Receiver:
    """The same four handlers on both views; each records what arrived."""

    received = ""

    def _record(self, name):
        self.received = (self.received + " " + name).strip()

    @event_handler
    def got_shortcut(self, **kwargs):
        self._record("shortcut")

    @event_handler
    def got_away(self, **kwargs):
        self._record("away")

    @event_handler
    def got_paste(self, **kwargs):
        self._record("paste")

    @event_handler
    def got_click(self, **kwargs):
        self._record("click")


class EmbeddedDirectivesChild(_Receiver, LiveView):
    template = """<div>
<p>Child received: <span id="child-received">{{ received }}</span></p>
<div id="away-box" dj-click-away="got_away" style="padding:1rem;border:1px solid #999">inside</div>
<span id="shortcut-host" dj-shortcut="ctrl+k:got_shortcut">shortcut</span>
<input id="paste-box" dj-paste="got_paste" placeholder="paste here">
<button id="child-click" dj-click="got_click">click</button>
</div>"""

    def mount(self, request, **kwargs):
        self.received = ""


class EmbeddedDirectivesView(_Receiver, LiveView):
    template_name = "demos/embedded_directives.html"

    def mount(self, request, **kwargs):
        self.received = ""
