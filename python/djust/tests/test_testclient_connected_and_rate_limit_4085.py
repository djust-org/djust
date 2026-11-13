"""``LiveViewTestClient`` runs ``connected()`` and applies ``@rate_limit`` (#4085).

A live mount (WebSocket or SSE) runs the view's ``connected()`` hook after
``mount()``, ``handle_params()`` and the object check, before the first render
(``ViewRuntime.dispatch_mount`` through ``_child_lifecycle.begin_live_connection``).
Every transport applies a handler's ``@rate_limit`` against the shared per-caller
store (``djust.rate_limit.handler_rate_check``; the HTTP paths through
``http_handler_rate_check``). The test client did neither, so a test could pass
for a view that behaves differently over a socket.
"""

from __future__ import annotations

import pytest

from djust import LiveView, state
from djust.decorators import event_handler, rate_limit
from djust.rate_limit import reset_handler_buckets
from djust.testing import LiveViewTestClient

pytestmark = pytest.mark.django_db

HOOKS: list = []


@pytest.fixture(autouse=True)
def _fresh():
    reset_handler_buckets()
    HOOKS.clear()
    yield
    reset_handler_buckets()
    HOOKS.clear()


class ConnectedView(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root>{{ status }}</div>"
    status = state("mounted")

    def mount(self, request, **kwargs):
        HOOKS.append("mount")

    def connected(self):
        HOOKS.append("connected")
        self.status = "live"

    def disconnected(self):
        HOOKS.append("disconnected")


class FailingConnectedView(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root></div>"

    def connected(self):
        raise RuntimeError("connected failed")

    def disconnected(self):
        HOOKS.append("disconnected")


class LimitedView(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root>{{ count }}</div>"
    count = state(0)

    @event_handler
    @rate_limit(rate=0.001, burst=2)
    def bump(self, **kwargs):
        self.count += 1

    @event_handler
    def free(self, **kwargs):
        self.count += 1


class TestConnected:
    def test_a_live_mount_runs_connected_after_mount(self):
        client = LiveViewTestClient(ConnectedView).mount()
        assert HOOKS == ["mount", "connected"]
        client.assert_state(status="live")

    def test_the_http_prerender_mount_does_not(self):
        client = LiveViewTestClient(ConnectedView).mount(via_websocket=False)
        assert HOOKS == ["mount"]
        client.assert_state(status="mounted")

    def test_a_failing_connected_fails_the_mount_and_runs_disconnected(self):
        client = LiveViewTestClient(FailingConnectedView)
        with pytest.raises(RuntimeError, match="connected failed"):
            client.mount()
        # As the runtime: the view entered the connected phase, so it is released
        # with its disconnected() hook, and the client is not mounted.
        assert HOOKS == ["disconnected"]
        with pytest.raises(RuntimeError, match="not mounted"):
            client.send_event("anything")


class TestRateLimit:
    def test_events_past_the_burst_are_refused_and_do_not_run(self):
        client = LiveViewTestClient(LimitedView).mount()
        assert client.send_event("bump")["success"]
        assert client.send_event("bump")["success"]
        refused = client.send_event("bump")
        assert refused["success"] is False
        assert refused["code"] == "rate_limited"
        assert refused["error"] == "Rate limit exceeded"
        client.assert_state(count=2)
        # An undecorated handler is not limited.
        assert client.send_event("free")["success"]
        client.assert_state(count=3)

    def test_the_budget_is_per_caller_across_clients_as_on_the_transports(self):
        LiveViewTestClient(LimitedView).mount().send_event("bump")
        LiveViewTestClient(LimitedView).mount().send_event("bump")
        third = LiveViewTestClient(LimitedView).mount().send_event("bump")
        assert third["code"] == "rate_limited"
        reset_handler_buckets()
        assert LiveViewTestClient(LimitedView).mount().send_event("bump")["success"]

    def test_the_client_calls_the_shared_http_check(self, monkeypatch):
        """The same function the page-POST fallback and the HTTP API call."""
        import djust.rate_limit as rl

        seen = []

        def refuse(request, owner, handler_name, handler):
            seen.append((type(owner).__name__, handler_name, handler.__name__))
            return False

        monkeypatch.setattr(rl, "http_handler_rate_check", refuse)
        client = LiveViewTestClient(LimitedView).mount()
        assert client.send_event("bump")["code"] == "rate_limited"
        assert seen == [("LimitedView", "bump", "bump")]


class TestSmokeFuzz:
    def test_fuzzing_a_rate_limited_handler_reports_no_rate_limit_failures(self):
        """The smoke test sends one event per fuzz payload, each on a fresh client.

        Those are many events from one caller; the fuzz checks how a handler takes
        its input, not its limit, so each fresh client starts with a full budget.
        """
        from unittest.mock import patch

        from djust.testing import LiveViewSmokeTest

        suite = type("Smoke", (LiveViewSmokeTest,), {})()
        with patch("djust.testing._discover_views", return_value=[LimitedView]):
            try:
                suite.test_fuzz_handlers_succeed()
                report = ""
            except AssertionError as exc:
                # Findings on framework handlers are not what this tests.
                report = str(exc)
            suite.test_fuzz_no_unhandled_crash()
        assert "Rate limit" not in report, report
        assert "LimitedView.bump" not in report, report
