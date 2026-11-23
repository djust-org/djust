"""#4285: a component's identity ``name`` is framework-stamped, so a strict
handler is sent it only when declared. The server contract is unchanged."""

from __future__ import annotations

import re

import pytest
from django.template import engines

from djust import LiveView
from djust.components.base import Component
from djust.decorators import event_handler
from djust.testing import LiveViewTestClient
from djust.validation import validate_handler_params

pytestmark = [pytest.mark.pr_tier]

OPTIONS = [{"value": "done", "label": "Done"}, {"value": "open", "label": "Open"}]


def _render(body: str) -> str:
    return (
        engines["django"]
        .from_string("{% load djust_components %}" + body)
        .render({"options": OPTIONS})
    )


class StrictStatusView(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root>x</div>"

    @event_handler(parameter_policy="strict")
    def set_status(self, value: str = ""):
        self.picked = value


class _Named(Component):
    def _render_custom(self) -> str:
        return ""


class _DeclaresName(Component):
    def __init__(self, name: str = "", **kwargs):
        super().__init__(name=name, **kwargs)

    def _render_custom(self) -> str:
        return ""


@pytest.mark.parametrize("tag", ['name="status"', ""])
def test_toggle_group_marks_identity_optional_on_every_button(tag):
    html = _render("{% toggle_group " + tag + " options=options value='open' event='set_status' %}")
    buttons = re.findall(r"<button\b[^>]*>", html)
    assert len(buttons) == 2
    assert all('data-djust-optional="name"' in b and "dj-value-name=" in b for b in buttons)


def test_event_attrs_marks_only_a_framework_stamped_identity():
    assert 'data-djust-optional="name"' in _Named(name="row-1").event_attrs("pick", value=1)
    caller = _Named(name="row-1").event_attrs("pick", name="x")
    assert 'dj-value-name="x"' in caller and "data-djust-optional" not in caller
    assert "data-djust-optional" not in _Named().event_attrs("pick", value=1)
    declares = _DeclaresName(name="field").event_attrs("pick", value=1)
    assert "dj-value-name" not in declares and "data-djust-optional" not in declares


def test_server_still_refuses_an_undeclared_name():
    view = StrictStatusView()
    result = validate_handler_params(
        view.set_status, {"name": "status", "value": "done"}, "set_status"
    )
    assert result["valid"] is False


def test_browser_payload_without_name_is_accepted():
    client = LiveViewTestClient(StrictStatusView)
    client.mount()
    assert client.send_event("set_status", value="done")["success"] is True
    refused = client.send_event("set_status", name="status", value="done")
    assert refused["success"] is False
