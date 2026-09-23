"""ADR-038 E5 live matrix surface: an explicit view and its legacy twin.

Exercised by ``tests/playwright/test_exposure_matrix.py``, which drives both
over WebSocket and SSE in a real browser and asserts that the sentinels below
reach no destination (HTML, frames, stored session state, server log) under
the explicit policy, while the legacy twin shows the harness can see them.

- ``E5_UNDECLARED_SENTINEL``: an ordinary attribute. Never context, never
  persisted, never sent.
- ``E5_PRIVATE_SENTINEL``: an underscore attribute. Same.
- ``E5_ERROR_SENTINEL``: raised by a handler. Errors follow Django (ADR-038
  D-a): under DEBUG every view's error frame carries it; in production the
  explicit view's frame and log do not.
"""

from djust import LiveView
from djust.decorators import event_handler, state


class ExposureMatrixView(LiveView):
    """Explicit policy: only declared state and context reach any destination."""

    exposure_policy = "explicit"
    template_name = "demos/exposure_matrix.html"

    count = state(0, persist="server")
    page = state(1, persist="client", client=True)

    def mount(self, request, **kwargs):
        self.secret_note = "E5_UNDECLARED_SENTINEL"
        self._private_note = "E5_PRIVATE_SENTINEL"

    def handle_params(self, params, uri):
        raw = params.get("page")
        if isinstance(raw, str) and raw.isdigit():
            self.page = int(raw)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["count"] = self.count
        context["page"] = self.page
        context["policy"] = "explicit"
        return context

    @event_handler()
    def increment(self):
        self.count += 1

    @event_handler()
    def spawn(self):
        self.start_async(self._work)

    def _work(self):
        return 10

    def handle_async_result(self, name, result=None, error=None):
        if error is None:
            self.count += result

    @event_handler()
    def boom(self):
        raise ValueError("E5_ERROR_SENTINEL")


class LegacyExposureMatrixView(LiveView):
    """The legacy twin: same behaviour, implicit exposure. Harness control."""

    template_name = "demos/exposure_matrix_legacy.html"

    def mount(self, request, **kwargs):
        self.count = 0
        self.page = 1
        self.policy = "legacy"
        self.secret_note = "E5_UNDECLARED_SENTINEL"
        self._private_note = "E5_PRIVATE_SENTINEL"

    def handle_params(self, params, uri):
        raw = params.get("page")
        if isinstance(raw, str) and raw.isdigit():
            self.page = int(raw)

    @event_handler()
    def increment(self):
        self.count += 1

    @event_handler()
    def spawn(self):
        self.start_async(self._work)

    def _work(self):
        return 10

    def handle_async_result(self, name, result=None, error=None):
        if error is None:
            self.count += result

    @event_handler()
    def boom(self):
        raise ValueError("E5_ERROR_SENTINEL")
