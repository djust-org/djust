"""ADR-036 P2 (d) live surface: strict event parameters in a real browser.

Exercised by ``tests/playwright/test_strict_parameters.py`` over WebSocket,
SSE and HTTP-only transports. Every handler records what it received in
``result`` and counts its calls, so a rejected event is visible as an
unchanged count. ``legacy_click`` is the control: its legacy payload still
carries ``data-*`` attributes.
"""

from djust import LiveView
from djust.decorators import event_handler


class StrictParametersView(LiveView):
    template_name = "demos/strict_parameters.html"

    def mount(self, request, **kwargs):
        self.result = "none"
        self.calls = 0

    def _record(self, text):
        self.result = text
        self.calls += 1

    @event_handler(parameter_policy="strict")
    def pick(self, item_id: int) -> None:
        self._record("pick:%s:%s" % (item_id, type(item_id).__name__))

    @event_handler(parameter_policy="strict")
    def search(self, value: str) -> None:
        self._record("search:%s" % value)

    @event_handler(parameter_policy="strict")
    def save(self, title: str) -> None:
        self._record("save:%s" % title)

    @event_handler(parameter_policy="legacy")
    def legacy_click(self, **kwargs) -> None:
        self._record("legacy:%s" % ",".join(sorted(kwargs)))
