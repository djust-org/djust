"""#4096: ``LiveViewTestClient.send_event`` refuses what the transports refuse,
and ``state_after`` holds the view's state, not framework attributes.

The client ran a method without ``@event_handler`` and reported success, while
every transport refuses that event under the default strict
``event_security``: a test passed for a view that does not work.
"""

from __future__ import annotations

import pytest
from django.test import override_settings

from djust import LiveView, state
from djust.decorators import event_handler, push_handler, rate_limit
from djust.testing import EventRejectedError, LiveViewTestClient, NoHandlerFoundError


class CounterView(LiveView):
    exposure_policy = "explicit"
    template = "<div>n={{ n }}</div>"
    n = state(0)
    label = "plain class attribute"

    def mount(self, request, **kwargs):
        self.n = 0
        self.note = "set in mount"

    def bump(self, **kwargs):  # no @event_handler: not an event
        self.n += 1

    @event_handler
    def inc(self, **kwargs):
        self.n += 1

    @event_handler
    def _hidden(self, **kwargs):  # decorated, but a private name is never an event
        self.n += 1000

    @push_handler
    def pushed(self, **kwargs):
        self.n += 100

    def get_context_data(self, **kwargs):
        return {"n": self.n}


def test_an_undecorated_method_is_refused_and_does_not_run():
    client = LiveViewTestClient(CounterView).mount()
    with pytest.raises(EventRejectedError, match="not decorated with @event_handler"):
        client.send_event("bump")
    assert client.view_instance.n == 0


def test_the_refusal_is_a_no_handler_error_and_carries_the_transport_answer():
    """``raise_on_missing=False``: the envelope holds what a transport sends
    (generic outside DEBUG, as ``_safe_error``) and the reason in ``detail``."""
    client = LiveViewTestClient(CounterView).mount()
    with pytest.raises(NoHandlerFoundError):
        client.send_event("bump")
    result = client.send_event("bump", raise_on_missing=False)
    assert result["success"] is False
    assert result["code"] == "event_rejected"
    assert result["error"] == "Event rejected"
    assert "@event_handler" in result["detail"]
    assert client.view_instance.n == 0
    with override_settings(DEBUG=True):
        result = client.send_event("bump", raise_on_missing=False)
    assert "@event_handler" in result["error"]


def test_a_decorated_handler_still_runs():
    client = LiveViewTestClient(CounterView).mount()
    result = client.send_event("inc")
    assert result["success"] is True
    assert client.view_instance.n == 1


def test_open_event_security_runs_it_as_the_transports_do():
    client = LiveViewTestClient(CounterView).mount()
    from djust.config import config as djust_config

    previous = djust_config.get("event_security", "strict")
    djust_config.set("event_security", "open")
    try:
        result = client.send_event("bump")
    finally:
        djust_config.set("event_security", previous)
    assert result["success"] is True
    assert client.view_instance.n == 1


@pytest.mark.parametrize("name", ["__class__", "_private", "server_push", "resume_wizard"])
def test_names_the_transports_never_dispatch_are_refused(name):
    client = LiveViewTestClient(CounterView).mount()
    with pytest.raises(NoHandlerFoundError):
        client.send_event(name)


def test_a_private_name_is_refused_even_when_decorated():
    client = LiveViewTestClient(CounterView).mount()
    with pytest.raises(EventRejectedError, match="unsafe event name"):
        client.send_event("_hidden")
    assert client.view_instance.n == 0


def test_a_push_handler_is_refused_as_missing():
    client = LiveViewTestClient(CounterView).mount()
    with pytest.raises(NoHandlerFoundError):
        client.send_event("pushed")
    assert client.view_instance.n == 0


def test_the_security_check_runs_before_the_rate_limit():
    """Transport order: a refused event does not spend the handler's budget."""

    class Limited(LiveView):
        exposure_policy = "explicit"
        template = "<div></div>"

        @rate_limit(rate=1, burst=1)
        def hit(self, **kwargs):  # undecorated: refused first
            pass

    client = LiveViewTestClient(Limited).mount()
    for _ in range(3):
        result = client.send_event("hit", raise_on_missing=False)
        assert result["code"] == "event_rejected"


def test_state_after_holds_the_view_state_only():
    client = LiveViewTestClient(CounterView).mount()
    after = client.send_event("inc")["state_after"]
    assert after == {"n": 1, "label": "plain class attribute", "note": "set in mount"}
    for framework_name in ("request", "template", "template_name", "login_required"):
        assert framework_name not in after
    assert client.get_state() == after
